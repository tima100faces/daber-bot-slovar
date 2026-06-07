"""
Enrichment pipeline — fetch texts → LLM extraction → pending_words.

Sources: Reddit r/Israel, RSS (Ynet/Haaretz/Walla/Mako), YouTube subs, Twitter/X
LLM: Claude Sonnet 4 (Anthropic)
Output: INSERT into pending_words (reviewed manually via admin panel)

Usage:
  python3 enrichment/pipeline.py --source reddit --text "טקסט כאן"
"""

import json
import os
import sys
import re
import time
import urllib.request
import urllib.error
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")

# ── LLM Prompt (STRICT JSON output) ──────────────────────────────────────

SYSTEM_PROMPT = """Ты — лингвист-лексикограф. Твоя задача: проанализировать текст на иврите и найти 5–10 слов, которые будут полезны новому репатрианту (уровень B1–C1). Это должны быть РЕАЛЬНЫЕ повседневные слова из живого контекста — не базовые (אבא, מים, ספר), не слишком редкие.

ВАЖНО:
- Ищи слова СРЕДНЕЙ сложности: не очевидные для начинающего, но реально используемые в быту/новостях/разговорах
- Каждое слово ДОЛЖНО быть взято из контекста предоставленного текста
- Для каждого слова укажи контекстное предложение ИЗ ТЕКСТА
- ⚠️ НЕ ИЗВЛЕКАЙ ГЛАГОЛЫ. Глаголы (verbs) УЖЕ полностью покрыты в словаре. Только: существительные, прилагательные, наречия, предлоги, союзы, местоимения, числительные, междометия, частицы, приставки.

Ответь СТРОГО в формате JSON-массива. Каждый элемент — объект:

{
  "headword": "מחשב",
  "headword_nikud": "מַחשֵׁב",
  "translit": "махшЕв",
  "pos_slug": "noun",
  "gender": "m",
  "number": "s",
  "translation_ru": "компьютер",
  "translation_enriched": ["компьютер", "вычислительная машина", "ПК"],
  "context_sentence": "קניתי מחשב חדש אתמול",
  "context_translation": "я купил новый компьютер вчера",
  "examples": [
    {"hebrew": "המחשב שלי התקלקל", "translation": "мой компьютер сломался"}
  ],
  "synonyms": [
    {"hebrew": "מחשבון", "translation": "калькулятор"}
  ],
  "notes": "«מחשב» — существительное мужского рода. Основное значение: электронное устройство для обработки данных. В разговорном иврите также используется для обозначения калькулятора. Однокоренные слова: חישב (посчитал), תחשיב (расчёт)."
},
{
  "headword": "בקלות",
  "headword_nikud": "בְּקַלוּת",
  "translit": "бэкалУт",
  "pos_slug": "adv",
  "gender": "",
  "number": "",
  "translation_ru": "легко",
  "translation_enriched": ["легко", "без труда", "с лёгкостью"],
  "context_sentence": "הוא פתר את הבעיה בקלות",
  "context_translation": "он легко решил проблему",
  "examples": [],
  "synonyms": [],
  "notes": "«בקלות» — наречие образа действия. Обозначает выполнение без усилий."
}

ПРАВИЛА:
1. headword — слово или фраза на иврите БЕЗ огласовок:
   - Одиночные слова: в словарной форме (м.р. ед.ч. для сущ., инфинитив для глаголов)
   - Составные слова (смихут, например «בית ספר»): headword через пробел, pos_slug = noun
   - Устойчивые словосочетания/термины (например «מועצה אזורית»): headword через пробел, pos_slug = phrase
2. headword_nikud — то же слово С огласовками (никуд)
3. translit — ТОЛЬКО кириллица, НИКАКОЙ латиницы. Русская транслитерация, ударение ЗАГЛАВНОЙ буквой: махшЕв, тикшОрет, экзИт
4. pos_slug — строго одно из: noun, adj, adv, prep, conj, pron, num, intj, particle, pref, phrase. 
   НИКАКИХ ГЛАГОЛОВ (verb) — глаголы уже полностью покрыты в словаре.
   phrase — только для настоящих фраз/терминов, НЕ для смихута.
5. gender — m, f, или пустая строка если не применимо
6. number — ТОЛЬКО для noun и adj: s (ед.ч.), p (мн.ч.). Для всех остальных (adv, prep, conj…) — ВСЕГДА пустая строка "". Не придумывай число там, где его нет.
7. translation_ru — ОДИН основной перевод (строка)
8. translation_enriched — 1-4 варианта перевода (массив строк)
9. context_sentence — предложение ИЗ ПРЕДОСТАВЛЕННОГО ТЕКСТА, где встречается слово
10. context_translation — перевод этого предложения на русский
11. examples — 1-3 своих примера употребления (массив {hebrew, translation})
12. synonyms — 1-2 близких по смыслу слов (массив {hebrew, translation})
13. notes — краткое лингвистическое описание на русском (3-5 предложений), СТРОГО в стиле:
    «СЛОВО» — это ЧАСТЬ РЕЧИ (РОД, ЧИСЛО). Основное значение: ...
    Используется для / в контексте ... Также может быть формой ...
    Однокоренные слова: ... (если есть).
    Примеры хорошего стиля:
    «מלאת» — это форма ж.р. ед.ч. прилагательного «מלא» (полный). Используется для описания наполненности. Также может быть глагольной формой от «למלא» (наполнять).
    «מושבות» — это форма мн.ч. от «מושבה» (колония). В израильском контексте — ранние сельхозпоселения пионеров-сионистов. Отличать от «мошавим».

ОТВЕТЬ ТОЛЬКО JSON-массивом. Никакого текста до или после. Никаких markdown-блоков с ```."""

