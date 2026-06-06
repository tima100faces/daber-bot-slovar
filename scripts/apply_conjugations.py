#!/usr/bin/env python3
"""Apply conjugation_ru batch to verbs table.
Usage: python3 apply_conjugations.py /tmp/conj_batch_N.json
Input JSON: [{"verb_id": N, "conjugation_ru": {...}}, ...]
"""
import sys, json, psycopg2

data = json.load(open(sys.argv[1], encoding='utf-8'))
conn = psycopg2.connect(host='127.0.0.1', port=5434, dbname='daber_dict', user='postgres')
cur = conn.cursor()
count = 0
for item in data:
    cur.execute(
        "UPDATE verbs SET conjugation_ru = %s::jsonb WHERE id = %s AND conjugation_ru IS NULL",
        (json.dumps(item['conjugation_ru'], ensure_ascii=False), item['verb_id'])
    )
    count += cur.rowcount
conn.commit()
conn.close()
print(count)
