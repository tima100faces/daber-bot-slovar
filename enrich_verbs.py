#!/usr/bin/env python3
"""Enrich 140 new verbs via Anthropic Sonnet — match existing enrichment format."""
import os, sys, json, time, base64, urllib.request, psycopg2, psycopg2.extras

with open('/tmp/ak_b64') as f:
    API_KEY = base64.b64decode(f.read().strip()).decode()

PG = dict(
    host='127.0.0.1', port=5434, dbname='daber_dict', user='postgres'
)

conn = psycopg2.connect(**PG)
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

cur.execute("""
    SELECT id, pealim_slug, infinitive_he, translation_ru, binyan, root
    FROM verbs 
    WHERE id > 4608 AND (translation_enriched IS NULL)
    ORDER BY id
""")
verbs = cur.fetchall()
print(f'Verbs to enrich: {len(verbs)}')

PROMPT = """Ты — эксперт по ивриту и русскому языку. Для глагола иврита дай enrichment на русском в JSON.

Глагол: {infinitive} ({binyan}, корень {root})
Базовый перевод: {translation}

Верни ТОЛЬКО JSON (без markdown, без ```):
{{
  "translation_enriched": "расширенные русские переводы через запятую, синонимы, уточнения",
  "examples": [
    {{"hebrew": "предложение на иврите с этим глаголом", "translation": "перевод на русский"}}
  ],
  "synonyms": ["глагол1 (перевод1)", "глагол2 (перевод2)"],
  "notes": "1-2 предложения о биньяне, особенностях спряжения, употреблении. На русском."
}}

Правила:
- translation_enriched: строка через запятую, расширяющая базовый перевод. Для пассивных глаголов (pual/hufal) — "пассив от [активный глагол] — [значение]".
- examples: 3-5 предложений
- synonyms: 2-3 похожих глагола иврита с переводом в скобках
- notes: кратко, 1-2 предложения"""

def call_sonnet(prompt):
    data = json.dumps({
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": prompt}]
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=data,
        headers={
            "Content-Type": "application/json",
            "x-api-key": API_KEY,
            "anthropic-version": "2023-06-01"
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f'API: {e}', end=' ')
        return None

enriched_count = 0
failed = []

for i, v in enumerate(verbs):
    slug = v['pealim_slug']
    infinitive = v['infinitive_he'] or '(нет)'
    translation = v['translation_ru'] or '(пассив)'
    binyan = v['binyan'] or '?'
    root = v['root'] or '?'
    
    prompt = PROMPT.format(
        infinitive=infinitive, binyan=binyan, root=root, translation=translation
    )
    
    print(f'[{i+1}/{len(verbs)}] {slug} ... ', end='', flush=True)
    
    result = call_sonnet(prompt)
    if not result:
        failed.append(slug)
        print('FAIL')
        continue
    
    try:
        text = result['content'][0]['text']
        text = text.strip()
        if text.startswith('```'):
            text = text.split('\n', 1)[1]
            if text.endswith('```'):
                text = text[:-3]
        enrichment = json.loads(text)
    except (KeyError, json.JSONDecodeError) as e:
        print(f'PARSE: {e}')
        failed.append(slug)
        continue
    
    trans_enriched = enrichment.get('translation_enriched', '')
    notes = enrichment.get('notes', '')
    examples = enrichment.get('examples', [])
    synonyms = enrichment.get('synonyms', [])
    
    try:
        # Store translation_enriched as JSONB string (matching existing format)
        cur.execute("""
            UPDATE verbs 
            SET translation_enriched = %s::jsonb, notes = %s, enriched_at = NOW()
            WHERE id = %s
        """, (json.dumps(trans_enriched, ensure_ascii=False), notes, v['id']))
        
        for ex in examples[:5]:
            cur.execute("""
                INSERT INTO verb_examples (verb_id, hebrew, translation, source)
                VALUES (%s, %s, %s, 'sonnet')
            """, (v['id'], ex.get('hebrew', ''), ex.get('translation', '')))
        
        for s in synonyms[:3]:
            cur.execute("""
                INSERT INTO verb_synonyms (verb_id, hebrew, translation, source)
                VALUES (%s, %s, %s, 'sonnet')
            """, (v['id'], s, ''))
        
        conn.commit()
        enriched_count += 1
        print('OK')
    except Exception as e:
        conn.rollback()
        print(f'DB: {e}')
        failed.append(slug)
    
    time.sleep(0.25)  # rate limit

cur.close()
conn.close()

print(f'\nDone! Enriched: {enriched_count}, Failed: {len(failed)}')
if failed:
    print('Failed:')
    for s in failed:
        print(f'  {s}')
