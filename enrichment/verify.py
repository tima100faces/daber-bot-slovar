"""
Hebrew word verification — morphological rules + DB cross-check + LLM double-check.

Used by enrichment pipeline to flag potential errors before insertion.
Warnings are stored in pending_words.reviewer_note and shown in admin panel.
"""

import json
import re
import time

# ── Morphological rules ────────────────────────────────────────────────────

# Hebrew letters
HEBREW = set("אבגדהוזחטיכלמנסעפצקרשתךםןףץ")

# Mishkalim (noun patterns) — if word matches, it's likely a noun
NOUN_MISHKALIM = [
    (r"^מ[^י].{2,3}$", "miCCaC — существительное (место, инструмент)"),
    (r"^ת.{2,4}$", "tiCCuC — существительное (процесс)"),
    (r"^ה.{4,5}ה$", "hitCaCCut — существительное"),
    (r"^.{3,4}ון$", "CCaCon — существительное (уменьшительное/абстрактное)"),
    (r"^.{3,4}ות$", "CCaCut — существительное (абстрактное)"),
    (r"^.{3,4}ית$", "CCaCit — существительное (уменьшительное)"),
]

# Common feminine suffixes
FEM_SUFFIXES = ["ה", "ת", "ית", "ות"]

# Words that are ALWAYS masculine despite ending in ה/ת
MASC_EXCEPTIONS = ["לילה", "שולחן", "מקום", "כסא"]

# Words that look like verbs (hitpael/etc prefixes) but might be nouns
VERB_PREFIXES = ["הת", "התח", "התק", "התפ"]

# Words ending in ים are usually masculine plural
PLURAL_MASC = re.compile(r".{2,}ים$")

# Words ending in ות are usually feminine plural
PLURAL_FEM = re.compile(r".{2,}ות$")


def _ktiv_variants(hw: str) -> set:
    """All spellings that differ from `hw` by one optional mater (ו or י):
    each single deletion of a ו/י, and each insertion of a ו/י at any position.
    Used to catch ktiv male/haser duplicates (e.g. דיבור ↔ דבור)."""
    hw = hw.strip()
    out = set()
    for i, ch in enumerate(hw):
        if ch in ("ו", "י"):
            out.add(hw[:i] + hw[i + 1:])          # drop a mater
    for i in range(len(hw) + 1):
        for m in ("ו", "י"):
            out.add(hw[:i] + m + hw[i:])           # add a mater
    out.discard(hw)
    return out


# ── Hard guard: reject inflected verb forms / mislabeled verbs ──────────────
# The pipeline already drops anything the LLM tags pos_slug='verb'. But the LLM
# sometimes tags a conjugated verb FORM (e.g. נדרסה, ניצלה — past 3f.sg) as a
# noun/particle, and it slips through. These are not new headwords — they are
# inflections already covered by the verbs table. This guard catches them.

_MORPH = None


def _gloss_is_verb(translation: str) -> bool:
    """True if the head word of the Russian gloss parses as a verb/infinitive.
    Uses pymorphy3 (reliable POS) — avoids the naive '-ость nouns end in -ть' trap."""
    if not translation:
        return False
    g = translation.strip().strip('"').lstrip("[").strip()
    g = re.sub(r"\(.*?\)", "", g)
    g = re.split(r"[,;]", g)[0].strip()
    parts = g.split()
    if not parts:
        return False
    head = parts[0].strip("«»\"'.,!?")
    global _MORPH
    try:
        if _MORPH is None:
            import pymorphy3
            _MORPH = pymorphy3.MorphAnalyzer()
        parses = _MORPH.parse(head)
        return bool(parses) and parses[0].tag.POS in ("VERB", "INFN")
    except Exception:
        return False


def is_verb_candidate(word: dict) -> str:
    """Return a non-empty reason if this candidate is a verb mislabeled as a
    non-verb (so it must NOT be inserted as a new word); else ''.

    Detection is by MEANING — pymorphy3 on the Russian gloss — which is
    homograph-proof. Spelling is NOT used: many real nouns legitimately share a
    form with a verb (מחשב 'computer' = piel of 'to computerize'; חושב 'thinker'
    = pual 'was calculated'), so a spelling match would wrongly drop them. A
    verb FORM extracted from news text (נדרסה 'was run over', ניצלה 'was saved')
    instead carries a verb gloss, which this catches.
    """
    pos = (word.get("pos_slug") or "").strip()
    if pos in ("phrase", "verb"):
        return ""
    if _gloss_is_verb(word.get("translation_ru") or ""):
        return "перевод — глагол, а POS указан как не-глагол"
    return ""


