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
