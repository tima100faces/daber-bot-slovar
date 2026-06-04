"""DABER Dictionary v2 — FastAPI backend on PostgreSQL daber_dict.

Two entity types:
  • verbs (4,607) — pealim source, binyan, root, infinitiv, enriched translations
  • words (11,612) — IRIS source, pos, gender, enriched translations

API endpoints:
  /api/search       — full-text + prefix on headword/translations
  /api/letter/{l}   — words by first Hebrew letter
  /api/pos/{p}      — words by part-of-speech slug
  /api/word/{h}     — single word detail (auto-detects verb vs word)
  /api/verb/{slug}  — single verb by pealim_slug
  /api/random       — random words
  /api/stats        — overall stats
"""
import io
import json
import os
import random
import re
import threading
import time as time_module
from pathlib import Path
from typing import Any, Optional

import psycopg2
import psycopg2.extras
import pyotp
import qrcode
from dotenv import load_dotenv
from fastapi import FastAPI, Query, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

load_dotenv()

PG = dict(
    host=os.environ.get("PGHOST", "127.0.0.1"),
    port=int(os.environ.get("PGPORT", "5434")),
    dbname=os.environ.get("PGDB", "daber_dict"),
    user=os.environ.get("PGUSER", "postgres"),
)
STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="DABER Dictionary v2 (PG)")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# Russian part-of-speech names + display label
POS_RU = {
    "noun": ("существительные", "noun"),
    "verb": ("глаголы", "verb"),
    "adj": ("прилагательные", "adj"),
    "adv": ("наречия", "adv"),
    "prep": ("предлоги", "prep"),
    "pron": ("местоимения", "pron"),
    "conj": ("союзы", "conj"),
    "num": ("числительные", "num"),
    "intj": ("междометия", "intj"),
    "particle": ("частицы", "particle"),
    "pref": ("приставки", "pref"),
    "suff": ("суффиксы", "suff"),
    "art": ("артикли", "art"),
    "unknown": ("без категории", "unknown"),
    "phrase": ("фразы", "phrase"),
}
# Wordtype raw → slug (IRIS sometimes has multi-word like "ед.ч., м.р., повел. накл.")
# We don't try to map wordtype → pos_slug in API; pos_slug is the source of truth.
BINYAN_RU = {
    "paal": "пааль",
    "piel": "пиэль",
    "hifil": "hифъиль",
    "hitpael": "hитпаэль",
    "nifal": "нифъаль",
    "pual": "пуаль",
    "hufal": "hуфъаль",
}


def pos_label(slug: str) -> str:
    """Return Russian display label for a pos_slug."""
    return POS_RU.get(slug, (slug or "—", slug))[0]


def binyan_label(b: str) -> str:
    return BINYAN_RU.get(b, b or "—")


def get_db():
    conn = psycopg2.connect(**PG)
    conn.set_session(readonly=True, autocommit=True)
    return conn


def get_db_writable():
    """Connection for write operations (feedback, etc.)."""
    conn = psycopg2.connect(**PG)
    conn.autocommit = True
    return conn


def _normalize_enriched(value):
    """translation_enriched may be a JSONB array, JSON string, or plain string.
    Return a list[str] always."""
    import json as _json
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x) for x in value if x]
    if isinstance(value, str):
        v = value.strip()
        if not v:
            return []
        if v.startswith("["):
            try:
                parsed = _json.loads(v)
                if isinstance(parsed, list):
                    return [str(x) for x in parsed if x]
                if isinstance(parsed, str):
                    return [parsed]
            except (ValueError, TypeError):
                pass
        return [v]
    return [str(value)]


def _clean_translit(t: str) -> str:
    """Strip [...] bracket annotations from transliteration display.
    Keeps the main translit, removes grammatical notes in brackets.
    Also converts capital-letter stress markers to combining accents.
    'эт [оти, отха]' → 'эт'
    'шнатAйм' → 'шната́йм'
    """
    if not t:
        return t
    t = re.sub(r'\s*\[[^]]*\]', '', t)
    t = re.sub(r'\s*\([^)]*\)', '', t)
    t = t.strip()
    return _render_translit(t)


# Capital letters → stress markers
_CAPITAL_TO_ACCENT = str.maketrans({
    'А': 'а́', 'Е': 'е́', 'Ё': 'ё́', 'И': 'и́', 'О': 'о́',
    'У': 'у́', 'Ы': 'ы́', 'Э': 'э́', 'Ю': 'ю́', 'Я': 'я́',
    'A': 'а́', 'E': 'е́', 'I': 'и́', 'O': 'о́', 'U': 'у́', 'Y': 'ы́',
})


def _render_translit(t: str) -> str:
    """Convert capital-letter stress markers to combining accents.
    'шнатAйм' → 'шната́йм'
    'лероцEт' → 'лероце́т'
    Leaves existing combining accents untouched.
    """
    if not t:
        return t
    # Only apply if the translit has NO combining accents already
    if '\u0301' in t or '\u0300' in t:
        return t
    return t.translate(_CAPITAL_TO_ACCENT)


def verb_to_dict(row, cur=None):
    """Row from verbs (with joined counts).
    
    Transliteration priority:
    1. infinitive_translit (Russian, from verb_forms WHERE tense='infinitive')
    2. Any verb_form transliteration (Russian, queried if cur provided)
    3. Empty string (never fall back to English pealim_slug)
    """
    translit = row.get("infinitive_translit") or ""
    if not translit and cur is not None:
        # Fall back to any form's transliteration
        cur.execute("""SELECT transliteration FROM verb_forms
                       WHERE verb_id = %s AND transliteration IS NOT NULL AND transliteration != ''
                       ORDER BY id LIMIT 1""", (row["id"],))
        fb = cur.fetchone()
        if fb:
            translit = fb["transliteration"] or ""
    return {
        "type": "verb",
        "id": row["id"],
        "headword": row["infinitive_he"],
        "headword_nikud": row["infinitive_he_nikud"] or "",
        "translit": translit,
        "root": row["root"],
        "binyan": row["binyan"],
        "binyan_label": binyan_label(row["binyan"]),
        "translation_ru": row["translation_ru"] or "",
        "translation_enriched": _normalize_enriched(row["translation_enriched"]),
        "pealim_slug": row["pealim_slug"],
        "passive_of": row["passive_of"],
        "notes": row["notes"] or "",
        "enriched": row["enriched_at"] is not None,
    }


def word_to_dict(row):
    """Row from words."""
    grammar = row["grammar_json"] or {}
    return {
        "type": "word",
        "id": row["id"],
        "headword": row["headword"],
        "headword_nikud": row["headword_nikud"] or "",
        "translit": _clean_translit(row.get("translit") or ""),
        "wordtype": row["wordtype"] or "",
        "pos_slug": row["pos_slug"],
        "pos_label": pos_label(row["pos_slug"]),
        "gender": row["gender"] or grammar.get("gender", ""),
        "number": grammar.get("number", ""),
        "grammar": grammar,
        "frequency": row["frequency"] or 0,
        "frequency_rank": row["frequency_rank"] or 0,
        "translation_enriched": _normalize_enriched(row["translation_enriched"]),
        "notes": row["notes"] or "",
    }


# ─── SEARCH ────────────────────────────────────────────────────────────────

