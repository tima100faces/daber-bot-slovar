"""
Batch verification of dictionary words using Claude Sonnet.
Verifies: POS, gender, number, translit, translation correctness.
Saves results to word_verification table.
"""
import os
import json
import time
import urllib.request
import urllib.error
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY")
if not ANTHROPIC_KEY:
    print("❌ ANTHROPIC_API_KEY not set in .env")
    exit(1)

API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-4-20250514"

import psycopg2
import psycopg2.extras

BATCH_SIZE = 20

def get_db():
    return psycopg2.connect(
        host="127.0.0.1", port=5434, dbname="daber_dict",
        user="postgres", password=os.getenv("PGPASSWORD")
    )

def get_words_to_verify():
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT DISTINCT ON (w.id)
            w.id, w.headword, w.headword_nikud, w.translit, w.pos_slug,
            w.gender, w.number, w.translation_enriched,
            w.grammar_json, w.notes,
            COALESCE(
                (SELECT json_agg(json_build_object('hebrew', we.hebrew, 'translation', we.translation))
                 FROM word_examples we WHERE we.word_id = w.id LIMIT 3),
                '[]'::json
            ) as examples
        FROM words w
        LEFT JOIN word_examples we ON we.word_id = w.id
        WHERE 
            (w.translit ~ '[a-zA-Z]')
            OR (w.gender = 'm' AND (w.headword LIKE '%ה' OR w.headword LIKE '%ת'))
            OR (w.gender = 'f' AND w.headword LIKE '%ים')
            OR (w.pos_slug != 'verb' AND (w.headword LIKE 'הת%'))
            OR ((w.pos_slug = 'noun' OR w.pos_slug = 'adj') AND (w.gender IS NULL OR w.gender = ''))
            OR (w.translit IS NOT NULL AND w.translit != '' AND (LENGTH(w.translit) < LENGTH(w.headword)/2 OR LENGTH(w.translit) > LENGTH(w.headword)*3))
            OR (we.id IS NULL)
            OR (w.headword IN (SELECT headword FROM words GROUP BY headword HAVING COUNT(DISTINCT pos_slug) > 1))
        ORDER BY w.id
    """)
    words = [dict(r) for r in cur.fetchall()]
    conn.close()
    return words

def call_sonnet(batch_words, batch_num, total_batches):
    """Send a batch of words to Sonnet for verification."""
    
    # Build the word list for the prompt
    words_text = []
    for i, w in enumerate(batch_words):
        examples = json.loads(w['examples']) if isinstance(w['examples'], str) else (w['examples'] or [])
        ex_text = ""
        if examples:
            ex_parts = []
            for ex in examples[:2]:
                heb = ex.get('hebrew', '')
                trans = ex.get('translation', '')
                if heb:
                    ex_parts.append(f"{heb} — {trans}" if trans else heb)
            if ex_parts:
                ex_text = f" | Примеры: {'; '.join(ex_parts)}"
        
        translation = w.get('translation_enriched')
        if isinstance(translation, str):
            try:
                translation = json.loads(translation)
            except:
                pass
        
        words_text.append(
            f"{i+1}. [{w['id']}] {w['headword']} "
            f"(никуд: {w.get('headword_nikud') or '—'}, "
            f"транслит: {w.get('translit') or '—'}, "
            f"POS: {w['pos_slug']}, "
            f"род: {w.get('gender') or '—'}, "
            f"число: {w.get('number') or '—'}, "
            f"перевод: {json.dumps(translation, ensure_ascii=False) if translation else '—'})"
            f"{ex_text}"
        )
    
    prompt = f"""Ты — эксперт по ивриту. Проверь следующие {len(batch_words)} слов из иврит-русского словаря. Для каждого слова проверь:
- Правильно ли указана часть речи (POS)?
- Правильно ли указан род (gender: m/f)?
- Правильно ли указано число (number: s/p)?
- Правильно ли записан кириллический транслит (ударение обозначено заглавной буквой)?
- Соответствует ли русский перевод значению слова?

Список слов:
{chr(10).join(words_text)}