def verify_morphology(word: dict) -> list[str]:
    """Check Hebrew morphological rules. Returns list of warning strings."""
    warnings = []
    headword = word.get("headword", "").strip()
    pos = word.get("pos_slug", "").strip()
    gender = word.get("gender", "").strip()

    if not headword:
        return warnings

    # Skip morphological checks for phrases
    if pos == "phrase":
        return warnings

    # Check: if word looks like a noun pattern but pos is not noun
    if pos and pos != "noun":
        for pattern, desc in NOUN_MISHKALIM:
            if re.match(pattern, headword):
                warnings.append(f"Возможно существительное ({desc}), а указано: {pos}")
                break

    # Check: feminine suffix but gender is masculine
    if gender == "m":
        for suffix in FEM_SUFFIXES:
            if headword.endswith(suffix) and headword not in MASC_EXCEPTIONS:
                if not PLURAL_MASC.search(headword):
                    warnings.append(f"Слово оканчивается на '{suffix}' (обычно ж.р.), но указан м.р.")
                    break

    # Check: masculine suffix but gender is feminine
    if gender == "f":
        if PLURAL_MASC.search(headword):
            warnings.append(f"Слово оканчивается на 'ים' (обычно м.р.), но указан ж.р.")

    # Check: hitpael-looking prefix — might be a verb
    if pos == "noun":
        for prefix in VERB_PREFIXES:
            if headword.startswith(prefix):
                warnings.append(f"Слово начинается с '{prefix}' — возможно, это глагол (hитпаэль)")
                break

    # Check: translit contains latin
    translit = word.get("translit", "")
    if translit and any(c.isascii() and c.isalpha() for c in translit):
        warnings.append("Транслитерация содержит латиницу")

    return warnings


# ── DB cross-check ─────────────────────────────────────────────────────────

def verify_against_db(word: dict) -> list[str]:
    """Cross-check word against existing dictionary. Returns warning strings."""
    import psycopg2
    import os
    
    headword = word.get("headword", "").strip()
    pos = word.get("pos_slug", "").strip()
    gender = word.get("gender", "").strip()
    translit = word.get("translit", "").strip()

    if not headword:
        return []

    # Skip DB cross-check for phrases (different comparison logic needed)
    if pos == "phrase":
        return []

    warnings = []
    
    try:
        conn = psycopg2.connect(
            host=os.environ.get("PGHOST", "127.0.0.1"),
            port=int(os.environ.get("PGPORT", "5434")),
            dbname=os.environ.get("PGDB", "daber_dict"),
            user=os.environ.get("PGUSER", "postgres"),
        )
        cur = conn.cursor()

        # 1. Same headword exists? Compare POS and gender
        cur.execute(
            "SELECT pos_slug, gender, translit, translation_enriched FROM words WHERE headword = %s LIMIT 3",
            (headword,),
        )
        existing_words = cur.fetchall()
        for ew in existing_words:
            if ew[0] and pos and ew[0] != pos:
                warnings.append(f"В словаре уже есть '{headword}' как '{ew[0]}', а новое — '{pos}'")
            if ew[1] and gender and ew[1] != gender:
                warnings.append(f"В словаре '{headword}' имеет род '{ew[1]}', а новое — '{gender}'")

        # 2. Same root words? Suggest synonyms
        if len(headword) >= 3:
            root_prefix = headword[:3]
            cur.execute(
                """SELECT headword, translation_enriched FROM words 
                   WHERE headword LIKE %s AND headword != %s LIMIT 5""",
                (root_prefix + "%", headword),
            )
            same_root = cur.fetchall()
            if same_root:
                existing_headwords = [r[0] for r in same_root]
                warnings.append(f"Однокоренные слова в словаре: {', '.join(existing_headwords[:5])}")

        # 3. Spelling-variant duplicate (ktiv male/haser): the candidate differs
        #    from an existing headword by exactly one optional mater (ו or י).
        #    We generate those one-mater variants and look them up exactly — tight
        #    enough to avoid the noise of skeleton-stripping. Show the existing
        #    word's gloss so a reviewer can instantly tell a true duplicate
        #    (same meaning) from a coincidental minimal pair (שבועה/שבעה).
        variants = list(_ktiv_variants(headword))
        if variants:
            hits = []
            cur.execute(
                "SELECT headword, translation_enriched FROM words WHERE headword = ANY(%s) LIMIT 5",
                (variants,),
            )
            for hw, te in cur.fetchall():
                gloss = ""
                try:
                    arr = te if isinstance(te, list) else (json.loads(te) if te else [])
                    if arr:
                        gloss = f" ({arr[0]})"
                except Exception:
                    pass
                hits.append(f"{hw}{gloss}")
            cur.execute(
                "SELECT infinitive_he, translation_ru FROM verbs WHERE infinitive_he = ANY(%s) LIMIT 5",
                (variants,),
            )
            for inf, tr in cur.fetchall():
                hits.append(f"{inf} ({tr})" if tr else inf)
            if hits:
                warnings.append(
                    f"Возможно дубликат (другое написание): уже есть {', '.join(hits)}"
                )

        # 4. Matches a conjugated form of an existing verb — likely a word-form,
        #    not a new headword. Warn (don't block): verbal nouns legitimately
        #    share spelling with verb forms.
        cur.execute(
            """SELECT DISTINCT v.infinitive_he FROM verb_forms vf
                   JOIN verbs v ON v.id = vf.verb_id
                   WHERE vf.form_he = %s AND vf.tense <> 'infinitive'
                     AND COALESCE(v.infinitive_he, '') <> ''
                   LIMIT 3""",
            (headword,),
        )
        verb_forms = [r[0] for r in cur.fetchall() if r[0]]
        if verb_forms:
            warnings.append(
                f"Совпадает с формой глагола ({', '.join(verb_forms)}) — проверьте, не словоформа ли это"
            )

        conn.close()
    except Exception as e:
        warnings.append(f"Ошибка проверки БД: {e}")

    return warnings


