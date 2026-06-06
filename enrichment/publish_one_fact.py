#!/usr/bin/env python3
"""Publish one draft fact per day for steady SEO content flow."""

import os
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


def main():
    conn = psycopg2.connect(**PG)
    cur = conn.cursor()

    # Pick the oldest draft
    cur.execute(
        """SELECT id, title FROM language_facts
           WHERE is_published = false
           ORDER BY id ASC LIMIT 1"""
    )
    row = cur.fetchone()

    if not row:
        print("Черновиков нет — нечего публиковать.")
        conn.close()
        return

    fid, title = row
    cur.execute(
        """UPDATE language_facts
           SET is_published = true, published_at = NOW()
           WHERE id = %s""",
        (fid,),
    )
    conn.commit()

    cur.execute("SELECT COUNT(*) FROM language_facts WHERE is_published = false")
    remaining = cur.fetchone()[0]

    print(f"Опубликован: #{fid} «{title}»")
    print(f"Осталось черновиков: {remaining}")

    conn.close()


if __name__ == "__main__":
    main()