@app.get("/api/search")
def search(q: str = Query(""), limit: int = Query(20, le=100), offset: int = Query(0, ge=0),
           pos: str | None = None):
    """Multi-stage search with strict priority ranking.

    Priority buckets (lower is better, shown in UI first):
      0 — exact headword match (Hebrew or pealim_slug stripped)
      1 — prefix on headword (Hebrew) or pealim_slug (translit)
      2 — substring on headword (Hebrew) or pealim_slug (translit)
      3 — transliteration of word forms/phrases (English letters)
      4 — translation/notes (last resort)
    """
    q = q.strip()
    if not q:
        return {"results": [], "total": 0, "query": q}
    if len(q) < 2:
        return {"results": [], "total": 0, "query": q}

    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    is_hebrew = bool(re.search(r"[\u0590-\u05FF]", q))

    if is_hebrew:
        like_exact = q
        like_prefix = f"{q}%"
        like_substr = f"%{q}%"
    else:
        like_exact = q.lower()
        like_prefix = f"{q.lower()}%"
        like_substr = f"%{q.lower()}%"

    results_all = []  # list of (priority, row_dict)

    if is_hebrew:
        # ── VERBS ──
        # 0. exact infinitive match
        cur.execute("""SELECT v.*,
                       (SELECT transliteration FROM verb_forms WHERE verb_id = v.id AND tense = 'infinitive' LIMIT 1) AS infinitive_translit
                       FROM verbs v WHERE v.infinitive_he = %s""", (like_exact,))
        for r in cur.fetchall():
            d = verb_to_dict(r, cur); d["example_count"] = r["example_count"]; d["synonym_count"] = r["synonym_count"]
            results_all.append((0, d))
        # 1. prefix
        cur.execute("""SELECT v.*,
                       (SELECT transliteration FROM verb_forms WHERE verb_id = v.id AND tense = 'infinitive' LIMIT 1) AS infinitive_translit
                       FROM verbs v WHERE v.infinitive_he LIKE %s AND v.infinitive_he != %s""",
                    (like_prefix, like_exact))
        for r in cur.fetchall():
            d = verb_to_dict(r, cur); d["example_count"] = r["example_count"]; d["synonym_count"] = r["synonym_count"]
            results_all.append((1, d))
        # NOTE: No substring search for Hebrew — roots are too short and would match
        # unrelated words (e.g., 'בית' matching 'ריבית', 'שביתה', 'חבית')

        # ── WORDS ──
        sql_base = """SELECT w.*,
                      (SELECT translit FROM word_forms WHERE word_id = w.id AND translit IS NOT NULL AND translit != '' ORDER BY CASE WHEN form_he = w.headword THEN 0 ELSE 1 END, id LIMIT 1) AS form_translit
                      FROM words w"""
        pos_clause = " AND w.pos_slug = %s" if pos else ""
        pos_args = (pos,) if pos else ()
        verb_filter = " AND w.pos_slug != 'verb'"

        # 0. exact
        cur.execute(sql_base + f" WHERE w.headword = %s{verb_filter}{pos_clause}", (like_exact, *pos_args))
        for r in cur.fetchall():
            d = word_to_dict(r); d["example_count"] = r["example_count"]; d["synonym_count"] = r["synonym_count"]; d["phrase_count"] = r["phrase_count"]
            if not d.get("translit") and r.get("form_translit"):
                d["translit"] = _clean_translit(r["form_translit"])
            results_all.append((0, d))
        # 1. prefix
        cur.execute(sql_base + f" WHERE w.headword LIKE %s AND w.headword != %s{verb_filter}{pos_clause}", (like_prefix, like_exact, *pos_args))
        for r in cur.fetchall():
            d = word_to_dict(r); d["example_count"] = r["example_count"]; d["synonym_count"] = r["synonym_count"]; d["phrase_count"] = r["phrase_count"]
            if not d.get("translit") and r.get("form_translit"):
                d["translit"] = _clean_translit(r["form_translit"])
            results_all.append((1, d))
        # NOTE: No substring search for Hebrew — too many false positives from shared root letters
    else:
        # Latin/Cyrillic input: search transliterations and translations
        q_re = re.compile(rf'(?<![а-яёa-z0-9]){re.escape(q.lower())}(?![а-яёa-z0-9])', re.UNICODE)

        # 0. exact pealim_slug match (translit primary for verbs)
        cur.execute("""SELECT v.*,
                       (SELECT transliteration FROM verb_forms WHERE verb_id = v.id AND tense = 'infinitive' LIMIT 1) AS infinitive_translit
                       FROM verbs v
                       WHERE LOWER(REGEXP_REPLACE(COALESCE(v.pealim_slug, ''), '^[0-9]+-?', '')) = %s""",
                    (like_exact,))
        for r in cur.fetchall():
            d = verb_to_dict(r, cur); d["example_count"] = r["example_count"]; d["synonym_count"] = r["synonym_count"]
            results_all.append((0, d))
        # 1. prefix on pealim_slug
        cur.execute("""SELECT v.*,
                       (SELECT transliteration FROM verb_forms WHERE verb_id = v.id AND tense = 'infinitive' LIMIT 1) AS infinitive_translit
                       FROM verbs v
                       WHERE LOWER(REGEXP_REPLACE(COALESCE(v.pealim_slug, ''), '^[0-9]+-?', '')) LIKE %s
                         AND LOWER(REGEXP_REPLACE(COALESCE(v.pealim_slug, ''), '^[0-9]+-?', '')) != %s""",
                    (like_prefix, like_exact))
        for r in cur.fetchall():
            d = verb_to_dict(r, cur); d["example_count"] = r["example_count"]; d["synonym_count"] = r["synonym_count"]
            results_all.append((1, d))
        # 2. substring on pealim_slug
        cur.execute("""SELECT v.*,
                       (SELECT transliteration FROM verb_forms WHERE verb_id = v.id AND tense = 'infinitive' LIMIT 1) AS infinitive_translit
                       FROM verbs v
                       WHERE LOWER(REGEXP_REPLACE(COALESCE(v.pealim_slug, ''), '^[0-9]+-?', '')) LIKE %s
                         AND LOWER(REGEXP_REPLACE(COALESCE(v.pealim_slug, ''), '^[0-9]+-?', '')) NOT LIKE %s""",
                    (like_substr, like_prefix))
        for r in cur.fetchall():
            d = verb_to_dict(r, cur); d["example_count"] = r["example_count"]; d["synonym_count"] = r["synonym_count"]
            results_all.append((2, d))

        # 3. substring on verb's Russian translation (translation_ru, translation_enriched)
        #    PG's POSIX word-boundary classes don't work for utf-8 cyrillic, so we
        #    fetch with LIKE and whole-word filter in Python.
        cur.execute(r"""SELECT v.*,
                       (SELECT transliteration FROM verb_forms WHERE verb_id = v.id AND tense = 'infinitive' LIMIT 1) AS infinitive_translit
                       FROM verbs v
                       WHERE LOWER(COALESCE(v.translation_ru, '')) LIKE %s
                          OR LOWER(COALESCE(v.translation_enriched::text, '')) LIKE %s""",
                    (like_substr, like_substr))
        for r in cur.fetchall():
            tr = (r.get("translation_ru") or "").lower()
            te = (r.get("translation_enriched") or "")
            if isinstance(te, list):
                te = " ".join(te).lower()
            else:
                te = str(te).lower()
            if q_re.search(tr) or q_re.search(te):
                d = verb_to_dict(r, cur); d["example_count"] = r["example_count"]; d["synonym_count"] = r["synonym_count"]
                results_all.append((3, d))

        # Words: search translit in word_forms / word_phrases
        sql_word = """SELECT DISTINCT w.*,
                      (SELECT translit FROM word_forms WHERE word_id = w.id AND translit IS NOT NULL AND translit != '' ORDER BY CASE WHEN form_he = w.headword THEN 0 ELSE 1 END, id LIMIT 1) AS form_translit
                      FROM words w"""
        pos_clause = " AND w.pos_slug = %s" if pos else ""
        pos_args = (pos,) if pos else ()

        # 4. translit match via word_forms (only one row per word via DISTINCT)
        cur.execute(f"""SELECT DISTINCT ON (w.id) w.*,
                        (SELECT translit FROM word_forms WHERE word_id = w.id AND translit IS NOT NULL AND translit != '' ORDER BY CASE WHEN form_he = w.headword THEN 0 ELSE 1 END, id LIMIT 1) AS form_translit
                        FROM words w
                        JOIN word_forms wf ON wf.word_id = w.id
                        WHERE w.pos_slug != 'verb'
                          AND LOWER(wf.translit) LIKE %s{pos_clause}
                        ORDER BY w.id""", (like_substr, *pos_args))
        for r in cur.fetchall():
            d = word_to_dict(r); d["example_count"] = r["example_count"]; d["synonym_count"] = r["synonym_count"]; d["phrase_count"] = r["phrase_count"]
            if not d.get("translit") and r.get("form_translit"):
                d["translit"] = _clean_translit(r["form_translit"])
            # Whole-word filter: skip if query is embedded in a longer word
            translit_val = (d.get("translit") or "").lower()
            if q_re.search(translit_val) or not any(c.isalpha() for c in q):
                results_all.append((4, d))

        # 5. word translation/notes match (substring fetch + whole-word filter in Python)
        cur.execute(f"""{sql_word}
                        WHERE w.pos_slug != 'verb'
                          AND (LOWER(COALESCE(w.translation_enriched::text, '')) LIKE %s
                            OR LOWER(COALESCE(w.notes, '')) LIKE %s){pos_clause}
                        ORDER BY w.frequency_rank ASC, w.frequency DESC NULLS LAST
                        LIMIT 200""",
                     (like_substr, like_substr, *pos_args))
        for r in cur.fetchall():
            te_raw = r.get("translation_enriched")
            te = " ".join(te_raw).lower() if isinstance(te_raw, list) else (str(te_raw or "").lower())
            notes = (r.get("notes") or "").lower()
            if q_re.search(te) or q_re.search(notes):
                d = word_to_dict(r); d["example_count"] = r["example_count"]; d["synonym_count"] = r["synonym_count"]; d["phrase_count"] = r["phrase_count"]
                if not d.get("translit") and r.get("form_translit"):
                    d["translit"] = _clean_translit(r["form_translit"])
                results_all.append((5, d))

    # Deduplicate (same id+type may appear in multiple priorities → keep lowest)
    seen = set()
    deduped = []
    for prio, d in sorted(results_all, key=lambda x: x[0]):
        key = (d["type"], d["id"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(d)

    # Second pass: deduplicate words with same headword AND identical translations
    # (IRIS data errors where same word is incorrectly duplicated across pos_slug)
    # Keeps the entry with most metadata; preserves legitimate homonyms with different translations
    headword_seen = {}  # headword → (translation_key, best_entry)
    deduped2 = []
    for d in deduped:
        if d["type"] != "word":
            deduped2.append(d)
            continue
        hw = d["headword"]
        tr_key = json.dumps(d.get("translation_enriched", []), sort_keys=True, ensure_ascii=False)
        meta_score = (d.get("example_count", 0) + d.get("synonym_count", 0) +
                      d.get("phrase_count", 0) + (1 if d.get("notes") else 0))
        # Prefer adj over noun on tie
        if d.get("pos_slug") == "adj":
            meta_score += 0.5

        prev = headword_seen.get(hw)
        if prev is not None and prev[0] == tr_key:
            # Same headword + identical translation → keep best
            if meta_score > prev[2]:
                headword_seen[hw] = (tr_key, d, meta_score)
        elif prev is None or prev[0] != tr_key:
            # New headword, or same headword but different translation → keep both
            if prev is not None:
                deduped2.append(prev[1])
            headword_seen[hw] = (tr_key, d, meta_score)

    # Flush last entry
    for hw, (tr_key, best, score) in headword_seen.items():
        if not any(d is best for d in deduped2):
            deduped2.append(best)

    total = len(deduped2)
    paginated = deduped2[offset:offset + limit]
    conn.close()
    return {"results": paginated, "total": total, "query": q, "pos": pos}


# ─── Enrichment helpers ─────────────────────────────────────────────────────

def _enrich_verb_detail(result: dict, verb_id: int, cur):
    """Add forms, examples, synonyms to a verb dict in-place."""
    cur.execute("""SELECT tense, person, gender, number, form_he, form_he_nikud, transliteration
                   FROM verb_forms WHERE verb_id = %s
                   ORDER BY tense, person, gender, number""", (verb_id,))
    result["verb_forms"] = [
        {"tense": r["tense"], "person": r["person"], "gender": r["gender"],
         "number": r["number"], "hebrew": r["form_he"], "nikud": r["form_he_nikud"],
         "translit": r["transliteration"]}
        for r in cur.fetchall()
    ]
    cur.execute("""SELECT DISTINCT ON (hebrew, translation) hebrew, translation FROM verb_examples
                   WHERE verb_id = %s ORDER BY hebrew, translation, id LIMIT 10""", (verb_id,))
    result["examples"] = [
        {"hebrew": r["hebrew"], "translation": r["translation"]} for r in cur.fetchall()
    ]
    cur.execute("""SELECT DISTINCT ON (hebrew, translation) hebrew, translation FROM verb_synonyms
                   WHERE verb_id = %s ORDER BY hebrew, translation, id LIMIT 10""", (verb_id,))
    result["synonyms"] = [
        {"hebrew": r["hebrew"], "translation": r["translation"] or ""} for r in cur.fetchall()
    ]


def _enrich_word_detail(d: dict, word_id: int, cur):
    """Add examples, synonyms, phrases, forms to a word dict in-place."""
    headword = d.get("headword", "")
    
    cur.execute("""SELECT DISTINCT ON (hebrew, translation) hebrew, translation FROM word_examples
                   WHERE word_id = %s ORDER BY hebrew, translation, id LIMIT 10""", (word_id,))
    d["examples"] = [{"hebrew": r["hebrew"], "translation": r["translation"]} for r in cur.fetchall()]
    cur.execute("""SELECT DISTINCT ON (hebrew, translation) hebrew, translation FROM word_synonyms
                   WHERE word_id = %s ORDER BY hebrew, translation, id LIMIT 10""", (word_id,))
    d["synonyms"] = [{"hebrew": r["hebrew"], "translation": r["translation"] or ""} for r in cur.fetchall()]
    cur.execute("""SELECT hebrew, nikud, translit, translation FROM word_phrases
                   WHERE word_id = %s ORDER BY id LIMIT 15""", (word_id,))
    phrases_raw = [{"hebrew": r["hebrew"], "nikud": r["nikud"], "translit": _clean_translit(r["translit"] or ""),
                    "translation": r["translation"]} for r in cur.fetchall()]
    # Deduplicate phrases by hebrew+translation, filter out bare headword
    seen_phrases = set()
    d["phrases"] = []
    for p in phrases_raw:
        if p["hebrew"] == headword:
            continue  # skip phrases that are just the word itself
        key = (p["hebrew"], p["translation"])
        if key not in seen_phrases:
            seen_phrases.add(key)
            d["phrases"].append(p)
    
    cur.execute("""SELECT DISTINCT ON (form_he, form_he_nikud, translit) form_he, form_he_nikud, translit, translation, grammar_json
                   FROM word_forms WHERE word_id = %s ORDER BY form_he, form_he_nikud, translit, id LIMIT 20""", (word_id,))
    forms_raw = [{"hebrew": r["form_he"], "nikud": r["form_he_nikud"], "translit": _clean_translit(r["translit"] or ""),
                  "translation": r["translation"], "grammar": r["grammar_json"]} for r in cur.fetchall()]
    # Filter out forms that are just the headword itself
    d["forms"] = [f for f in forms_raw if f["hebrew"] != headword]
    d["form_count"] = len(d["forms"])  # Updated count after filtering
    
    for tbl, col in [("word_examples", "example_count"), ("word_synonyms", "synonym_count"),
                    ("word_phrases", "phrase_count")]:
        cur.execute(f"SELECT COUNT(*) AS c FROM {tbl} WHERE word_id = %s", (word_id,))
        d[col] = cur.fetchone()["c"]
    d["phrase_count"] = len(d["phrases"])  # Updated count after dedup


# ─── WORD / VERB detail ────────────────────────────────────────────────────

@app.get("/api/word/{word}")
def get_word(word: str, id: Optional[int] = Query(None), type: Optional[str] = Query(None)):
    """Get a single word detail.

    Logic (in order):
      0. If `id` provided → return that specific entry (uses `type` to pick table)
      1. If exact match in `verbs` → return full verb detail with examples/synonyms
      2. Else, gather all `words` rows with headword=word
         - If exactly 1 row: return full word detail
         - If >1 rows:
            a. If at least one has translation_enriched, return only the enriched ones
            b. If all are empty AND `word` matches a `verbs.infinitive_he` form, return that verb
            c. Else: return variants with all metadata
    """
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # 0. Direct ID lookup
    if id is not None:
        if type == "verb":
            cur.execute("""SELECT v.*, (SELECT transliteration FROM verb_forms
                           WHERE verb_id = v.id AND tense = 'infinitive' LIMIT 1) AS infinitive_translit
                           FROM verbs v WHERE v.id = %s""", (id,))
            row = cur.fetchone()
            if row:
                result = verb_to_dict(row, cur)
                _enrich_verb_detail(result, row["id"], cur)
                conn.close()
                return result
        else:
            # type == 'word' or no type specified
            cur.execute("""SELECT w.*,
                       (SELECT MIN(translit) FROM word_forms
                        WHERE word_id = w.id AND translit IS NOT NULL AND translit != ''
                          AND form_he = w.headword) AS form_translit
                       FROM words w WHERE w.id = %s""", (id,))
            w = cur.fetchone()
            if w:
                d = word_to_dict(w)
                if not d["translit"] and w.get("form_translit"):
                    d["translit"] = _clean_translit(w["form_translit"])
                _enrich_word_detail(d, w["id"], cur)
                conn.close()
                return d
        # ID not found in either table
        conn.close()
        return {"type": "word_empty", "headword": word, "variants": [],
                "message": "Запись не найдена", "other_count": 0}

    # 1. Verb? (exact infinitive match)
    cur.execute("SELECT *, (SELECT transliteration FROM verb_forms WHERE verb_id = verbs.id AND tense = 'infinitive' LIMIT 1) AS infinitive_translit FROM verbs WHERE infinitive_he = %s", (word,))
    verb_row = cur.fetchone()
    if verb_row:
        result = verb_to_dict(verb_row, cur)
        # Verb forms (conjugations)
        cur.execute("""SELECT tense, person, gender, number, form_he, form_he_nikud, transliteration
                       FROM verb_forms WHERE verb_id = %s
                       ORDER BY tense, person, gender, number""", (verb_row["id"],))
        result["verb_forms"] = [
            {"tense": r["tense"], "person": r["person"], "gender": r["gender"],
             "number": r["number"], "hebrew": r["form_he"], "nikud": r["form_he_nikud"],
             "translit": r["transliteration"]}
            for r in cur.fetchall()
        ]
        # Examples
        cur.execute("""SELECT hebrew, translation FROM verb_examples
                       WHERE verb_id = %s ORDER BY id LIMIT 10""", (verb_row["id"],))
        result["examples"] = [
            {"hebrew": r["hebrew"], "translation": r["translation"]} for r in cur.fetchall()
        ]
        # Synonyms
        cur.execute("""SELECT hebrew, translation FROM verb_synonyms
                       WHERE verb_id = %s ORDER BY id LIMIT 10""", (verb_row["id"],))
        result["synonyms"] = [
            {"hebrew": r["hebrew"], "translation": r["translation"] or ""} for r in cur.fetchall()
        ]
        # Same-root words
        if verb_row["root"]:
            cur.execute("""SELECT infinitive_he, infinitive_he_nikud, binyan, translation_ru, pealim_slug
                           FROM verbs WHERE root = %s AND id != %s
                           ORDER BY binyan LIMIT 15""",
                       (verb_row["root"], verb_row["id"]))
            result["same_root_verbs"] = [
                {"headword": r["infinitive_he"], "nikud": r["infinitive_he_nikud"] or "",
                 "binyan": r["binyan"], "binyan_label": binyan_label(r["binyan"]),
                 "translation_ru": r["translation_ru"] or "", "slug": r["pealim_slug"]}
                for r in cur.fetchall()
            ]
            # Also find words sharing same root letters (without dashes)
            root_letters = verb_row["root"].replace("-", "")
            cur.execute("""SELECT headword, headword_nikud, pos_slug, translit,
                                  translation_enriched
                           FROM words WHERE headword LIKE %s
                           ORDER BY frequency_rank LIMIT 10""",
                       (f"%{root_letters}%",))
            result["same_root_words"] = [
                {"headword": r["headword"], "nikud": r["headword_nikud"] or "",
                 "pos_slug": r["pos_slug"], "pos_label": pos_label(r["pos_slug"]),
                 "translit": _clean_translit(r["translit"] or ""),
                 "translation": (_normalize_enriched(r["translation_enriched"]) or [""])[0][:80]}
                for r in cur.fetchall()
            ]
        conn.close()
        return result

    # 2. Look for word headword matches
    cur.execute("""SELECT w.*,
                   (SELECT translit FROM word_forms
                    WHERE word_id = w.id AND translit IS NOT NULL AND translit != ''
                    ORDER BY CASE WHEN form_he = w.headword THEN 0 ELSE 1 END, id LIMIT 1) AS form_translit
                   FROM words w
                   WHERE headword = %s
                   ORDER BY (translation_enriched IS NULL), frequency_rank ASC NULLS LAST, frequency DESC""",
                (word,))
    word_rows = cur.fetchall()

    # 2a. If word has translations, return it immediately (don't redirect to verb-form)
    enriched_rows = [w for w in word_rows if w.get("translation_enriched")]
    if enriched_rows:
        if len(enriched_rows) == 1:
            w = enriched_rows[0]
            d = word_to_dict(w)
            if not d["translit"] and w.get("form_translit"):
                d["translit"] = _clean_translit(w["form_translit"])
            _enrich_word_detail(d, w["id"], cur)
            # Also check if this word is a verb form (homograph: same spelling, different POS)
            cur.execute("""SELECT DISTINCT ON (v.id) v.infinitive_he, v.translation_ru, v.binyan,
                                  vf.tense, vf.person, vf.gender, vf.number
                           FROM verb_forms vf
                           JOIN verbs v ON v.id = vf.verb_id
                           WHERE vf.form_he = %s
                           ORDER BY v.id, vf.tense
                           LIMIT 1""", (word,))
            also_verb = cur.fetchone()
            if also_verb:
                d["also_verb_form"] = {
                    "infinitive": also_verb["infinitive_he"],
                    "translation": also_verb["translation_ru"] or "",
                    "binyan": also_verb["binyan"],
                    "tense": also_verb["tense"],
                    "person": also_verb["person"],
                    "gender": also_verb["gender"],
                    "number": also_verb["number"],
                }
            conn.close()
            return d
        else:
            # Multiple enriched variants — show them as variant list
            variants = []
            for w in enriched_rows:
                v = word_to_dict(w)
                if not v["translit"] and w.get("form_translit"):
                    v["translit"] = _clean_translit(w["form_translit"])
                _enrich_word_detail(v, w["id"], cur)
                variants.append(v)
            other_count = len(word_rows) - len(enriched_rows)
            conn.close()
            return {"type": "word", "headword": word, "variants": variants,
                    "other_count": other_count, "message": f"Найдено вариантов: {len(variants)}"}

    # 2b. Word exists but NO translations — check if it's a verb form
    # 2c. No word at all — check if it's a verb form
    cur.execute("""SELECT v.*,
                       (SELECT transliteration FROM verb_forms WHERE verb_id = v.id AND tense = 'infinitive' LIMIT 1) AS infinitive_translit, vf.tense AS matched_tense, vf.person AS matched_person,
                          vf.gender AS matched_gender, vf.number AS matched_number,
                          vf.form_he_nikud AS matched_nikud, vf.transliteration AS matched_translit
                   FROM verbs v
                   JOIN verb_forms vf ON vf.verb_id = v.id
                   WHERE vf.form_he = %s
                   ORDER BY CASE vf.tense WHEN 'present' THEN 0 WHEN 'past' THEN 1 WHEN 'future' THEN 2 ELSE 3 END, v.id
                   LIMIT 1""", (word,))
    verb_by_form = cur.fetchone()
    if verb_by_form:
        # Found matching verb form — return compact word-form card (NOT full verb page)
        form_context = {
            "tense": verb_by_form["matched_tense"],
            "person": verb_by_form["matched_person"],
            "gender": verb_by_form["matched_gender"],
            "number": verb_by_form["matched_number"],
            "nikud": verb_by_form["matched_nikud"] or word,
            "translit": verb_by_form["matched_translit"] or "",
        }
        result = {
            "type": "word_form",
            "headword": word,
            "headword_nikud": verb_by_form["matched_nikud"] or "",
            "translit": verb_by_form["matched_translit"] or "",
            "matched_by_form": True,
            "form_context": form_context,
            "verb_headword": verb_by_form["infinitive_he"],
            "verb_nikud": verb_by_form["infinitive_he_nikud"] or "",
            "verb_translation_ru": verb_by_form["translation_ru"] or "",
            "verb_translation_enriched": _normalize_enriched(verb_by_form["translation_enriched"]),
            "verb_binyan": verb_by_form["binyan"],
            "verb_binyan_label": binyan_label(verb_by_form["binyan"]),
            "verb_root": verb_by_form["root"],
            "verb_slug": verb_by_form["pealim_slug"],
            "pos_slug": "verb_form",
            "pos_label": pos_label("verb"),
        }
        # Only the past-tense paradigm (8 forms) for context, not all 200+
        cur.execute("""SELECT tense, person, gender, number, form_he, form_he_nikud, transliteration
                       FROM verb_forms WHERE verb_id = %s AND tense = %s
                       ORDER BY person, gender, number""",
                   (verb_by_form["id"], form_context["tense"]))
        result["paradigm_forms"] = [
            {"tense": r["tense"], "person": r["person"], "gender": r["gender"],
             "number": r["number"], "hebrew": r["form_he"], "nikud": r["form_he_nikud"],
             "translit": r["transliteration"]}
            for r in cur.fetchall()
        ]
        # Examples from the parent verb
        cur.execute("""SELECT hebrew, translation FROM verb_examples
                       WHERE verb_id = %s ORDER BY id LIMIT 5""", (verb_by_form["id"],))
        result["examples"] = [
            {"hebrew": r["hebrew"], "translation": r["translation"]} for r in cur.fetchall()
        ]
        # Synonyms from the parent verb
        cur.execute("""SELECT hebrew, translation FROM verb_synonyms
                       WHERE verb_id = %s ORDER BY id LIMIT 5""", (verb_by_form["id"],))
        result["synonyms"] = [
            {"hebrew": r["hebrew"], "translation": r["translation"] or ""} for r in cur.fetchall()
        ]
        # Also check if this spelling exists as a regular word (homograph)
        cur.execute("""SELECT headword, pos_slug, translation_enriched
                       FROM words WHERE headword = %s AND translation_enriched IS NOT NULL
                       LIMIT 1""", (word,))
        also_word = cur.fetchone()
        if also_word:
            result["also_word"] = {
                "headword": also_word["headword"],
                "pos_slug": also_word["pos_slug"],
                "pos_label": pos_label(also_word["pos_slug"]),
                "translation": (_normalize_enriched(also_word["translation_enriched"]) or [""])[0],
            }
        conn.close()
        return result

    if not word_rows:
        # 2a. If still nothing, but the word looks like a verb form (not starting with ל-),
        #     try to find the matching verb-инфинитив via pealim's verb forms table on verbs
        #     (pealim data isn't here yet, so just 404)
        conn.close()
        raise HTTPException(404, f"Word '{word}' not found")

    # Helper: enrich single word with all related data
    def _enrich_word(w):
        d = word_to_dict(w)
        # translit fallback from forms
        if not d["translit"] and w.get("form_translit"):
            d["translit"] = _clean_translit(w["form_translit"])
        wid = w["id"]
        cur.execute("""SELECT hebrew, translation FROM word_examples
                       WHERE word_id = %s ORDER BY id LIMIT 10""", (wid,))
        d["examples"] = [
            {"hebrew": r["hebrew"], "translation": r["translation"]} for r in cur.fetchall()
        ]
        cur.execute("""SELECT hebrew, translation FROM word_synonyms
                       WHERE word_id = %s ORDER BY id LIMIT 10""", (wid,))
        d["synonyms"] = [
            {"hebrew": r["hebrew"], "translation": r["translation"] or ""} for r in cur.fetchall()
        ]
        cur.execute("""SELECT hebrew, nikud, translit, translation FROM word_phrases
                       WHERE word_id = %s ORDER BY id LIMIT 15""", (wid,))
        d["phrases"] = [
            {"hebrew": r["hebrew"], "nikud": r["nikud"], "translit": _clean_translit(r["translit"] or ""),
             "translation": r["translation"]}
            for r in cur.fetchall()
        ]
        cur.execute("""SELECT form_he, form_he_nikud, translit, translation, grammar_json
                       FROM word_forms WHERE word_id = %s ORDER BY id LIMIT 20""", (wid,))
        d["forms"] = [
            {"hebrew": r["form_he"], "nikud": r["form_he_nikud"], "translit": _clean_translit(r["translit"] or ""),
             "translation": r["translation"], "grammar": r["grammar_json"]}
            for r in cur.fetchall()
        ]
        cur.execute("""SELECT COUNT(*) AS c FROM word_examples WHERE word_id = %s""", (wid,))
        d["example_count"] = cur.fetchone()["c"]
        cur.execute("""SELECT COUNT(*) AS c FROM word_synonyms WHERE word_id = %s""", (wid,))
        d["synonym_count"] = cur.fetchone()["c"]
        cur.execute("""SELECT COUNT(*) AS c FROM word_phrases WHERE word_id = %s""", (wid,))
        d["phrase_count"] = cur.fetchone()["c"]
        cur.execute("""SELECT COUNT(*) AS c FROM word_forms WHERE word_id = %s""", (wid,))
        d["form_count"] = cur.fetchone()["c"]
        return d

    # Single variant — return enriched
    if len(word_rows) == 1:
        d = _enrich_word(word_rows[0])
        conn.close()
        return d

    # Multiple variants — if at least one is enriched, show only those (plus a count of others)
    enriched_rows = [w for w in word_rows if w.get("translation_enriched")]
    if enriched_rows:
        if len(enriched_rows) == 1:
            d = _enrich_word(enriched_rows[0])
            conn.close()
            return d
        # Multiple enriched — return all as a single object with `variants`
        variants = [_enrich_word(w) for w in enriched_rows]
        conn.close()
        return {"type": "word_multi", "headword": word, "variants": variants,
                "other_count": len(word_rows) - len(enriched_rows)}

    # All variants empty (the "Найдено вариантов: 2" case).
    # First, try verb-form lookup: is this a conjugated form of a verb?
    cur.execute("""SELECT v.*, (SELECT transliteration FROM verb_forms WHERE verb_id = v.id AND tense = 'infinitive' LIMIT 1) AS infinitive_translit FROM verbs v
                   JOIN verb_forms vf ON vf.verb_id = v.id
                   WHERE vf.form_he = %s
                   ORDER BY CASE vf.tense WHEN 'present' THEN 0 WHEN 'past' THEN 1 WHEN 'future' THEN 2 ELSE 3 END, v.id
                   LIMIT 1""", (word,))
    verb_by_form = cur.fetchone()
    if verb_by_form:
        # Redirect to verb detail logic
        result = verb_to_dict(verb_by_form, cur)
        cur.execute("""SELECT tense, person, gender, number, form_he, form_he_nikud, transliteration
                       FROM verb_forms WHERE verb_id = %s
                       ORDER BY tense, person, gender, number""", (verb_by_form["id"],))
        result["verb_forms"] = [
            {"tense": r["tense"], "person": r["person"], "gender": r["gender"],
             "number": r["number"], "hebrew": r["form_he"], "nikud": r["form_he_nikud"],
             "translit": r["transliteration"]}
            for r in cur.fetchall()
        ]
        cur.execute("""SELECT hebrew, translation FROM verb_examples
                       WHERE verb_id = %s ORDER BY id LIMIT 10""", (verb_by_form["id"],))
        result["examples"] = [
            {"hebrew": r["hebrew"], "translation": r["translation"]} for r in cur.fetchall()
        ]
        cur.execute("""SELECT hebrew, translation FROM verb_synonyms
                       WHERE verb_id = %s ORDER BY id LIMIT 10""", (verb_by_form["id"],))
        result["synonyms"] = [
            {"hebrew": r["hebrew"], "translation": r["translation"] or ""} for r in cur.fetchall()
        ]
        if verb_by_form["root"]:
            cur.execute("""SELECT infinitive_he, infinitive_he_nikud, binyan, translation_ru, pealim_slug
                           FROM verbs WHERE root = %s AND id != %s
                           ORDER BY binyan LIMIT 15""",
                       (verb_by_form["root"], verb_by_form["id"]))
            result["same_root_verbs"] = [
                {"headword": r["infinitive_he"], "nikud": r["infinitive_he_nikud"] or "",
                 "binyan": r["binyan"], "binyan_label": binyan_label(r["binyan"]),
                 "translation_ru": r["translation_ru"] or "", "slug": r["pealim_slug"]}
                for r in cur.fetchall()
            ]
            root_letters = verb_by_form["root"].replace("-", "")
            cur.execute("""SELECT headword, headword_nikud, pos_slug, translit,
                                  translation_enriched
                           FROM words WHERE headword LIKE %s
                           ORDER BY frequency_rank LIMIT 10""",
                       (f"%{root_letters}%",))
            result["same_root_words"] = [
                {"headword": r["headword"], "nikud": r["headword_nikud"] or "",
                 "pos_slug": r["pos_slug"], "pos_label": pos_label(r["pos_slug"]),
                 "translit": _clean_translit(r["translit"] or ""),
                 "translation": (_normalize_enriched(r["translation_enriched"]) or [""])[0][:80]}
                for r in cur.fetchall()
            ]
        result["matched_by_form"] = True  # flag for UI: "это форма глагола X"
        conn.close()
        return result

    # No verb form match — return all word variants with empty translations + form count
    variants = []
    for w in word_rows:
        d = word_to_dict(w)
        if not d["translit"] and w.get("form_translit"):
            d["translit"] = _clean_translit(w["form_translit"])
        wid = w["id"]
        cur.execute("SELECT COUNT(*) AS c FROM word_forms WHERE word_id = %s", (wid,))
        d["form_count"] = cur.fetchone()["c"]
        cur.execute("SELECT COUNT(*) AS c FROM word_phrases WHERE word_id = %s", (wid,))
        d["phrase_count"] = cur.fetchone()["c"]
        cur.execute("SELECT COUNT(*) AS c FROM word_examples WHERE word_id = %s", (wid,))
        d["example_count"] = cur.fetchone()["c"]
        # Pull first 5 forms with translit so user can see what this word really is
        cur.execute("""SELECT form_he, form_he_nikud, translit FROM word_forms
                       WHERE word_id = %s AND translit IS NOT NULL AND translit != ''
                       ORDER BY id LIMIT 5""", (wid,))
        d["forms"] = [{"hebrew": r["form_he"], "nikud": r["form_he_nikud"],
                       "translit": r["translit"]} for r in cur.fetchall()]
        variants.append(d)
    conn.close()
    return {"type": "word_empty", "headword": word, "variants": variants,
            "message": "У этого слова пока нет перевода. Возможно это форма глагола или редкое слово."}


# ─── Letter / POS browse ──────────────────────────────────────────────────

@app.get("/api/letter/{letter}")
def by_letter(letter: str, limit: int = Query(50, le=200), offset: int = Query(0, ge=0)):
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    like = f"{letter}%"
    cur.execute("SELECT COUNT(*) AS c FROM words WHERE headword LIKE %s", (like,))
    total = cur.fetchone()["c"]
    cur.execute("""SELECT * FROM words WHERE headword LIKE %s
                   ORDER BY frequency_rank ASC, frequency DESC LIMIT %s OFFSET %s""",
                (like, limit, offset))
    results = [word_to_dict(r) for r in cur.fetchall()]
    conn.close()
    return {"results": results, "total": total, "letter": letter}


@app.get("/api/pos/{pos}")
def by_pos(pos: str, limit: int = Query(50, le=200), offset: int = Query(0, ge=0)):
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    if pos == "verb":
        cur.execute("SELECT COUNT(*) AS c FROM verbs")
        total = cur.fetchone()["c"]
        cur.execute("""SELECT v.*, (SELECT transliteration FROM verb_forms
                       WHERE verb_id = v.id AND tense = 'infinitive' LIMIT 1) AS infinitive_translit
                       FROM verbs v ORDER BY v.infinitive_he LIMIT %s OFFSET %s""", (limit, offset))
        results = [verb_to_dict(r, cur) for r in cur.fetchall()]
        conn.close()
        return {"results": results, "total": total, "pos": pos}

    cur.execute("SELECT COUNT(*) AS c FROM words WHERE pos_slug = %s", (pos,))
    total = cur.fetchone()["c"]
    cur.execute("""SELECT * FROM words WHERE pos_slug = %s
                   ORDER BY frequency_rank ASC, frequency DESC LIMIT %s OFFSET %s""",
                (pos, limit, offset))
    results = [word_to_dict(r) for r in cur.fetchall()]
    conn.close()
    return {"results": results, "total": total, "pos": pos}


@app.get("/api/root/{root}")
def by_root(root: str):
    """Return all verbs and words sharing a Hebrew root (e.g. כ-ת-ב or כתב)."""
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Normalise: accept both כתב and כ-ת-ב
    root_clean = root.replace("-", "")
    root_dashed = "-".join(root_clean) if len(root_clean) >= 2 else root

    # Verbs by exact root match
    cur.execute("""SELECT *, (SELECT transliteration FROM verb_forms WHERE verb_id = verbs.id AND tense = 'infinitive' LIMIT 1) AS infinitive_translit FROM verbs WHERE root = %s
                   ORDER BY binyan, infinitive_he""", (root_dashed,))
    verbs = []
    for r in cur.fetchall():
        d = verb_to_dict(r, cur)
        cur.execute("SELECT COUNT(*) AS c FROM verb_forms WHERE verb_id = %s", (r["id"],))
        d["form_count"] = cur.fetchone()["c"]
        verbs.append(d)

    # Words containing root letters (loose match)
    cur.execute("""SELECT * FROM words WHERE headword LIKE %s
                   ORDER BY frequency_rank LIMIT 30""",
               (f"%{root_clean}%",))
    words = [word_to_dict(r) for r in cur.fetchall()]

    conn.close()
    return {
        "root": root_dashed,
        "root_clean": root_clean,
        "verbs": verbs,
        "words": words,
    }


# ─── Random + Stats ───────────────────────────────────────────────────────

@app.get("/api/random")
def random_word(n: int = Query(1, le=10)):
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""SELECT w.* FROM words w
                   WHERE translation_enriched IS NOT NULL
                     AND translation_enriched::text != 'null'
                     AND translation_enriched::text != '[]'
                     AND translation_enriched::text != '""'
                   ORDER BY random() LIMIT %s""", (n,))
    results = [word_to_dict(r) for r in cur.fetchall()]
    conn.close()
    if not results:
        return {"results": []}
    return {"results": results}


# Stats cache — 1 hour TTL
_stats_cache = {"data": None, "ts": 0}

@app.get("/api/stats")
def stats():
    import time
    now = time.time()
    if _stats_cache["data"] and (now - _stats_cache["ts"]) < 3600:
        return _stats_cache["data"]

    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT COUNT(*) AS c FROM words")
    total_words = cur.fetchone()["c"]
    cur.execute("SELECT COUNT(*) AS c FROM verbs")
    total_verbs = cur.fetchone()["c"]

    cur.execute("""SELECT pos_slug, COUNT(*) AS cnt FROM words
                   WHERE pos_slug != '' GROUP BY pos_slug ORDER BY cnt DESC""")
    pos_dist = {r["pos_slug"]: r["cnt"] for r in cur.fetchall()}

    cur.execute("""SELECT binyan, COUNT(*) AS cnt FROM verbs
                   GROUP BY binyan ORDER BY cnt DESC""")
    binyan_dist = {r["binyan"]: r["cnt"] for r in cur.fetchall()}

    cur.execute("""SELECT COUNT(*) AS c FROM words
                   WHERE translation_enriched IS NOT NULL
                     AND translation_enriched::text != 'null'
                     AND translation_enriched::text != '[]'
                     AND translation_enriched::text != '\"\"'""")
    enriched_words = cur.fetchone()["c"]
    cur.execute("SELECT COUNT(*) AS c FROM verbs WHERE enriched_at IS NOT NULL")
    enriched_verbs = cur.fetchone()["c"]

    # Letters in words
    cur.execute("""SELECT DISTINCT SUBSTR(headword, 1, 1) AS letter FROM words
                   WHERE headword ~ '^[\u0590-\u05FF]' ORDER BY letter""")
    letters = [r["letter"] for r in cur.fetchall()]

    conn.close()
    data = {
        "total_words": total_words,
        "total_verbs": total_verbs,
        "enriched_words": enriched_words,
        "enriched_verbs": enriched_verbs,
        "enriched_words_pct": round(enriched_words / total_words * 100, 1) if total_words else 0,
        "pos_distribution": pos_dist,
        "binyan_distribution": binyan_dist,
        "letters": letters,
    }
    _stats_cache["data"] = data
    _stats_cache["ts"] = now
    return data


# ─── Feedback ──────────────────────────────────────────────────────────────

from pydantic import BaseModel

class FeedbackIn(BaseModel):
    word_id: int
    field_name: str
    selected_text: str | None = None
    comment: str

class ContactIn(BaseModel):
    subject: str = "другое"
    contact: str | None = None
    message: str

@app.post("/api/contact")
def submit_contact(c: ContactIn):
    if not c.message.strip():
        raise HTTPException(400, "Сообщение не может быть пустым")
    if len(c.message) > 5000:
        raise HTTPException(400, "Слишком длинное сообщение (макс 5000 символов)")
    conn = get_db_writable()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO contact_messages (subject, contact, message) VALUES (%s, %s, %s) RETURNING id",
        (c.subject, c.contact, c.message.strip()),
    )
    new_id = cur.fetchone()[0]
    conn.close()
    return {"ok": True, "id": new_id}

@app.post("/api/feedback")
def submit_feedback(fb: FeedbackIn):
    conn = get_db_writable()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO user_feedback (word_id, field_name, selected_text, comment) VALUES (%s, %s, %s, %s) RETURNING id",
        (fb.word_id, fb.field_name, fb.selected_text, fb.comment),
    )
    new_id = cur.fetchone()[0]
    conn.close()
    return {"ok": True, "id": new_id}

@app.get("/api/feedback")
def list_feedback(resolved: Optional[bool] = Query(None)):
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    if resolved is None:
        cur.execute("SELECT * FROM user_feedback ORDER BY created_at DESC LIMIT 100")
    else:
        cur.execute("SELECT * FROM user_feedback WHERE resolved = %s ORDER BY created_at DESC LIMIT 100", (resolved,))
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─── Admin (TOTP-protected) ────────────────────────────────────────────────

ADMIN_SECRET = os.environ.get("ADMIN_TOTP_SECRET", "")
if not ADMIN_SECRET:
    import warnings
    warnings.warn("ADMIN_TOTP_SECRET not set — admin panel will not work")
    ADMIN_SECRET = "fallback-dev-only"

totp = pyotp.TOTP(ADMIN_SECRET)
session_signer = URLSafeTimedSerializer(ADMIN_SECRET, salt="daber-admin-session")
SESSION_MAX_AGE = 8 * 3600  # 8 hours


def admin_required(request: Request) -> None:
    """Raise 401 if session cookie is missing, expired, or tampered."""
    session = request.cookies.get("daber_admin_session")
    if not session:
        raise HTTPException(401, "No admin session")
    try:
        session_signer.loads(session, max_age=SESSION_MAX_AGE)
    except SignatureExpired:
        raise HTTPException(401, "Session expired")
    except BadSignature:
        raise HTTPException(401, "Invalid session")


# ── Auth ──


class LoginIn(BaseModel):
    code: str


@app.post("/admin/api/login")
def admin_login(data: LoginIn):
    """Verify TOTP code, return signed session cookie."""
    if not totp.verify(data.code, valid_window=1):
        raise HTTPException(401, "Invalid code")
    session = session_signer.dumps("admin")
    resp = JSONResponse({"ok": True})
    resp.set_cookie(
        "daber_admin_session", session,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="strict",
        secure=False,  # False for dev/localhost; nginx adds HTTPS in prod
    )
    return resp


@app.get("/admin/api/check")
def admin_check(request: Request):
    admin_required(request)
    return {"ok": True}


@app.get("/api/admin/session")
def admin_session_check(request: Request):
    """Public-safe check: returns {admin: true/false} without raising 401."""
    try:
        admin_required(request)
        return {"admin": True}
    except HTTPException:
        return {"admin": False}


@app.delete("/api/admin/word/{word_id}")
def admin_delete_word(word_id: int, request: Request, type: str = Query("word")):
    """Delete a word or verb and all associated records. Admin only."""
    admin_required(request)
    conn = get_db_writable()
    cur = conn.cursor()

    if type == "verb":
        cur.execute("DELETE FROM verb_examples WHERE verb_id = %s", (word_id,))
        cur.execute("DELETE FROM verb_synonyms WHERE verb_id = %s", (word_id,))
        cur.execute("DELETE FROM verb_forms WHERE verb_id = %s", (word_id,))
        cur.execute("DELETE FROM verbs WHERE id = %s", (word_id,))
    else:
        cur.execute("DELETE FROM word_examples WHERE word_id = %s", (word_id,))
        cur.execute("DELETE FROM word_synonyms WHERE word_id = %s", (word_id,))
        cur.execute("DELETE FROM word_forms WHERE word_id = %s", (word_id,))
        cur.execute("DELETE FROM word_phrases WHERE word_id = %s", (word_id,))
        # Also delete any feedback for this word
        cur.execute("DELETE FROM user_feedback WHERE word_id = %s", (word_id,))
        cur.execute("DELETE FROM words WHERE id = %s", (word_id,))

    deleted = cur.rowcount
    conn.commit()
    conn.close()

    if deleted == 0:
        raise HTTPException(404, f"{type} with id={word_id} not found")
    return {"ok": True, "deleted": type, "id": word_id}


# ── Admin: Word editor ──

@app.get("/admin/api/words/search")
def admin_words_search(
    request: Request,
    q: str = Query(""),
    mode: str = Query("all"),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
):
    """Search words for admin editor. mode=all|suspicious."""
    admin_required(request)
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    if mode == "suspicious":
        # Words with known issues: empty translit, phrase-translit for single-word headwords,
        # empty nikud, or unresolved feedback
        cur.execute(
            """SELECT w.*, 
                      (SELECT COUNT(*) FROM user_feedback WHERE word_id = w.id AND resolved = false) AS open_feedback
               FROM words w
               WHERE (w.translit IS NULL OR w.translit = '')
                  OR (w.translit LIKE '%% %%' AND w.headword NOT LIKE '%% %%')
                  OR (w.headword_nikud IS NULL OR w.headword_nikud = '' OR w.headword_nikud = w.headword)
                  OR w.id IN (SELECT word_id FROM user_feedback WHERE resolved = false)
               ORDER BY 
                  CASE WHEN w.id IN (SELECT word_id FROM user_feedback WHERE resolved = false) THEN 0 ELSE 1 END,
                  CASE WHEN (w.translit IS NULL OR w.translit = '') THEN 0 ELSE 1 END,
                  w.frequency_rank ASC NULLS LAST
               LIMIT %s OFFSET %s""",
            (limit, offset),
        )
    else:
        if q.strip():
            like_q = f"%{q.strip()}%"
            cur.execute(
                """SELECT w.*, 
                          (SELECT COUNT(*) FROM user_feedback WHERE word_id = w.id AND resolved = false) AS open_feedback
                   FROM words w
                   WHERE w.headword ILIKE %s OR w.translit ILIKE %s OR w.headword_nikud ILIKE %s
                   ORDER BY w.frequency_rank ASC NULLS LAST
                   LIMIT %s OFFSET %s""",
                (like_q, like_q, like_q, limit, offset),
            )
        else:
            cur.execute(
                """SELECT w.*, 
                          (SELECT COUNT(*) FROM user_feedback WHERE word_id = w.id AND resolved = false) AS open_feedback
                   FROM words w
                   ORDER BY w.frequency_rank ASC NULLS LAST
                   LIMIT %s OFFSET %s""",
                (limit, offset),
            )

    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.put("/admin/api/word/{word_id}")
async def admin_update_word(word_id: int, request: Request):
    """Update word fields. Admin only."""
    admin_required(request)
    body = await request.json()

    allowed = {"headword", "headword_nikud", "translit", "wordtype", "pos_slug",
               "gender", "number", "notes"}
    updates = {k: v for k, v in body.items() if k in allowed and v is not None}

    # Map translation_ru to translation_enriched
    translation_ru = body.get("translation_ru")
    if translation_ru is not None and translation_ru.strip():
        updates["translation_enriched"] = json.dumps([translation_ru.strip()])

    if not updates:
        raise HTTPException(400, "No valid fields to update")

    conn = get_db_writable()
    cur = conn.cursor()

    set_clause = ", ".join(f"{k} = %s" for k in updates)
    values = list(updates.values()) + [word_id]

    cur.execute(f"UPDATE words SET {set_clause}, updated_at = NOW() WHERE id = %s", values)
    if cur.rowcount == 0:
        conn.close()
        raise HTTPException(404, f"Word id={word_id} not found")

    conn.commit()
    conn.close()
    return {"ok": True, "id": word_id, "updated": list(updates.keys())}


# ── Pending words ──

@app.get("/admin/api/pending")
def admin_pending(request: Request, status: str = "pending", id: int = None):
    admin_required(request)
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    if id:
        cur.execute(
            """SELECT p.*,
                      w.id IS NOT NULL AS already_exists
               FROM pending_words p
               LEFT JOIN words w ON w.headword = p.headword
               WHERE p.id = %s""",
            (id,),
        )
    else:
        cur.execute(
            """SELECT p.*,
                      w.id IS NOT NULL AS already_exists
               FROM pending_words p
               LEFT JOIN words w ON w.headword = p.headword
               WHERE p.status = %s
               ORDER BY p.created_at DESC LIMIT 100""",
            (status,),
        )
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/admin/api/pending/{pw_id}/preview")
def admin_pending_preview(pw_id: int, request: Request):
    admin_required(request)
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM pending_words WHERE id = %s", (pw_id,))
    pw = cur.fetchone()
    conn.close()
    if not pw:
        raise HTTPException(404, "Pending word not found")

    return {
        "headword": pw["headword"],
        "nikud": pw.get("headword_nikud") or pw["headword"],
        "translit": pw.get("translit") or "",
        "pos": pw.get("pos_slug") or pw.get("pos") or "noun",
        "gender": pw.get("gender") or "",
        "translations": pw.get("translation_enriched") or [pw.get("translation_ru") or ""],
        "examples": pw.get("examples") or [],
        "synonyms": pw.get("synonyms") or [],
        "notes": pw.get("notes") or "",
        "source": pw.get("source") or "",
        "status": pw["status"],
        "reviewed_at": str(pw.get("reviewed_at")) if pw.get("reviewed_at") else None,
    }


@app.post("/admin/api/pending/{pw_id}/approve")
def admin_approve(pw_id: int, request: Request):
    admin_required(request)
    conn = get_db_writable()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("SELECT * FROM pending_words WHERE id = %s AND status = 'pending'", (pw_id,))
    pw = cur.fetchone()
    if not pw:
        conn.close()
        raise HTTPException(404, "Pending word not found")

    # Check if word already exists (by headword)
    cur.execute("SELECT id FROM words WHERE headword = %s LIMIT 1", (pw["headword"],))
    existing = cur.fetchone()

    if not existing:
        # Insert into words table
        grammar = {"gender": pw["gender"], "number": pw.get("number", "")}
        cur.execute(
            """INSERT INTO words (headword, headword_nikud, translit,
               translation_enriched, notes, pos_slug, gender, grammar_json, source)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (headword, pos_slug) DO NOTHING
               RETURNING id""",
            (
                pw["headword"], pw["headword_nikud"], pw["translit"],
                json.dumps(pw["translation_enriched"]) if pw["translation_enriched"] else None,
                pw.get("notes") or pw.get("translation_ru") or "",
                pw["pos_slug"], pw["gender"], json.dumps(grammar), pw["source"],
            ),
        )
        new_word = cur.fetchone()
        word_id = new_word["id"] if new_word else None

        if word_id:
            # Insert examples
            examples = pw["examples"]
            if examples:
                if isinstance(examples, str):
                    examples = json.loads(examples)
                for ex in examples:
                    cur.execute(
                        "INSERT INTO word_examples (word_id, hebrew, translation) VALUES (%s, %s, %s)",
                        (word_id, ex.get("hebrew", ""), ex.get("translation", "")),
                    )

            # Insert synonyms
            synonyms = pw["synonyms"]
            if synonyms:
                if isinstance(synonyms, str):
                    synonyms = json.loads(synonyms)
                for syn in synonyms:
                    cur.execute(
                        "INSERT INTO word_synonyms (word_id, hebrew, translation) VALUES (%s, %s, %s)",
                        (word_id, syn.get("hebrew", ""), syn.get("translation", "")),
                    )

            # Update pre-computed counts
            cur.execute("""
                UPDATE words SET
                  example_count = (SELECT COUNT(*) FROM word_examples WHERE word_id = %s),
                  synonym_count = (SELECT COUNT(*) FROM word_synonyms WHERE word_id = %s),
                  phrase_count  = (SELECT COUNT(*) FROM word_phrases  WHERE word_id = %s)
                WHERE id = %s
            """, (word_id, word_id, word_id, word_id))

    # Mark as approved
    cur.execute(
        "UPDATE pending_words SET status = 'approved', reviewed_at = now() WHERE id = %s",
        (pw_id,),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "word_id": word_id if not existing else None, "already_existed": bool(existing)}


@app.get("/admin/api/pending/{pw_id}/check")
def admin_check(pw_id: int, request: Request):
    """Check a pending word: duplicates, data quality, warnings."""
    admin_required(request)
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    
    cur.execute("SELECT * FROM pending_words WHERE id = %s AND status = 'pending'", (pw_id,))
    pw = cur.fetchone()
    if not pw:
        conn.close()
        raise HTTPException(404, "Pending word not found")
    
    headword = pw["headword"]
    warnings = []
    duplicates = []
    
    # 1. Check words table
    cur.execute("SELECT headword, pos_slug, translation_enriched FROM words WHERE headword = %s", (headword,))
    word_dupes = cur.fetchall()
    for w in word_dupes:
        trans = w["translation_enriched"]
        if isinstance(trans, list):
            trans = trans[0] if trans else ""
        elif isinstance(trans, str):
            trans = trans[:60]
        duplicates.append({
            "table": "words",
            "headword": w["headword"],
            "pos": w["pos_slug"],
            "translation": str(trans)[:80] if trans else "",
        })
    
    # 2. Check verbs table
    cur.execute("SELECT infinitive_he, translation_ru, binyan FROM verbs WHERE infinitive_he = %s", (headword,))
    verb_dupes = cur.fetchall()
    for v in verb_dupes:
        duplicates.append({
            "table": "verbs",
            "headword": v["infinitive_he"],
            "pos": "verb",
            "translation": v["translation_ru"] or "",
        })
    
    # 3. Check verb_forms (conjugated forms)
    cur.execute("SELECT DISTINCT form_he, tense FROM verb_forms WHERE form_he = %s LIMIT 3", (headword,))
    form_matches = cur.fetchall()
    if form_matches:
        for vf in form_matches:
            duplicates.append({
                "table": "verb_forms",
                "headword": vf["form_he"],
                "pos": f"verb_form ({vf['tense']})",
                "translation": "— форма глагола",
            })
    
    # 4. Data quality checks
    if not pw.get("headword_nikud", "").strip():
        warnings.append("Нет никуда (огласовок)")
    if not pw.get("translit", "").strip():
        warnings.append("Нет транслитерации")
    
    translit = pw.get("translit", "")
    if translit and any(c.isascii() and c.isalpha() for c in translit):
        warnings.append("Транслитерация содержит латиницу")
    
    if not pw.get("translation_ru", "").strip() and not pw.get("translation_enriched"):
        warnings.append("Нет перевода")
    
    notes = pw.get("notes", "")
    if not notes or len(notes) < 20:
        warnings.append("Описание отсутствует или слишком короткое")
    
    conn.close()
    # Pending word's translation for comparison
    pending_trans = pw.get("translation_ru") or ""
    if not pending_trans and pw.get("translation_enriched"):
        te = pw["translation_enriched"]
        if isinstance(te, list):
            pending_trans = te[0] if te else ""
        elif isinstance(te, str):
            pending_trans = te[:80]
    return {
        "ok": True,
        "headword": headword,
        "pending_translation": str(pending_trans)[:120] if pending_trans else "",
        "pending_pos": pw["pos_slug"],
        "duplicates": duplicates,
        "is_duplicate": len(duplicates) > 0,
        "warnings": warnings,
        "is_clean": len(duplicates) == 0 and len(warnings) == 0,
    }


@app.post("/admin/api/pending/{pw_id}/reject")
def admin_reject(pw_id: int, request: Request, note: str = ""):
    admin_required(request)
    conn = get_db_writable()
    cur = conn.cursor()
    cur.execute(
        "UPDATE pending_words SET status = 'rejected', reviewer_note = %s, reviewed_at = now() WHERE id = %s",
        (note, pw_id),
    )
    conn.commit()
    conn.close()
    return {"ok": True}


class EditPendingIn(BaseModel):
    translit: Optional[str] = None
    translation_ru: Optional[str] = None
    translation_enriched: Optional[list] = None
    examples: Optional[list] = None
    synonyms: Optional[list] = None
    notes: Optional[str] = None


@app.put("/admin/api/pending/{pw_id}")
async def admin_edit_pending(pw_id: int, request: Request):
    """Edit a pending word's fields before approval."""
    admin_required(request)
    body = await request.json()

    allowed = {"translit", "translation_ru", "translation_enriched", "examples", "synonyms", "notes",
               "pos_slug", "gender", "number"}
    updates = {k: body[k] for k in allowed if k in body}
    if not updates:
        raise HTTPException(400, "No valid fields to update")

    conn = get_db_writable()
    cur = conn.cursor()

    # Check it exists and is pending
    cur.execute("SELECT id FROM pending_words WHERE id = %s AND status = 'pending'", (pw_id,))
    if not cur.fetchone():
        conn.close()
        raise HTTPException(404, "Pending word not found")

    # Build SET clause
    set_parts = []
    params = []
    for k, v in updates.items():
        if k in ("translation_enriched", "examples", "synonyms"):
            set_parts.append(f"{k} = %s::jsonb")
            params.append(json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v)
        else:
            set_parts.append(f"{k} = %s")
            params.append(v)
    params.append(pw_id)

    cur.execute(
        f"UPDATE pending_words SET {', '.join(set_parts)} WHERE id = %s",
        params,
    )
    conn.commit()
    conn.close()
    return {"ok": True}


# ── History ──

@app.get("/admin/api/history")
def admin_history(request: Request):
    admin_required(request)
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        """SELECT * FROM pending_words
           WHERE status IN ('approved','rejected')
           ORDER BY reviewed_at DESC LIMIT 100"""
    )
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Feedback (admin view) ──

@app.get("/admin/api/feedback")
def admin_feedback(request: Request, resolved: Optional[bool] = None):
    admin_required(request)
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    if resolved is None:
        cur.execute(
            """SELECT uf.*, w.headword, w.headword_nikud, w.translit
               FROM user_feedback uf
               JOIN words w ON w.id = uf.word_id
               ORDER BY uf.created_at DESC LIMIT 200"""
        )
    else:
        cur.execute(
            """SELECT uf.*, w.headword, w.headword_nikud, w.translit
               FROM user_feedback uf
               JOIN words w ON w.id = uf.word_id
               WHERE uf.resolved = %s
               ORDER BY uf.created_at DESC LIMIT 200""",
            (resolved,),
        )
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.post("/admin/api/feedback/{fb_id}/toggle")
def admin_feedback_toggle(fb_id: int, request: Request):
    admin_required(request)
    conn = get_db_writable()
    cur = conn.cursor()
    cur.execute(
        "UPDATE user_feedback SET resolved = NOT resolved WHERE id = %s RETURNING id, resolved",
        (fb_id,),
    )
    row = cur.fetchone()
    conn.commit()
    conn.close()
    if not row:
        raise HTTPException(404, "Feedback not found")
    return {"ok": True, "id": row[0], "resolved": row[1]}


# ─── Contact Messages (admin) ──────────────────────────────────────────────

@app.get("/admin/api/contact")
def admin_contact_list(request: Request, resolved: Optional[bool] = Query(None)):
    admin_required(request)
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    if resolved is None:
        cur.execute("SELECT * FROM contact_messages ORDER BY created_at DESC LIMIT 100")
    else:
        cur.execute("SELECT * FROM contact_messages WHERE resolved = %s ORDER BY created_at DESC LIMIT 100", (resolved,))
    rows = [dict(r) for r in cur.fetchall()]
    for r in rows:
        r["created_at"] = r["created_at"].isoformat() if r.get("created_at") else None
    conn.close()
    return rows


@app.post("/admin/api/contact/{msg_id}/toggle")
def admin_contact_toggle(msg_id: int, request: Request):
    admin_required(request)
    conn = get_db_writable()
    cur = conn.cursor()
    cur.execute(
        "UPDATE contact_messages SET resolved = NOT resolved WHERE id = %s RETURNING id, resolved",
        (msg_id,),
    )
    row = cur.fetchone()
    conn.commit()
    conn.close()
    if not row:
        raise HTTPException(404, "Message not found")
    return {"ok": True, "id": row[0], "resolved": row[1]}


# ─── Word Verification (admin) ────────────────────────────────────────────

@app.get("/admin/api/verify")
def admin_verify_list(request: Request):
    admin_required(request)
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT v.*, w.pos_slug, w.gender, w.number, w.translit, 
               w.translation_enriched::text as translation_enriched
        FROM word_verification v
        JOIN words w ON w.id = v.word_id
        ORDER BY 
            CASE v.sonnet_verdict WHEN 'fix' THEN 0 WHEN 'error' THEN 1 ELSE 2 END,
            v.created_at DESC
        LIMIT 500
    """)
    rows = [dict(r) for r in cur.fetchall()]
    for r in rows:
        r["created_at"] = r["created_at"].isoformat() if r.get("created_at") else None
    conn.close()
    return rows


@app.post("/admin/api/verify/{verify_id}/apply")
def admin_verify_apply(verify_id: int, request: Request):
    """Apply Sonnet's suggested fixes to the word."""
    admin_required(request)
    conn = get_db_writable()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    
    # Get the verification record
    cur.execute("SELECT * FROM word_verification WHERE id = %s", (verify_id,))
    v = cur.fetchone()
    if not v:
        conn.close()
        raise HTTPException(404, "Verification not found")
    
    # Build update query
    updates = []
    params = []
    field_names = []
    if v["sonnet_pos"]:
        updates.append("pos_slug = %s")
        params.append(v["sonnet_pos"])
        field_names.append("pos: " + v["sonnet_pos"])
    if v["sonnet_gender"]:
        updates.append("gender = %s")
        params.append(v["sonnet_gender"])
        field_names.append("gender: " + v["sonnet_gender"])
    if v["sonnet_number"]:
        updates.append("number = %s")
        params.append(v["sonnet_number"])
        field_names.append("number: " + v["sonnet_number"])
    if v["sonnet_translit"]:
        updates.append("translit = %s")
        params.append(v["sonnet_translit"])
        field_names.append("translit: " + v["sonnet_translit"])
    if v["sonnet_translation"]:
        # sonnet_translation formats vary: PG array literal {a,b,c}, plain "a, b, c", "a; b; c"
        # translation_enriched is jsonb — must convert to proper JSON array
        trans_raw = v["sonnet_translation"].strip()
        try:
            # Strip PG array braces if present
            inner = trans_raw
            if inner.startswith("{") and inner.endswith("}"):
                inner = inner[1:-1]
            # Detect separator (comma vs semicolon)
            sep = ";" if inner.count(";") > inner.count(",") else ","
            items = [it.strip().strip('"').strip("'") for it in inner.split(sep) if it.strip()]
            trans_json = json.dumps(items, ensure_ascii=False)
        except Exception:
            # Fallback: wrap as single-item array
            trans_json = json.dumps([trans_raw], ensure_ascii=False)
        updates.append("translation_enriched = %s::jsonb")
        params.append(trans_json)
        field_names.append("translation: " + v["sonnet_translation"])
    
    if updates:
        params.append(v["word_id"])
        cur.execute(f"UPDATE words SET {', '.join(updates)} WHERE id = %s", params)
    
    # Mark as applied (even if no field updates — Sonnet confirmed fix but no specific fields)
    cur.execute("UPDATE word_verification SET applied = true WHERE id = %s", (verify_id,))
    
    conn.commit()
    conn.close()
    return {"ok": True, "applied": len(updates), "fields": field_names}


@app.post("/admin/api/verify/{verify_id}/skip")
def admin_verify_skip(verify_id: int, request: Request):
    """Skip this verification — mark as ok."""
    admin_required(request)
    conn = get_db_writable()
    cur = conn.cursor()
    cur.execute(
        "UPDATE word_verification SET sonnet_verdict = 'ok' WHERE id = %s",
        (verify_id,),
    )
    conn.commit()
    conn.close()
    return {"ok": True}


# ─── Enrichment Control ────────────────────────────────────────────────────

@app.get("/admin/api/enrichment/status")
def enrichment_status(request: Request):
    """Get enrichment settings: paused state, daily limit, today's count."""
    admin_required(request)
    conn = get_db()
    cur = conn.cursor()
    
    # Settings
    cur.execute("SELECT key, value FROM enrichment_settings")
    settings = {r[0]: r[1] for r in cur.fetchall()}
    
    # Today's inserted count
    cur.execute("""
        SELECT COALESCE(SUM(words_inserted), 0) 
        FROM enrichment_costs 
        WHERE run_at::date = CURRENT_DATE
    """)
    today_inserted = int(cur.fetchone()[0])
    
    # Today's costs
    cur.execute("""
        SELECT COALESCE(SUM(cost_usd), 0)::float
        FROM enrichment_costs
        WHERE run_at::date = CURRENT_DATE
    """)
    today_cost = float(cur.fetchone()[0])
    
    conn.close()
    
    return {
        "paused": settings.get("paused", "false") == "true",
        "daily_limit": int(settings.get("daily_limit", "30")),
        "today_inserted": today_inserted,
        "today_cost_usd": today_cost,
    }


@app.post("/admin/api/enrichment/toggle")
async def enrichment_toggle(request: Request):
    """Toggle enrichment paused state."""
    admin_required(request)
    conn = get_db_writable()
    cur = conn.cursor()
    
    # Toggle
    cur.execute("""
        UPDATE enrichment_settings 
        SET value = CASE WHEN value = 'true' THEN 'false' ELSE 'true' END,
            updated_at = NOW()
        WHERE key = 'paused'
        RETURNING value
    """)
    new_state = cur.fetchone()[0]
    conn.commit()
    conn.close()
    
    return {"ok": True, "paused": new_state == "true"}


@app.post("/admin/api/enrichment/limit")
async def enrichment_set_limit(request: Request):
    """Set daily word limit. Body: {"limit": 30}"""
    admin_required(request)
    body = await request.json()
    limit = int(body.get("limit", 30))
    if limit < 0:
        limit = 0
    if limit > 500:
        limit = 500
    
    conn = get_db_writable()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO enrichment_settings (key, value, updated_at) 
        VALUES ('daily_limit', %s, NOW())
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
    """, (str(limit),))
    conn.commit()
    conn.close()
    
    return {"ok": True, "daily_limit": limit}


# ─── Enrichment manual run ──────────────────────────────────────────────────

_enrichment_run_state = {"running": False, "started_at": None, "error": None}


def _run_enrichment_background():
    """Run enrichment pipeline in background thread."""
    global _enrichment_run_state
    try:
        from enrichment.run import main
        main()
    except Exception as e:
        _enrichment_run_state["error"] = str(e)
    finally:
        _enrichment_run_state["running"] = False


@app.post("/admin/api/enrichment/run")
def enrichment_run(request: Request):
    """Manually trigger enrichment pipeline (respects daily limit)."""
    admin_required(request)
    global _enrichment_run_state

    # Auto-reset if stuck (timeout 30 min)
    if _enrichment_run_state["running"]:
        elapsed = time_module.time() - (_enrichment_run_state.get("started_at") or 0)
        if elapsed > 1800:  # 30 minutes
            _enrichment_run_state = {"running": False, "started_at": None, "error": "timed out"}
        else:
            raise HTTPException(409, "Enrichment is already running")

    # Check if paused
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT value FROM enrichment_settings WHERE key = 'paused'")
    row = cur.fetchone()
    conn.close()
    if row and row[0] == "true":
        raise HTTPException(400, "Enrichment is paused. Resume first.")

    _enrichment_run_state = {"running": True, "started_at": time_module.time(), "error": None}
    t = threading.Thread(target=_run_enrichment_background, daemon=True)
    t.start()

    return {"ok": True, "started_at": _enrichment_run_state["started_at"]}


@app.get("/admin/api/enrichment/run-status")
def enrichment_run_status(request: Request):
    """Check if a manual enrichment run is in progress."""
    admin_required(request)
    return _enrichment_run_state


# ─── Enrichment Costs ───────────────────────────────────────────────────────

@app.get("/admin/api/costs")
def admin_costs(request: Request, days: int = 30):
    """Daily cost breakdown for enrichment pipeline."""
    admin_required(request)
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT
            run_at::date AS day,
            COUNT(*) AS requests,
            SUM(total_tokens) AS total_tokens,
            SUM(cost_usd) AS cost_usd,
            SUM(words_extracted) AS words_extracted,
            SUM(words_new) AS words_new,
            SUM(words_inserted) AS words_inserted,
            COUNT(*) FILTER (WHERE error IS NOT NULL) AS errors
        FROM enrichment_costs
        WHERE run_at >= NOW() - %s::interval
        GROUP BY run_at::date
        ORDER BY day DESC
    """, (f"{days} days",))
    rows = cur.fetchall()
    conn.close()
    
    # Convert Decimal to float for JSON
    days_list = []
    for r in rows:
        d = dict(r)
        d["day"] = str(d["day"])
        d["cost_usd"] = float(d["cost_usd"] or 0)
        d["total_tokens"] = int(d["total_tokens"] or 0)
        days_list.append(d)
    
    # Calculate summary
    total_cost = sum(d["cost_usd"] for d in days_list)
    total_tokens = sum(d["total_tokens"] for d in days_list)
    total_requests = sum(d["requests"] for d in days_list)
    total_words = sum(d["words_inserted"] for d in days_list)
    
    return {
        "days": days_list,
        "summary": {
            "total_cost_usd": round(total_cost, 6),
            "total_cost_ils": round(total_cost * 3.6, 4),
            "total_tokens": total_tokens,
            "total_requests": total_requests,
            "total_words_inserted": total_words,
            "avg_cost_per_day": round(total_cost / max(days, 1), 6),
        },
    }