def verify_word(word: dict) -> list[str]:
    """Run all verification checks (morphology + DB). Returns combined warning list."""
    warnings = []
    warnings.extend(verify_morphology(word))
    warnings.extend(verify_against_db(word))
    return warnings


# ── LLM Verification (Layer 2) — Claude Sonnet via OpenRouter ─────────────

VERIFY_SYSTEM_PROMPT = """Ты — лингвист-эксперт по ивриту с глубоким знанием морфологии, синтаксиса и семитских языков. Твоя задача — проверить анализ слов, извлечённых из текстов. 

Для каждого слова дан контекст (реальное предложение из текста) и предполагаемый анализ (POS, род, число, перевод).

Проверь для каждого слова:
1. Правильно ли определена часть речи (pos_slug)? Особенно: не перепутан ли смихут с фразой, не пропущен ли глагол.
2. Правильно ли определён род (gender: m/f/""), учитывая морфологию (окончания ה, ת, ים) и исключения?
3. Правильно ли определено число (number: s/p/"")? Слова, существующие только во мн.ч. или только в ед.ч. — проверь.
4. Корректен ли перевод (translation_ru)? Нет ли более точного/частотного перевода?

Ответь СТРОГО в JSON-формате. Массив объектов, по одному на каждое слово. Каждый объект:
{
  "index": <номер слова из списка, начиная с 0>,
  "verdict": "ok" | "fix",
  "pos_slug": "<исправленный или исходный>",
  "gender": "<исправленный или исходный>",
  "number": "<исправленный или исходный>",
  "translation_ru": "<исправленный или исходный перевод>",
  "note": "<краткое пояснение на русском, только если verdict=fix>"
}

ПРАВИЛА:
- Если слово правильное — verdict: "ok", поля можно оставить пустыми или скопировать исходные.
- Если есть ошибка — verdict: "fix" и ВСЕ поля должны быть заполнены (даже те, что не менялись).
- Для фраз (phrase) — pos_slug="phrase", gender и number — пустые строки.
- НЕ выдумывай ошибки. Сомневаешься — лучше "ok".
- НИКАКОГО текста до или после JSON. Только массив объектов."""

VERIFY_USER_TEMPLATE = """Проверь анализ следующих слов. Для каждого дано контекстное предложение из реального текста.

{words_text}

Верни JSON-массив с результатами проверки."""


def _format_word_for_verify(word: dict, index: int) -> str:
    """Format a single word for the batch verify prompt."""
    ctx = word.get("context_sentence", "")
    ctx_trans = word.get("context_translation", "")
    return (
        f"--- Слово {index} ---\n"
        f"headword: {word.get('headword', '')}\n"
        f"pos_slug: {word.get('pos_slug', '')}\n"
        f"gender: {word.get('gender', '')}\n"
        f"number: {word.get('number', '')}\n"
        f"translation_ru: {word.get('translation_ru', '')}\n"
        f"context: {ctx}\n"
        + (f"context_translation: {ctx_trans}\n" if ctx_trans else "")
    )


