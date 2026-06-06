#!/usr/bin/env python3
"""Balashon fact generator — fetches latest posts from Balashon RSS,
generates facts via Sonnet, inserts as drafts into language_facts."""

import json
import os
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from html import unescape
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

PG = dict(
    host=os.environ.get("PGHOST", "127.0.0.1"),
    port=int(os.environ.get("PGPORT", "5434")),
    dbname=os.environ.get("PGDB", "daber_dict"),
    user=os.environ.get("PGUSER", "postgres"),
)
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY")
RSS_URL = "https://www.balashon.com/feeds/posts/default?max-results=10"
STATE_FILE = Path(__file__).parent / ".balashon_processed.txt"


def get_processed_urls() -> set:
    """Return set of already-processed post URLs."""
    if STATE_FILE.exists():
        return set(STATE_FILE.read_text().splitlines())
    return set()


def save_processed_url(url: str):
    with open(STATE_FILE, "a") as f:
        f.write(url + "\n")


def strip_html(text: str) -> str:
    """Remove HTML tags, decode entities."""
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def fetch_new_posts() -> list[dict]:
    """Fetch RSS feed, return new (unprocessed) posts."""
    req = urllib.request.Request(
        RSS_URL,
        headers={"User-Agent": "DaberDict/1.0 (+https://slovar.daber.me)"},
    )
    resp = urllib.request.urlopen(req, timeout=30)
    tree = ET.fromstring(resp.read())

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    processed = get_processed_urls()
    new_posts = []

    for entry in tree.findall("atom:entry", ns):
        title_el = entry.find("atom:title", ns)
        content_el = entry.find("atom:content", ns)

        # Get the alternate (actual post) URL, not reply/comment links
        url = ""
        for link in entry.findall("atom:link", ns):
            rel = link.get("rel", "alternate")
            if rel == "alternate":
                url = link.get("href", "")
                break
        # Fallback to first link
        if not url:
            first_link = entry.find("atom:link", ns)
            url = first_link.get("href", "") if first_link is not None else ""

        if not url or url in processed:
            continue

        title = title_el.text if title_el is not None else "Untitled"
        html_content = content_el.text if content_el is not None else ""
        text_content = strip_html(html_content)

        # Skip if too short (probably not a full article)
        if len(text_content) < 500:
            save_processed_url(url)
            continue

        new_posts.append(
            {
                "url": url,
                "title": title,
                "content": text_content[:8000],  # Truncate for Sonnet
            }
        )

    return new_posts


def generate_facts(post: dict, existing_titles: list[str]) -> list[dict]:
    """Send article to Sonnet, get back 2-3 facts."""
    blocked = ""
    if existing_titles:
        top = existing_titles[:30]
        blocked = "\n".join(f"- {t}" for t in top)
        blocked = (
            f"\n\nЗАПРЕЩЕНО повторять эти темы (уже есть в блоге):\n{blocked}\n"
        )

    prompt = f"""Ты — редактор блога об иврите. Извлеки 2-3 САМЫХ интересных факта из статьи.

ВАЖНО:
- Только факты ИЗ статьи, не придумывай
- Каждый факт — законченная мысль на русском
- Пиши живым языком
- Title должен содержать «иврит» или заканчиваться на « — факт об иврите»
- Разнообразь форматы: did_you_know, etymology, story, comparison{blocked}

Источник: {post['url']}
Заголовок: {post['title']}

СТАТЬЯ:
{post['content']}

Верни ТОЛЬКО JSON-массив:
[
  {{
    "fact_type": "etymology",
    "title": "...",
    "fact_body": "...",
    "source_url": "{post['url']}"
  }},
  ...
]"""

    payload = json.dumps(
        {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 2048,
            "temperature": 0.7,
            "messages": [{"role": "user", "content": prompt}],
        }
    ).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": ANTHROPIC_KEY,
            "anthropic-version": "2023-06-01",
        },
    )

    resp = urllib.request.urlopen(req, timeout=120)
    body = json.loads(resp.read())
    text = body["content"][0]["text"]

    # Extract JSON array
    json_match = re.search(r"\[.*\]", text, re.DOTALL)
    if not json_match:
        print(f"  No JSON in response for {post['title']}")
        return []

    try:
        return json.loads(json_match.group())
    except json.JSONDecodeError:
        print(f"  JSON parse error for {post['title']}")
        return []


def get_existing_titles() -> list[str]:
    """Return published fact titles to avoid duplicates."""
    try:
        conn = psycopg2.connect(**PG)
        cur = conn.cursor()
        cur.execute("SELECT title FROM language_facts WHERE is_published = true")
        titles = [r[0] for r in cur.fetchall()]
        conn.close()
        return titles
    except Exception:
        return []


def insert_facts(facts: list[dict]) -> int:
    """Insert facts as drafts. Return count inserted (skip duplicates by title)."""
    conn = psycopg2.connect(**PG)
    cur = conn.cursor()
    
    # Get existing titles to skip duplicates
    cur.execute("SELECT title FROM language_facts")
    existing = {r[0] for r in cur.fetchall()}
    
    count = 0
    for f in facts:
        if not f.get("title") or not f.get("fact_body"):
            continue
        if f["title"] in existing:
            continue
        cur.execute(
            """INSERT INTO language_facts (fact_type, title, fact_body, source_url, is_published)
               VALUES (%s, %s, %s, %s, false)""",
            (
                f.get("fact_type", "did_you_know"),
                f["title"],
                f["fact_body"],
                f.get("source_url"),
            ),
        )
        count += 1
        existing.add(f["title"])
    conn.commit()
    conn.close()
    return count


def main():
    if not ANTHROPIC_KEY:
        print("ERROR: ANTHROPIC_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] Balashon fact generator")

    # Fetch new posts
    try:
        posts = fetch_new_posts()
    except Exception as e:
        print(f"ERROR fetching RSS: {e}", file=sys.stderr)
        sys.exit(1)

    if not posts:
        print("No new posts to process")
        return

    print(f"New posts found: {len(posts)}")
    existing_titles = get_existing_titles()
    print(f"Existing published facts: {len(existing_titles)}")

    total_facts = 0
    for post in posts:
        print(f"\nProcessing: {post['title']}")
        print(f"  URL: {post['url']}")
        print(f"  Content: {len(post['content'])} chars")

        try:
            facts = generate_facts(post, existing_titles)
            print(f"  Generated {len(facts)} facts")
            count = insert_facts(facts)
            print(f"  Inserted {count} new draft(s)")
            total_facts += count
            # Add titles to existing to avoid intra-batch duplicates
            existing_titles.extend(f["title"] for f in facts)
        except Exception as e:
            print(f"  ERROR: {e}")
            # Still mark as processed to avoid infinite retry
            save_processed_url(post["url"])
            continue

        save_processed_url(post["url"])
        time.sleep(2)  # Rate limit

    print(f"\nDone! {total_facts} new draft facts from {len(posts)} posts")


if __name__ == "__main__":
    main()