@app.get("/admin/api/costs/summary")
def admin_costs_summary(request: Request):
    """Quick summary: today + this month, with model breakdown."""
    admin_required(request)
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    
    # Today
    cur.execute("""
        SELECT
            COUNT(*) AS requests,
            COALESCE(SUM(total_tokens), 0) AS total_tokens,
            COALESCE(SUM(cost_usd), 0)::float AS cost_usd,
            COALESCE(SUM(words_inserted), 0) AS words_inserted
        FROM enrichment_costs
        WHERE run_at::date = CURRENT_DATE
    """)
    today = dict(cur.fetchone())
    
    # Today by model
    cur.execute("""
        SELECT model, COUNT(*) AS requests, COALESCE(SUM(cost_usd), 0)::float AS cost_usd,
               COALESCE(SUM(total_tokens), 0) AS total_tokens
        FROM enrichment_costs
        WHERE run_at::date = CURRENT_DATE
        GROUP BY model
        ORDER BY cost_usd DESC
    """)
    today_models = [dict(r) for r in cur.fetchall()]
    
    # This month
    cur.execute("""
        SELECT
            COUNT(*) AS requests,
            COALESCE(SUM(total_tokens), 0) AS total_tokens,
            COALESCE(SUM(cost_usd), 0)::float AS cost_usd,
            COALESCE(SUM(words_inserted), 0) AS words_inserted
        FROM enrichment_costs
        WHERE date_trunc('month', run_at) = date_trunc('month', NOW())
    """)
    month = dict(cur.fetchone())
    
    # Month by model
    cur.execute("""
        SELECT model, COUNT(*) AS requests, COALESCE(SUM(cost_usd), 0)::float AS cost_usd,
               COALESCE(SUM(total_tokens), 0) AS total_tokens
        FROM enrichment_costs
        WHERE date_trunc('month', run_at) = date_trunc('month', NOW())
        GROUP BY model
        ORDER BY cost_usd DESC
    """)
    month_models = [dict(r) for r in cur.fetchall()]
    
    # Last 7 days total
    cur.execute("""
        SELECT COALESCE(SUM(cost_usd), 0)::float AS cost_usd
        FROM enrichment_costs
        WHERE run_at >= NOW() - INTERVAL '7 days'
    """)
    week = dict(cur.fetchone())
    
    conn.close()
    return {
        "today": today,
        "today_models": today_models,
        "month": month,
        "month_models": month_models,
        "week": {"cost_usd": week["cost_usd"]},
        "pricing": {
            "gemini-2.5-flash": {"input_per_1m": 0.15, "output_per_1m": 0.60, "label": "Gemini 2.5 Flash"},
            "claude-sonnet-4-20250514": {"input_per_1m": 3.0, "output_per_1m": 15.0, "label": "Claude Sonnet 4"},
        },
    }