def call_sonnet(system_prompt: str, user_prompt: str, max_tokens: int = 4096) -> tuple[dict, dict]:
    """Call Claude Sonnet via Anthropic API. Returns (parsed_json, usage_dict)."""
    import urllib.request
    import os

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        from dotenv import load_dotenv
        from pathlib import Path
        load_dotenv(Path(__file__).parent.parent / ".env")
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not found")

    url = "https://api.anthropic.com/v1/messages"
    payload = {
        "model": "claude-sonnet-4-20250514",
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
        "temperature": 0.1,
        "max_tokens": max_tokens,
    }
    data = json.dumps(payload, ensure_ascii=False).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )
    t0 = time.time()
    resp = urllib.request.urlopen(req, timeout=60)
    elapsed = time.time() - t0
    result = json.loads(resp.read())
    
    usage = result.get("usage", {})
    usage["elapsed_sec"] = elapsed
    usage["model"] = "claude-sonnet-4-20250514"
    
    content = result["content"][0]["text"].strip()

    # Strip markdown code fences if present
    if content.startswith("```"):
        content = content.split("\n", 1)[1]
        if content.endswith("```"):
            content = content[:-3].strip()

    return json.loads(content), usage


def verify_batch_llm(words: list[dict]) -> dict[int, list[str]]:
    """Send all flagged words to Sonnet in one batch. Returns {word_index: [warnings]}."""
    if not words:
        return {}

    parts = [_format_word_for_verify(w, i) for i, w in enumerate(words)]
    user_prompt = VERIFY_USER_TEMPLATE.format(words_text="\n\n".join(parts))

    t0 = time.time()
    try:
        results, usage = call_sonnet(VERIFY_SYSTEM_PROMPT, user_prompt, max_tokens=8192)
    except Exception as e:
        elapsed = time.time() - t0
        _log_sonnet_cost(words, elapsed, error=str(e))
        return {i: [f"LLM-проверка не удалась: {e}"] for i in range(len(words))}

    elapsed = usage.get("elapsed_sec", time.time() - t0)
    _log_sonnet_cost(words, elapsed, usage=usage)

    output = {}
    for item in results:
        idx = item.get("index", -1)
        if idx < 0 or idx >= len(words):
            continue
        if item.get("verdict") == "ok":
            output[idx] = []
        elif item.get("verdict") == "fix":
            fixes = []
            w = words[idx]
            for field in ["pos_slug", "gender", "number", "translation_ru"]:
                old_val = w.get(field, "")
                new_val = item.get(field, "")
                if new_val and new_val != old_val:
                    fixes.append(f"{field}={new_val}")
            note = item.get("note", "")
            msg = "LLM: ИСПРАВЛЕНО: " + ", ".join(fixes)
            if note:
                msg += f" ({note})"
            output[idx] = [msg]
        else:
            output[idx] = [f"LLM: {item.get('note', 'неожиданный ответ')}"]

    return output


def _log_sonnet_cost(words: list[dict], elapsed_sec: float, usage: dict = None, error: str = None):
    """Insert Sonnet verification cost into enrichment_costs."""
    try:
        conn = get_db_writable()
        cur = conn.cursor()
        prompt_tokens = usage.get("input_tokens", 0) if usage else 0
        output_tokens = usage.get("output_tokens", 0) if usage else 0
        total_tokens = prompt_tokens + output_tokens
        
        # Anthropic Sonnet pricing: $3/$15 per MTok
        cost = (prompt_tokens / 1_000_000) * 3.0 + (output_tokens / 1_000_000) * 15.0
        
        cur.execute("""
            INSERT INTO enrichment_costs 
                (model, source, text_chars, prompt_tokens, output_tokens, 
                 total_tokens, cost_usd, words_extracted, words_new, words_inserted, error, elapsed_sec)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            usage.get("model", "claude-sonnet-4-20250514") if usage else "claude-sonnet-4-20250514",
            "verification/batch",
            sum(len(json.dumps(w, ensure_ascii=False)) for w in words),
            prompt_tokens,
            output_tokens,
            total_tokens,
            round(cost, 8),
            len(words),
            0,
            0,
            error,
            round(elapsed_sec, 2),
        ))
        conn.commit()
        conn.close()
    except Exception:
        pass  # Don't fail verification on cost logging