USER_PROMPT_TEMPLATE = """Найди 5–10 слов среднего уровня сложности (B1–C1) для изучающего иврит репатрианта. Слова должны быть из предоставленного текста.

Источник: {source}
Текст:
---
{text}
---

Ответь ТОЛЬКО JSON-массивом. Без текста до и после."""


# ── LLM Client (Anthropic Sonnet) ────────────────────────────────────────

ANTHROPIC_MODEL = "claude-sonnet-4-20250514"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

# Sonnet pricing (USD per 1M tokens)
SONNET_PRICE_INPUT = 3.0 / 1_000_000    # $3.00 per 1M input
SONNET_PRICE_OUTPUT = 15.0 / 1_000_000  # $15.00 per 1M output


def _get_anthropic_key() -> str:
    """Get Anthropic API key from env or key files."""
    api_key = os.environ.get("ANTHROPIC_API_KEY") or ""
    if not api_key:
        # Fallback: check key files
        for path in ["/tmp/ak_b64", "/tmp/.apikey_tmp"]:
            try:
                with open(path) as f:
                    data = f.read().strip()
                if len(data) > 20:
                    try:
                        import base64
                        api_key = base64.b64decode(data).decode()
                    except Exception:
                        api_key = data
                    break
            except FileNotFoundError:
                continue
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not found in env or key files")
    return api_key


def _calc_cost(usage: dict) -> float:
    """Calculate USD cost from Anthropic usage."""
    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)
    return round(input_tokens * SONNET_PRICE_INPUT + output_tokens * SONNET_PRICE_OUTPUT, 8)


def call_sonnet(system_prompt: str, user_prompt: str, max_tokens: int = 16384) -> tuple[list, dict]:
    """Call Anthropic Sonnet API. Returns (parsed_words, usage_dict).

    usage_dict keys: prompt_tokens, output_tokens, total_tokens, cost_usd
    """
    api_key = _get_anthropic_key()

    payload = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": max_tokens,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
    }

    data = json.dumps(payload, ensure_ascii=False).encode()
    req = urllib.request.Request(
        ANTHROPIC_URL, data=data,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }
    )

    try:
        resp = urllib.request.urlopen(req, timeout=90)
        result = json.loads(resp.read())

        input_tokens = result.get("usage", {}).get("input_tokens", 0)
        output_tokens = result.get("usage", {}).get("output_tokens", 0)
        usage = {
            "prompt_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "cost_usd": _calc_cost({"input_tokens": input_tokens, "output_tokens": output_tokens}),
        }

        text = result["content"][0]["text"]
        # Strip markdown fences if present
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
        parsed = json.loads(text)
        # Handle both array and {words: [...]} responses
        if isinstance(parsed, dict) and "words" in parsed:
            return parsed["words"], usage
        if isinstance(parsed, list):
            return parsed, usage
        raise RuntimeError(f"Unexpected Sonnet response type: {type(parsed)}")
    except json.JSONDecodeError as e:
        raw_sample = text[:300] if 'text' in dir() else "(binary)"
        raise RuntimeError(f"Sonnet JSON parse error: {e}. Raw: {raw_sample}")
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:500]
        raise RuntimeError(f"Anthropic HTTP {e.code}: {body}")


# ── Database ─────────────────────────────────────────────────────────────