Верни ТОЛЬКО валидный JSON-массив. Для каждого слова:
- id: число (word_id в квадратных скобках)
- verdict: "ok" если всё правильно, "fix" если нужны исправления
- pos: исправленная часть речи (или null если ок)
- gender: исправленный род (или null)
- number: исправленное число (или null)
- translit: исправленный транслит (или null)
- translation: исправленный перевод (или null)
- explanation: краткое объяснение на русском (почему исправление, или "всё верно")

Пример ответа:
[{{"id": 123, "verdict": "fix", "pos": "verb", "gender": null, "number": null, "translit": "ката́в", "translation": "писал", "explanation": "Это глагол прошедшего времени, не существительное"}}]

Проверяй ВСЕ слова. Даже те, что выглядят правильно — подтверди это. Не пропускай ни одного."""

    print(f"\n📤 Батч {batch_num}/{total_batches} — {len(batch_words)} слов, ~{len(prompt)} символов")
    
    body = {
        "model": MODEL,
        "max_tokens": 4096,
        "temperature": 0.1,
        "messages": [{"role": "user", "content": prompt}]
    }
    
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "x-api-key": ANTHROPIC_KEY,
            "anthropic-version": "2023-06-01"
        }
    )
    
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read())
                content = data["content"][0]["text"]
                
                # Extract JSON array from response
                content = content.strip()
                if content.startswith("```"):
                    content = content.split("\n", 1)[1]
                    if content.endswith("```"):
                        content = content[:-3]
                
                results = json.loads(content)
                print(f"   ✅ {len(results)} проверено")
                
                # Log cost
                usage = data.get("usage", {})
                cost = (usage.get("input_tokens", 0) * 3.0 / 1_000_000 +
                        usage.get("output_tokens", 0) * 15.0 / 1_000_000)
                print(f"   💰 {usage.get('input_tokens',0)}→{usage.get('output_tokens',0)} токенов, ${cost:.4f}")
                
                return results, cost
        except (urllib.error.HTTPError, json.JSONDecodeError, KeyError) as e:
            print(f"   ⚠️ Попытка {attempt+1}: {e}")
            time.sleep(2 ** attempt)
    
    print(f"   ❌ Батч {batch_num} провален после 3 попыток")
    return [], 0

def save_results(batch_words, results):
    """Save Sonnet results to word_verification table."""
    conn = get_db()
    cur = conn.cursor()
    
    result_map = {r.get('id'): r for r in results}
    batch_id = f"batch-{int(time.time())}"
    
    for w in batch_words:
        r = result_map.get(w['id'], {})
        cur.execute("""
            INSERT INTO word_verification 
            (word_id, headword, sonnet_verdict, sonnet_pos, sonnet_gender, 
             sonnet_number, sonnet_translit, sonnet_translation, sonnet_explanation, batch_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            w['id'], w['headword'],
            r.get('verdict', 'error'),
            r.get('pos'),
            r.get('gender'),
            r.get('number'),
            r.get('translit'),
            r.get('translation'),
            r.get('explanation', ''),
            batch_id
        ))
    
    conn.commit()
    conn.close()

def main():
    print("🔍 Загружаем слова для верификации...")
    words = get_words_to_verify()
    print(f"📋 {len(words)} слов для проверки")
    
    total_batches = (len(words) + BATCH_SIZE - 1) // BATCH_SIZE
    total_cost = 0
    total_fixed = 0
    
    for batch_num in range(total_batches):
        start = batch_num * BATCH_SIZE
        end = min(start + BATCH_SIZE, len(words))
        batch = words[start:end]
        
        results, cost = call_sonnet(batch, batch_num + 1, total_batches)
        total_cost += cost
        
        if results:
            save_results(batch, results)
            fixed = sum(1 for r in results if r.get('verdict') == 'fix')
            total_fixed += fixed
            print(f"   🔧 {fixed} из {len(batch)} требуют исправлений")
        
        if batch_num < total_batches - 1:
            time.sleep(1)  # Rate limit
    
    print(f"\n{'='*50}")
    print(f"✅ Готово! {len(words)} слов проверено")
    print(f"🔧 {total_fixed} требуют исправлений")
    print(f"💰 Общие затраты: ${total_cost:.4f}")
    print(f"📊 Результаты в таблице word_verification")

if __name__ == "__main__":
    main()