# ─── Word of the Day ──────────────────────────────────────────────────────

@app.get("/api/word-of-day")
def word_of_day():
    """Deterministic word of the day based on date. Same word for everyone all day.
    
    Selection: 
    - Exclude function words (prepositions, conjunctions, pronouns, etc.)
    - Exclude very short words (length < 3, likely function words)
    - Prefer words with freq_rank 200–10000 (OpenSubtitles 2018 corpus)
    - Fall back to words without frequency data (keep existing POS/length filters)
    - Bias toward words with fewer examples (less common = more interesting)
    """
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        import datetime
        today = datetime.date.today().isoformat()
        day_hash = sum(ord(c) for c in today)
        
        # Exclude function-word POS, very short headwords, and too-common/too-rare words
        cur.execute("""
            SELECT COUNT(*) as cnt FROM words w
            LEFT JOIN word_frequencies f ON f.headword = w.headword
            WHERE w.pos_slug IS NOT NULL
              AND w.translation_enriched IS NOT NULL
              AND w.pos_slug NOT IN ('prep', 'conj', 'pron', 'pref', 'det', 'article',
                                     'intj', 'part', 'particle', 'suffix')
              AND LENGTH(w.headword) >= 3
              AND (f.freq_rank BETWEEN 200 AND 10000 OR f.freq_rank IS NULL)
        """)
        total = cur.fetchone()["cnt"]
        if total == 0:
            raise HTTPException(status_code=404)
        
        idx = (day_hash % total) + 1
        cur.execute("""
            SELECT w.id, w.headword, w.headword_nikud, w.translit,
                   w.pos_slug, w.gender, w.number,
                   w.translation_enriched, w.example_count
            FROM words w
            LEFT JOIN word_frequencies f ON f.headword = w.headword
            WHERE w.pos_slug IS NOT NULL
              AND w.translation_enriched IS NOT NULL
              AND w.pos_slug NOT IN ('prep', 'conj', 'pron', 'pref', 'det', 'article',
                                     'intj', 'part', 'particle', 'suffix')
              AND LENGTH(w.headword) >= 3
              AND (f.freq_rank BETWEEN 200 AND 10000 OR f.freq_rank IS NULL)
            ORDER BY 
              CASE WHEN f.freq_rank IS NOT NULL THEN 0 ELSE 1 END,
              w.example_count ASC, w.id
            LIMIT 1 OFFSET %s
        """, (idx - 1,))
        word = cur.fetchone()
        if not word:
            raise HTTPException(status_code=404)
        return dict(word)
    finally:
        conn.close()