def get_db():
    conn = psycopg2.connect(
        host=os.environ.get("PGHOST", "127.0.0.1"),
        port=int(os.environ.get("PGPORT", "5434")),
        dbname=os.environ.get("PGDB", "daber_dict"),
        user=os.environ.get("PGUSER", "postgres"),
    )
    conn.set_session(readonly=True, autocommit=True)
    return conn


def get_db_writable():
    return psycopg2.connect(
        host=os.environ.get("PGHOST", "127.0.0.1"),
        port=int(os.environ.get("PGPORT", "5434")),
        dbname=os.environ.get("PGDB", "daber_dict"),
        user=os.environ.get("PGUSER", "postgres"),
    )


def filter_existing_words(headwords: list[str]) -> list[str]:
    """Return headwords NOT already in the words table or approved in pending_words.

    Normalises: strips definite article ה prefix before comparison.
    """
    if not headwords:
        return []

    # Normalise headwords: strip leading ה (definite article) for comparison
    normalised = {}
    for h in headwords:
        h = h.strip()
        normalised[h] = h
        if h.startswith('ה') and len(h) > 1:
            stripped = h[1:]
            normalised[stripped] = h  # map stripped → original

    conn = get_db()
    cur = conn.cursor()
    # Check main words table (with normalised forms)
    cur.execute("SELECT headword FROM words WHERE headword = ANY(%s)", (list(normalised.keys()),))
    existing = {r[0] for r in cur.fetchall()}
    # Also check pending_words
    cur.execute(
        "SELECT headword FROM pending_words WHERE headword = ANY(%s) AND status IN ('approved', 'pending', 'rejected')",
        (list(normalised.keys()),),
    )
    existing |= {r[0] for r in cur.fetchall()}
    # Also check verbs table (infinitive_he is the verb headword)
    cur.execute("SELECT infinitive_he FROM verbs WHERE infinitive_he = ANY(%s)", (list(normalised.keys()),))
    existing |= {r[0] for r in cur.fetchall()}
    conn.close()

    # Return original headwords that are NOT in existing set
    return [orig for norm, orig in normalised.items() if norm not in existing]


