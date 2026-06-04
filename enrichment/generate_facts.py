"""Generate Hebrew language facts from source material using Claude Sonnet.

Reads enrichment/sources_raw.md, sends to Anthropic API, inserts facts into
language_facts table (unpublished — admin reviews before publishing).
"""

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

PG = dict(
    host=os.environ.get("PGHOST", "127.0.0.1"),
    port=int(os.environ.get("PGPORT", "5434")),
    dbname=os.environ.get("PGDB", "daber_dict"),
    user=os.environ.get("PGUSER", "postgres"),
)

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY")

PROMPT = """Ты — редактор образовательного блога об иврите. На основе предоставленных источников создай 8-12 интересных фактов на русском языке.

Для каждого факта укажи:
- fact_type: один из "did_you_know", "story", "etymology", "comparison"
- title: заголовок на русском (до 80 символов)
- fact_body: текст факта (2-5 предложений, живой язык, как для блога)
- source_url: URL источника (если есть в предоставленном тексте)

ТРЕБОВАНИЯ:
- НЕ придумывай факты, которых нет в источниках
- Переписывай своими словами, но сохраняй фактические данные
- Пиши увлекательно, как для широкой аудитории
- Разнообразь форматы: мини-истории, "а вы знали", этимологии, сравнения
- Для каждого факта укажи source_url из того источника, откуда взят материал
- Если в источнике нет URL, оставь source_url пустым

Верни ТОЛЬКО JSON-массив:
[
  {
    "fact_type": "did_you_know",
    "title": "...",
    "fact_body": "...",
    "source_url": "..."
  },
  ...
]

Никакого текста до или после JSON."""


def call_sonnet(source_text: str) -> list[dict]:
    """Send source text to Claude Sonnet, get back facts."""
    import urllib.request

    payload = json.dumps({
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 4096,
        "temperature": 0.7,
        "messages": [
            {"role": "user", "content": f"{PROMPT}\n\nИСТОЧНИКИ:\n\n{source_text[:12000]}"}
        ]
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": ANTHROPIC_KEY,
            "anthropic-version": "2023-06-01",
        }
    )

    resp = urllib.request.urlopen(req, timeout=120)
    body = json.loads(resp.read())
    content = body["content"][0]["text"]

    # Extract JSON array
    json_match = re.search(r'\[.*\]', content, re.DOTALL)
    if not json_match:
        print("ERROR: No JSON array in response")
        print(content[:500])
        return []

    try:
        facts = json.loads(json_match.group())
        return facts
    except json.JSONDecodeError as e:
        print(f"ERROR: JSON parse failed: {e}")
        print(json_match.group()[:500])
        return []


def insert_facts(facts: list[dict]) -> int:
    """Insert facts into DB, return count inserted."""
    conn = psycopg2.connect(**PG)
    cur = conn.cursor()
    count = 0
    for f in facts:
        if not f.get("title") or not f.get("fact_body"):
            continue
        cur.execute("""
            INSERT INTO language_facts (fact_type, title, fact_body, source_url, is_published)
            VALUES (%s, %s, %s, %s, false)
        """, (
            f.get("fact_type", "did_you_know"),
            f["title"],
            f["fact_body"],
            f.get("source_url"),
        ))
        count += 1
    conn.commit()
    conn.close()
    return count


def main():
    source_file = Path(__file__).parent / "sources_raw.md"
    if not source_file.exists():
        print(f"ERROR: {source_file} not found")
        sys.exit(1)

    source_text = source_file.read_text(encoding="utf-8")
    print(f"Source: {len(source_text)} chars")

    if not ANTHROPIC_KEY:
        print("ERROR: ANTHROPIC_API_KEY not set")
        sys.exit(1)

    print("Calling Claude Sonnet...")
    facts = call_sonnet(source_text)
    print(f"Got {len(facts)} facts from Sonnet")

    if not facts:
        print("No facts generated")
        sys.exit(1)

    count = insert_facts(facts)
    print(f"Inserted {count} facts into DB (unpublished — review in admin)")

    # Print preview
    for i, f in enumerate(facts[:3]):
        print(f"\n--- Fact {i+1} ---")
        print(f"Type: {f.get('fact_type', '?')}")
        print(f"Title: {f.get('title', '?')}")
        print(f"Body: {f.get('fact_body', '?')[:200]}...")


if __name__ == "__main__":
    main()