# ─── Quiz ─────────────────────────────────────────────────────────────────

@app.get("/api/quiz")
def quiz():
    """Get a random word + 3 distractors for the mini-quiz."""
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        import random
        # Get one random word with decent frequency
        cur.execute("""
            SELECT id, headword, headword_nikud, translit, pos_slug,
                   translation_enriched
            FROM words WHERE translation_enriched IS NOT NULL
            ORDER BY RANDOM() LIMIT 1
        """)
        target = cur.fetchone()
        if not target:
            raise HTTPException(status_code=404)

        # Extract first translation
        import json
        te = target["translation_enriched"]
        if isinstance(te, list):
            correct_answer = te[0]
        elif isinstance(te, str):
            try:
                parsed = json.loads(te)
                correct_answer = parsed[0] if isinstance(parsed, list) else te
            except (json.JSONDecodeError, IndexError):
                correct_answer = te
        else:
            correct_answer = str(te)

        # Get 3 distractors — same POS as target, random other translations
        target_pos = target["pos_slug"]
        cur.execute("""
            SELECT translation_enriched FROM (
                SELECT DISTINCT ON (translation_enriched) translation_enriched
                FROM words
                WHERE id != %s AND translation_enriched IS NOT NULL
                  AND pos_slug = %s
            ) t
            ORDER BY RANDOM() LIMIT 10
        """, (target["id"], target_pos))

        distractor_raws = []
        for row in cur.fetchall():
            te_val = row["translation_enriched"]
            if isinstance(te_val, list):
                distractor_raws.append(te_val[0])
            elif isinstance(te_val, str):
                try:
                    p = json.loads(te_val)
                    distractor_raws.append(p[0] if isinstance(p, list) else te_val)
                except json.JSONDecodeError:
                    distractor_raws.append(te_val)
            else:
                distractor_raws.append(str(te_val))

        # Deduplicate and pick 3
        distractors = []
        seen = {correct_answer.lower().strip()}
        for d in distractor_raws:
            key = d.lower().strip()
            if key not in seen and len(distractors) < 3:
                seen.add(key)
                distractors.append(d)

        # If we don't have 3 unique, add generic fallbacks
        fallbacks = ["человек", "дом", "хороший", "идти", "большой", "дело", "говорить"]
        while len(distractors) < 3:
            for fb in fallbacks:
                if fb.lower() not in seen and len(distractors) < 3:
                    seen.add(fb.lower())
                    distractors.append(fb)

        # Shuffle options
        options = [correct_answer] + distractors
        random.shuffle(options)
        correct_index = options.index(correct_answer)

        return {
            "headword": target["headword"],
            "headword_nikud": target.get("headword_nikud"),
            "translit": target.get("translit"),
            "pos_slug": target.get("pos_slug"),
            "options": options,
            "correct_index": correct_index,
            "word_id": target["id"],
        }
    finally:
        conn.close()