def insert_pending_words(words: list[dict], source: str, source_context: str) -> int:
    """Insert extracted words into pending_words. Returns count inserted."""
    conn = get_db_writable()
    cur = conn.cursor()
    inserted = 0

    for w in words:
        headword = w.get("headword", "").strip()
        if not headword:
            continue

        # Skip if already in pending (avoid duplicates across runs)
        cur.execute(
            "SELECT 1 FROM pending_words WHERE headword = %s AND status = 'pending'",
            (headword,),
        )
        if cur.fetchone():
            continue

        cur.execute(
            """INSERT INTO pending_words
               (headword, headword_nikud, translit, pos_slug, gender,
                translation_ru, translation_enriched, examples, synonyms,
                notes, reviewer_note, source, source_context)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                headword,
                w.get("headword_nikud", ""),
                _fix_translit(w.get("translit", "")),
                w.get("pos_slug", ""),
                w.get("gender", ""),
                w.get("translation_ru", ""),
                json.dumps(w.get("translation_enriched", []), ensure_ascii=False),
                json.dumps(w.get("examples", []), ensure_ascii=False),
                json.dumps(w.get("synonyms", []), ensure_ascii=False),
                w.get("notes", ""),
                _format_warnings(w.get("_warnings", [])),
                source,
                source_context,
            ),
        )
        inserted += 1

    conn.commit()
    conn.close()
    return inserted


def _check_daily_limit() -> tuple[int, int]:
    """Return (remaining, limit) — how many more words can be inserted today."""
    try:
        conn = get_db_writable()
        cur = conn.cursor()
        cur.execute("SELECT value FROM enrichment_settings WHERE key = 'daily_limit'")
        row = cur.fetchone()
        limit = int(row[0]) if row else 30
        
        cur.execute("""
            SELECT COALESCE(SUM(words_inserted), 0)
            FROM enrichment_costs
            WHERE run_at::date = CURRENT_DATE
        """)
        today = int(cur.fetchone()[0])
        conn.close()
        return max(0, limit - today), limit
    except Exception:
        return 999, 999  # If DB check fails, allow (fail open)


# ── Pipeline ─────────────────────────────────────────────────────────────

def process_text(text: str, source: str) -> dict:
    """Send text to LLM, extract words, filter, insert. Returns stats dict."""
    user_prompt = USER_PROMPT_TEMPLATE.format(source=source, text=text[:3000])

    print(f"  → Calling Sonnet ({len(text)} chars)...")
    t0 = time.time()

    usage = None
    try:
        words, usage = call_sonnet(SYSTEM_PROMPT, user_prompt)
    except RuntimeError as e:
        print(f"  ✗ LLM error: {e}")
        _save_cost(source, len(text), error=str(e), elapsed=time.time() - t0)
        return {"error": str(e), "extracted": 0, "new": 0, "inserted": 0}

    elapsed = time.time() - t0
    print(f"  ✓ Extracted {len(words)} words ({elapsed:.1f}s, {usage.get('total_tokens', 0):,} tokens, \${usage.get('cost_usd', 0):.6f})")

    # Filter against existing dictionary
    # Safety: filter out any verbs before anything else (belt-and-suspenders)
    non_verbs = [w for w in words if w.get("pos_slug", "") != "verb"]
    if len(non_verbs) < len(words):
        print(f"  ⚠ Filtered out {len(words) - len(non_verbs)} verb(s) from LLM output")
        words = non_verbs
    
    headwords = [w.get("headword", "").strip() for w in words if w.get("headword", "").strip()]
    new_headwords = filter_existing_words(headwords)
    new_words = [w for w in words if w.get("headword", "").strip() in new_headwords]

    print(f"  → {len(new_words)}/{len(words)} are new (not in dictionary)")

    # Hard guard: drop candidates that are actually inflected verb forms or whose
    # Russian gloss is a verb (LLM mislabeled the POS, e.g. נדרסה/ניצלה tagged as
    # particle). Verbs are covered by the verbs table — they are not new headwords.
    if new_words:
        try:
            from enrichment.verify import is_verb_candidate
            kept = []
            for w in new_words:
                reason = is_verb_candidate(w)
                if reason:
                    print(f"  🚫 Drop '{w.get('headword','')}' ({w.get('pos_slug','')}) — {reason}")
                else:
                    kept.append(w)
            if len(kept) < len(new_words):
                print(f"  🚫 Guard dropped {len(new_words) - len(kept)} verb-form/mislabeled-verb candidate(s)")
            new_words = kept
        except Exception as e:
            print(f"  ⚠ Verb-guard error (non-fatal): {e}")

    # Verify new words (morphology + DB cross-check, then batch LLM)
    verified_count = 0
    llm_verified = 0
    if new_words:
        try:
            from enrichment.verify import verify_word, verify_batch_llm

            # Layer 1: morphology + DB check
            flagged = []  # (index, word) pairs for LLM batch
            for i, w in enumerate(new_words):
                warns = verify_word(w)
                if warns:
                    w["_warnings"] = warns
                    flagged.append((i, w))
                    verified_count += 1

            # Layer 2: batch LLM double-check for all flagged words
            if flagged:
                try:
                    flagged_words = [w for _, w in flagged]
                    batch_results = verify_batch_llm(flagged_words)
                    for batch_idx, (orig_idx, w) in enumerate(flagged):
                        llm_warns = batch_results.get(batch_idx, [])
                        if llm_warns:
                            w.setdefault("_warnings", []).extend(llm_warns)
                            llm_verified += 1
                except Exception as e:
                    for _, w in flagged:
                        w.setdefault("_warnings", []).append(f"LLM-проверка не удалась: {e}")

            if verified_count:
                print(f"  🔍 Verification: {verified_count} words have warnings (LLM batch-checked: {llm_verified})")
        except Exception as e:
            print(f"  ⚠ Verification error (non-fatal): {e}")

    inserted = 0
    if new_words:
        remaining, limit = _check_daily_limit()
        if remaining <= 0:
            print(f"  ⏸ Daily limit reached ({limit} words). Skipping insert.")
        else:
            if len(new_words) > remaining:
                print(f"  ⚠ Daily limit: only {remaining}/{limit} slots left. Trimming {len(new_words)} → {remaining} words.")
                new_words = new_words[:remaining]
            inserted = insert_pending_words(new_words, source, text[:500])
            print(f"  ✓ Inserted {inserted} into pending_words")

    # Save cost tracking
    _save_cost(source, len(text), usage, len(words), len(new_words), inserted, elapsed)

    return {"extracted": len(words), "new": len(new_words), "inserted": inserted, "elapsed": round(elapsed, 1)}


def _save_cost(source: str, text_chars: int, usage: dict = None, 
               extracted: int = 0, new: int = 0, inserted: int = 0,
               elapsed: float = 0, error: str = None):
    """Insert a row into enrichment_costs table."""
    try:
        conn = get_db_writable()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO enrichment_costs 
                (model, source, text_chars, prompt_tokens, output_tokens, thoughts_tokens, 
                 total_tokens, cost_usd, words_extracted, words_new, words_inserted, error, elapsed_sec)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            "claude-sonnet-4-20250514",
            source,
            text_chars,
            usage.get("prompt_tokens", 0) if usage else 0,
            usage.get("output_tokens", 0) if usage else 0,
            usage.get("thoughts_tokens", 0) if usage else 0,
            usage.get("total_tokens", 0) if usage else 0,
            usage.get("cost_usd", 0) if usage else 0,
            extracted, new, inserted,
            error,
            round(elapsed, 1) if elapsed else None,
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"  ⚠ Failed to save cost: {e}")


def _format_warnings(warnings: list[str]) -> str:
    """Format verification warnings for reviewer_note field."""
    if not warnings:
        return ""
    return "⚠ " + " | ".join(warnings)


def _fix_translit(translit: str) -> str:
    """Fix transliteration: convert Latin to Cyrillic, fix h→х in middle of words.

    Hebrew ה = Russian 'х' (not Latin 'h'). Exception: word-initial 'h'
    may be the definite article (ha-), kept as Latin 'h'.
    Also handles LLM occasionally outputting full Latin transliteration (Ekzit → Экзит).
    """
    if not translit:
        return translit

    # Step 1: if the translit is predominantly Latin, convert character-by-character
    latin_chars = sum(1 for c in translit if c.isalpha() and c.isascii())
    cyrillic_chars = sum(1 for c in translit if 'а' <= c.lower() <= 'я' or c.lower() == 'ё')
    if latin_chars > cyrillic_chars:
        translit = _latin_to_cyrillic(translit)

    # Step 2: Replace Latin 'h' with Cyrillic 'х' when NOT at word start
    # Pattern: Cyrillic letter then 'h' then Cyrillic letter
    fixed = re.sub(r'(?<=[а-яё])h(?=[а-яё])', 'х', translit)

    return fixed


def _latin_to_cyrillic(text: str) -> str:
    """Convert Latin transliteration to Cyrillic (one-to-one mapping)."""
    mapping = {
        'a': 'а', 'b': 'б', 'c': 'ц', 'd': 'д', 'e': 'е',
        'f': 'ф', 'g': 'г', 'h': 'х', 'i': 'и', 'j': 'й',
        'k': 'к', 'l': 'л', 'm': 'м', 'n': 'н', 'o': 'о',
        'p': 'п', 'q': 'к', 'r': 'р', 's': 'с', 't': 'т',
        'u': 'у', 'v': 'в', 'w': 'в', 'x': 'кс', 'y': 'ы',
        'z': 'з',
        'A': 'А', 'B': 'Б', 'C': 'Ц', 'D': 'Д', 'E': 'Э',
        'F': 'Ф', 'G': 'Г', 'H': 'Х', 'I': 'И', 'J': 'Й',
        'K': 'К', 'L': 'Л', 'M': 'М', 'N': 'Н', 'O': 'О',
        'P': 'П', 'Q': 'К', 'R': 'Р', 'S': 'С', 'T': 'Т',
        'U': 'У', 'V': 'В', 'W': 'В', 'X': 'Кс', 'Y': 'Ы',
        'Z': 'З',
        'sh': 'ш', 'ch': 'ч', 'zh': 'ж', 'ts': 'ц',
        'Sh': 'Ш', 'Ch': 'Ч', 'Zh': 'Ж', 'Ts': 'Ц',
        'SCH': 'Щ', 'Sch': 'Щ', 'sch': 'щ',
        'ya': 'я', 'ye': 'е', 'yo': 'ё', 'yu': 'ю',
        'Ya': 'Я', 'Ye': 'Е', 'Yo': 'Ё', 'Yu': 'Ю',
    }
    result = text
    # Multi-char first (sh, ch, ya, etc.)
    for latin, cyr in sorted(mapping.items(), key=lambda x: -len(x[0])):
        result = result.replace(latin, cyr)
    return result


# ── Main ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Daber enrichment pipeline")
    ap.add_argument("--source", default="test", help="Source name")
    ap.add_argument("--text", help="Hebrew text to extract words from")
    args = ap.parse_args()

    if args.text:
        result = process_text(args.text, args.source)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print("Usage: python3 enrichment/pipeline.py --source reddit --text 'טקסט בעברית'")
