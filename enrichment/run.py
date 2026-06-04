#!/usr/bin/env python3
"""
Daily enrichment runner — fetch from all sources, extract new words, insert into pending_words.

Cron: 0 6 * * * cd /root/daber-dict && python3 enrichment/run.py

Sources: RSS (Ynet, Walla, Israel Hayom) + Reddit (via BrightData)
LLM: Gemini 2.5 Flash
Output: pending_words in PostgreSQL

Requires: GOOGLE_API_KEY in /root/daber-dict/.env, BrightData key at ~/.brightdata_key
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from enrichment.sources import fetch_all_sources, fetch_rss, fetch_reddit, fetch_reddit_comments, fetch_telegram, RSS_FEEDS, REDDIT_SUBREDDITS, TELEGRAM_CHANNELS, TELEGRAM_SHOPPING, _hebrew_ratio
from enrichment.pipeline import process_text


def main():
    print("=" * 60)
    print("Daber Enrichment Pipeline")
    print(f"Started: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # Check if paused
    try:
        import psycopg2
        conn = psycopg2.connect(
            host="127.0.0.1", port=5434, dbname="daber_dict", user="postgres"
        )
        cur = conn.cursor()
        cur.execute("SELECT value FROM enrichment_settings WHERE key = 'paused'")
        row = cur.fetchone()
        conn.close()
        if row and row[0] == 'true':
            print("\n⏸ Enrichment is PAUSED. Skipping run.")
            print("   Resume via admin panel: /admin → Затраты → toggle.")
            return
    except Exception as e:
        print(f"  ⚠ Could not check pause state: {e} — continuing anyway")

    # 1. Fetch texts from all sources
    print("\n── Fetching sources ──")
    all_texts = []

    # RSS news (reliable, open)
    for name, url in RSS_FEEDS:
        try:
            articles = fetch_rss(name, url, limit=5)
            all_texts.extend(articles)
        except Exception as e:
            print(f"  ✗ RSS {name}: {e}")
        time.sleep(1)

    # Reddit via BrightData (bypasses 403)
    for sub in REDDIT_SUBREDDITS:
        try:
            posts = fetch_reddit(sub, limit=10)
            all_texts.extend(posts)
        except Exception as e:
            print(f"  ✗ Reddit r/{sub}: {e}")

    # Telegram — public Israeli channels (conversational/news, no API key)
    for channel in TELEGRAM_CHANNELS:
        try:
            msgs = fetch_telegram(channel, limit=10)
            all_texts.extend(msgs)
        except Exception as e:
            print(f"  ✗ Telegram @{channel}: {e}")

    # Telegram shopping — commercial/everyday Hebrew (lower Hebrew threshold)
    for channel, ratio in TELEGRAM_SHOPPING:
        try:
            msgs = fetch_telegram(channel, limit=10, min_hebrew_ratio=ratio)
            all_texts.extend(msgs)
        except Exception as e:
            print(f"  ✗ Telegram @{channel}: {e}")

    print(f"\n  Total texts: {len(all_texts)}")

    if not all_texts:
        print("  No texts collected — aborting.")
        return

    # 2. Process each text through LLM
    total_extracted = 0
    total_new = 0
    total_inserted = 0
    errors = 0

    print("\n── Processing via LLM ──")
    for i, item in enumerate(all_texts):
        print(f"\n[{i+1}/{len(all_texts)}] {item['source']}: {item['title'][:70]}")
        try:
            result = process_text(item["text"], item["source"])
            total_extracted += result.get("extracted", 0)
            total_new += result.get("new", 0)
            total_inserted += result.get("inserted", 0)
            if "error" in result:
                errors += 1
        except Exception as e:
            print(f"  ✗ Exception: {e}")
            errors += 1
        time.sleep(1)  # Rate limit for Gemini free tier

    # 3. Report
    print("\n" + "=" * 60)
    print("SUMMARY")
    print(f"  Texts processed: {len(all_texts)}")
    print(f"  Words extracted: {total_extracted}")
    print(f"  New (not in dict): {total_new}")
    print(f"  Inserted into pending: {total_inserted}")
    print(f"  Errors: {errors}")
    print(f"  Finished: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)


if __name__ == "__main__":
    main()