# ─── Language Facts API ────────────────────────────────────────────────────

@app.get("/api/facts/random")
def random_fact():
    conn = psycopg2.connect(**PG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT id, fact_type, title, fact_body, source_url, created_at, published_at
        FROM language_facts
        WHERE is_published = true
        ORDER BY RANDOM()
        LIMIT 1
    """)
    row = cur.fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "No published facts yet")
    return dict(row)


@app.get("/api/facts")
def list_facts(page: int = Query(1, ge=1), limit: int = Query(10, ge=1, le=50)):
    offset = (page - 1) * limit
    conn = psycopg2.connect(**PG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT count(*) AS total FROM language_facts WHERE is_published = true")
    total = cur.fetchone()["total"]
    cur.execute("""
        SELECT id, fact_type, title, fact_body, source_url, created_at, published_at
        FROM language_facts
        WHERE is_published = true
        ORDER BY published_at DESC NULLS LAST, created_at DESC
        LIMIT %s OFFSET %s
    """, (limit, offset))
    rows = cur.fetchall()
    conn.close()
    return {"facts": [dict(r) for r in rows], "total": total, "page": page, "pages": max(1, (total + limit - 1) // limit)}


@app.get("/api/facts/{fact_id}")
def get_fact(fact_id: int):
    conn = psycopg2.connect(**PG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT id, fact_type, title, fact_body, source_url, created_at, published_at
        FROM language_facts
        WHERE id = %s AND is_published = true
    """, (fact_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Fact not found")
    return dict(row)


# Admin: list all facts (including unpublished) with filter and sort
@app.get("/admin/api/facts")
def admin_list_facts(filter: str = "all", sort: str = "newest"):
    conn = psycopg2.connect(**PG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    
    where = ""
    if filter == "published":
        where = "WHERE is_published = true"
    elif filter == "unpublished":
        where = "WHERE is_published = false"
    
    order = "ORDER BY COALESCE(published_at, created_at) DESC" if sort == "newest" else "ORDER BY COALESCE(published_at, created_at) ASC"
    
    cur.execute(f"""
        SELECT id, fact_type, title, fact_body, source_url, is_published, created_at, published_at
        FROM language_facts
        {where}
        {order}
        LIMIT 200
    """)
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.post("/admin/api/facts")
async def admin_create_fact(request: Request):
    body = await request.json()
    conn = psycopg2.connect(**PG)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        INSERT INTO language_facts (fact_type, title, fact_body, source_url, source_raw, is_published, published_at)
        VALUES (%s, %s, %s, %s, %s, %s, CASE WHEN %s THEN now() ELSE NULL END)
        RETURNING id
    """, (
        body.get("fact_type", "did_you_know"),
        body["title"],
        body["fact_body"],
        body.get("source_url"),
        body.get("source_raw"),
        body.get("is_published", False),
        body.get("is_published", False),
    ))
    fact_id = cur.fetchone()["id"]
    conn.commit()
    conn.close()
    return {"id": fact_id, "status": "ok"}


@app.put("/admin/api/facts/{fact_id}")
async def admin_update_fact(fact_id: int, request: Request):
    body = await request.json()
    conn = psycopg2.connect(**PG)
    cur = conn.cursor()
    cur.execute("""
        UPDATE language_facts
        SET fact_type = COALESCE(%s, fact_type),
            title = COALESCE(%s, title),
            fact_body = COALESCE(%s, fact_body),
            source_url = %s,
            source_raw = %s,
            is_published = COALESCE(%s, is_published),
            published_at = CASE WHEN COALESCE(%s, is_published) = true THEN now() ELSE published_at END
        WHERE id = %s
    """, (
        body.get("fact_type"),
        body.get("title"),
        body.get("fact_body"),
        body.get("source_url"),
        body.get("source_raw"),
        body.get("is_published"),
        body.get("is_published"),
        fact_id,
    ))
    conn.commit()
    conn.close()
    return {"status": "ok"}


@app.delete("/admin/api/facts/{fact_id}")
def admin_delete_fact(fact_id: int):
    conn = psycopg2.connect(**PG)
    cur = conn.cursor()
    cur.execute("DELETE FROM language_facts WHERE id = %s", (fact_id,))
    conn.commit()
    conn.close()
    return {"status": "ok"}


@app.post("/admin/api/enrichment/generate-facts")
def admin_generate_facts():
    """Trigger fact generation via enrichment script (Claude Sonnet)."""
    import subprocess
    try:
        result = subprocess.run(
            ["python3", "enrichment/generate_facts.py"],
            cwd=Path(__file__).parent,
            capture_output=True,
            text=True,
            timeout=180,
        )
        if result.returncode == 0:
            return {"status": "ok", "message": result.stdout.strip().split('\n')[-1] or "Факты сгенерированы"}
        else:
            err = (result.stderr + "\n" + result.stdout)[:800].strip() or "Неизвестная ошибка"
            return JSONResponse({"status": "error", "message": err}, status_code=500)
    except subprocess.TimeoutExpired:
        return JSONResponse({"status": "error", "message": "Генерация заняла больше 3 минут (Sonnet не ответил)"}, status_code=500)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


# ─── Static files ──────────────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Admin SPA — serve login + dashboard from static/admin/
ADMIN_STATIC = STATIC_DIR / "admin"
ADMIN_STATIC.mkdir(parents=True, exist_ok=True)
app.mount("/admin/static", StaticFiles(directory=str(ADMIN_STATIC)), name="admin_static")


@app.get("/admin/login")
def admin_login_page():
    return FileResponse(str(ADMIN_STATIC / "login.html"))


@app.get("/admin")
@app.get("/admin/")
def admin_dashboard():
    return RedirectResponse(url="/admin/pending")

@app.get("/admin/pending")
def admin_pending_page():
    return FileResponse(str(ADMIN_STATIC / "pending.html"))

@app.get("/admin/approved")
def admin_approved_page():
    return FileResponse(str(ADMIN_STATIC / "approved.html"))

@app.get("/admin/rejected")
def admin_rejected_page():
    return FileResponse(str(ADMIN_STATIC / "rejected.html"))

@app.get("/admin/feedback")
def admin_feedback_page():
    return FileResponse(str(ADMIN_STATIC / "feedback.html"))

@app.get("/admin/contact")
def admin_contact_page():
    return FileResponse(str(ADMIN_STATIC / "contact.html"))

@app.get("/admin/verify")
def admin_verify_page():
    return FileResponse(str(ADMIN_STATIC / "verify.html"))

@app.get("/admin/words")
def admin_words_page():
    return FileResponse(str(ADMIN_STATIC / "words.html"))

@app.get("/admin/costs")
def admin_costs_page():
    return FileResponse(str(ADMIN_STATIC / "costs.html"))

@app.get("/admin/facts")
def admin_facts_page():
    return FileResponse(str(ADMIN_STATIC / "facts.html"))


# ─── Facts public pages ──────────────────────────────────────────────────

@app.get("/facts")
def facts_page():
    return FileResponse(str(STATIC_DIR / "facts.html"))


@app.get("/facts/{fact_id}")
def fact_detail_page(fact_id: int):
    return FileResponse(str(STATIC_DIR / "fact.html"))


@app.get("/")
def index():
    return FileResponse(str(STATIC_DIR / "index.html"))
