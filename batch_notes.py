import json, os, time, sys
import psycopg2
from anthropic import Anthropic
from dotenv import load_dotenv
load_dotenv()

client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"], timeout=60)
conn = psycopg2.connect(host="127.0.0.1", port=5434, dbname="daber_dict", user="postgres")
conn.autocommit = True
cur = conn.cursor()

cur.execute("SELECT id, headword, pos_slug, gender, number, translit, translation_enriched FROM words WHERE notes IS NULL OR notes = '' ORDER BY id")
words = cur.fetchall()
print(f"Слов без описаний: {len(words)}")
if not words: conn.close(); sys.exit(0)

BATCH = 10
batches = [words[i:i+BATCH] for i in range(0, len(words), BATCH)]
POS = {"noun":"сущ","verb":"глаг","adj":"прил","adv":"нар","prep":"предл","pron":"мест","conj":"союз","num":"числ","intj":"межд","phrase":"фраза"}
total_cost = 0
ok = 0

for bi, batch in enumerate(batches):
    prompt = "Напиши краткое описание (2-3 предложения) для каждого слова иврита на русском языке в стиле словарной статьи. Начинай каждое описание с символа '—'.\n\n"
    for wid, hw, pos, g, n, trl, tenr in batch:
        trans = tenr
        if isinstance(trans, str):
            try: trans = json.loads(trans)
            except: trans = [trans]
        tl = trans if isinstance(trans, list) else [str(trans)] if trans else []
        p = POS.get(pos or "", "")
        gr = {"m":"м","f":"ж","m/f":"м/ж"}.get(g or "", "")
        nr = {"s":"ед","p":"мн"}.get(n or "", "")
        gram = ", ".join(filter(None, [p, gr, nr]))
        prompt += f"{hw} | {', '.join(tl[:3])} | {gram}\n"
    prompt += "\nОписания (строго по порядку, столько же строк):"

    try:
        resp = client.messages.create(
            model="claude-sonnet-4-20250514", max_tokens=2000,
            system="Ты лингвист-гебраист. Пиши кратко, содержательно, на русском.",
            messages=[{"role":"user","content":prompt}],
        )
        descs = [l.strip()[1:].strip() for l in resp.content[0].text.strip().split("\n") if l.strip().startswith("—")]
        c2 = conn.cursor()
        for i, (wid, *_) in enumerate(batch):
            if i < len(descs) and descs[i]:
                c2.execute("UPDATE words SET notes = %s WHERE id = %s", (descs[i], wid))
                ok += 1
        c2.close()
        cost = (resp.usage.input_tokens*3 + resp.usage.output_tokens*15)/1_000_000
        total_cost += cost
        sys.stdout.write(f"  [{bi+1}/{len(batches)}] {len(batch)}w → {len(descs)}d ${cost:.4f}\n")
        sys.stdout.flush()
    except Exception as e:
        sys.stdout.write(f"  [{bi+1}] ERR: {e}\n")
        sys.stdout.flush()
        time.sleep(2)
    time.sleep(0.2)

cur.execute("SELECT COUNT(*) FROM words WHERE notes IS NULL OR notes = ''")
rem = cur.fetchone()[0]
print(f"\nОбновлено: {ok}, осталось: {rem}, затраты: ${total_cost:.4f}")
conn.close()
