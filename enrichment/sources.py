"""
Sources for enrichment pipeline.

Reddit: r/Israel, r/hebrew — via BrightData Web Unlocker (bypasses 403)
RSS: Ynet, Haaretz, Walla, Mako — feedparser

Returns: list of dicts with {text, source, title, url}
"""

import json
import re
import socket
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional

# Belt-and-suspenders: cap every network op (including feedparser's own internal
# fetch, which otherwise has NO timeout) so a stalled source can never hang the
# whole enrichment run — this caused a run to block for >1h on a dead RSS host.
socket.setdefaulttimeout(45)


# ── BrightData ───────────────────────────────────────────────────────────

def _brightdata_key() -> str:
    """Read BrightData API key from ~/.brightdata_key"""
    keyfile = Path.home() / ".brightdata_key"
    if not keyfile.exists():
        raise FileNotFoundError(f"BrightData key not found at {keyfile}")
    return keyfile.read_text().strip()


def _brightdata_fetch(url: str, zone: str = "il_direct", timeout: int = 30) -> str:
    """
    Fetch a URL through BrightData Web Unlocker.
    Returns raw response body as string.
    """
    api_key = _brightdata_key()
    payload = json.dumps({"zone": zone, "url": url, "format": "raw"}).encode()

    req = urllib.request.Request(
        "https://api.brightdata.com/request",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    resp = urllib.request.urlopen(req, timeout=timeout)
    return resp.read().decode("utf-8")


# ── Reddit ───────────────────────────────────────────────────────────────

REDDIT_SUBREDDITS = ["Israel", "hebrew", "ani_bm"]  # ani_bm = Israeli Hebrew memes/discussion


def fetch_reddit(subreddit: str = "Israel", limit: int = 15) -> list[dict]:
    """
    Fetch recent posts from r/Israel or r/hebrew via BrightData Web Unlocker.
    Returns list of {text, source, title, url}.
    """
    try:
        import feedparser
    except ImportError:
        print("  ✗ feedparser not installed")
        return []

    url = f"https://www.reddit.com/r/{subreddit}/new/.rss?limit={limit}"

    try:
        raw = _brightdata_fetch(url, zone="il_direct", timeout=30)
        feed = feedparser.parse(raw)
    except Exception as e:
        print(f"  ✗ Reddit r/{subreddit}: {e}")
        return []

    results = []
    for entry in feed.entries[:limit]:
        title = entry.get("title", "")
        summary = entry.get("summary", "")
        link = entry.get("link", "")

        # Clean HTML from summary
        summary = re.sub(r"<[^>]+>", " ", summary)
        summary = re.sub(r"\s+", " ", summary).strip()

        full_text = f"{title}. {summary}".strip()
        if len(full_text) < 80:
            continue

        hebrew_ratio = _hebrew_ratio(full_text[:300])
        if hebrew_ratio < 0.1:  # Reddit posts often have English titles + Hebrew body
            continue

        results.append({
            "text": full_text,
            "source": f"reddit/r/{subreddit}",
            "title": title,
            "url": link,
        })

    print(f"  ✓ Reddit r/{subreddit}: {len(results)} posts (via BrightData)")
    return results


def fetch_reddit_comments(subreddit: str = "ani_bm", limit: int = 10) -> list[dict]:
    """
    Fetch Reddit comments (not just titles/summaries) for conversational Hebrew.
    Reddit JSON API: free, no auth needed for read-only.
    Returns list of {text, source, title, url}.
    """
    try:
        import feedparser
    except ImportError:
        print("  ✗ feedparser not installed")
        return []
    
    # Step 1: Get post IDs from RSS
    rss_url = f"https://www.reddit.com/r/{subreddit}/new/.rss?limit={limit}"
    try:
        raw = _brightdata_fetch(rss_url, zone="il_direct", timeout=30)
        feed = feedparser.parse(raw)
    except Exception as e:
        print(f"  ✗ Reddit comments r/{subreddit}: {e}")
        return []
    
    results = []
    posts_checked = 0
    
    for entry in feed.entries[:limit]:
        link = entry.get("link", "")
        # Extract post ID from link: /comments/abc123/
        post_id_match = re.search(r'/comments/([a-z0-9]+)/', link)
        if not post_id_match:
            continue
        post_id = post_id_match.group(1)
        posts_checked += 1
        
        # Step 2: Fetch comments via JSON API
        comments_url = f"https://www.reddit.com/r/{subreddit}/comments/{post_id}.json?limit=15"
        try:
            raw_json = _brightdata_fetch(comments_url, zone="il_direct", timeout=30)
            data = json.loads(raw_json)
        except Exception:
            continue
        
        # Reddit JSON: [post_data, comments_data]
        if not isinstance(data, list) or len(data) < 2:
            continue
        
        comments_list = data[1].get("data", {}).get("children", [])
        comment_texts = []
        
        for child in comments_list[:10]:
            if child.get("kind") != "t1":  # t1 = comment
                continue
            body = child.get("data", {}).get("body", "")
            # Clean markdown
            body = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', body)  # links
            body = re.sub(r'\*+', '', body)  # bold/italic
            body = re.sub(r'>.*', '', body)  # quotes
            body = re.sub(r'\n+', '. ', body).strip()
            if len(body) < 20:
                continue
            hebrew_ratio = _hebrew_ratio(body[:200])
            if hebrew_ratio < 0.15:  # Comments can have mixed EN/HE
                continue
            comment_texts.append(body)
        
        if comment_texts:
            combined = ". ".join(comment_texts)
            results.append({
                "text": combined[:2000],
                "source": f"reddit/r/{subreddit}/comments",
                "title": entry.get("title", "")[:80],
                "url": link,
            })
    
    print(f"  ✓ Reddit r/{subreddit} comments: {len(results)} threads ({posts_checked} posts checked)")
    return results


# ── RSS News ─────────────────────────────────────────────────────────────

RSS_FEEDS = [
    ("Ynet", "https://www.ynet.co.il/Integration/StoryRss2.xml"),
    ("Haaretz", "https://www.haaretz.co.il/cmlink/1.8092176"),
    ("Walla", "https://rss.walla.co.il/feed/1"),
    ("Mako", "https://www.mako.co.il/rss/news"),
    ("Israel Hayom", "https://www.israelhayom.co.il/rss.xml"),
    # Government / legal
    ("Nevo — Legal DB", "https://www.nevo.co.il/rss/Padi.ashx"),  # Israeli legal database: court rulings, legislation
]


def fetch_rss(feed_name: str, feed_url: str, limit: int = 5) -> list[dict]:
    """
    Fetch recent articles from an RSS feed.
    Returns list of {text, source, title, url}.
    """
    try:
        import feedparser
    except ImportError:
        print("  ✗ feedparser not installed. Run: pip3 install feedparser")
        return []

    try:
        # Fetch with an explicit timeout, then parse bytes — feedparser.parse(url)
        # does its own fetch with NO timeout and can hang indefinitely.
        req = urllib.request.Request(feed_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
        feed = feedparser.parse(raw)
    except Exception as e:
        print(f"  ✗ RSS {feed_name}: {e}")
        return []

    results = []
    for entry in feed.entries[:limit]:
        title = entry.get("title", "")
        summary = entry.get("summary", "")
        link = entry.get("link", "")

        # Clean HTML from summary
        summary = re.sub(r"<[^>]+>", " ", summary)
        summary = re.sub(r"\s+", " ", summary).strip()

        full_text = f"{title}. {summary}".strip()
        if len(full_text) < 80:
            continue

        # Filter: Hebrew content only
        hebrew_ratio = _hebrew_ratio(full_text[:300])
        if hebrew_ratio < 0.3:
            continue

        results.append({
            "text": full_text,
            "source": f"rss/{feed_name}",
            "title": title,
            "url": link,
        })

    print(f"  ✓ RSS {feed_name}: {len(results)} articles")
    return results


# ── Telegram ──────────────────────────────────────────────────────────────

# Public Israeli Telegram channels (accessible via t.me/s/ without login)
TELEGRAM_CHANNELS = [
    ("rotternews", 0.25),          # Rotter.net — новости и обсуждения
    ("kikar_shabbat", 0.25),       # כיכר השבת — новости, много иврита
]

# Commercial / everyday Hebrew channels (lower threshold — diluted by emoji/links)
TELEGRAM_SHOPPING = [
    ("kspcoil", 0.03),             # KSP — компьютеры и электроника
    ("superpharmil", 0.03),        # סופר-פארם — аптека, товары для дома
    ("dilimshavima", 0.03),        # דילים שווים — скидки и акции
    ("haregakaniti", 0.03),        # הרגע קניתי — покупки, AliExpress
]


def fetch_telegram(channel: str, limit: int = 15, min_hebrew_ratio: float = 0.25) -> list[dict]:
    """
    Fetch recent messages from a public Telegram channel via t.me/s/ preview.
    No API key required — scrapes the public HTML preview page.
    
    min_hebrew_ratio: minimum fraction of Hebrew characters required.
        News channels: 0.25, commercial/shopping: 0.03 (diluted by emoji).
    Returns list of {text, source, title, url}.
    """
    url = f"https://t.me/s/{channel}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            html = resp.read().decode("utf-8")
    except Exception as e:
        print(f"  ✗ Telegram @{channel}: {e}")
        return []

    # Extract message text from HTML
    # Messages are in <div class="tgme_widget_message_text ..."> blocks
    messages = re.findall(r'<div class="tgme_widget_message_text[^"]*"[^>]*dir="auto">(.*?)</div>', html, re.DOTALL)
    
    results = []
    for msg in messages[:limit]:
        # Clean HTML tags and entities
        clean = re.sub(r'<[^>]+>', ' ', msg)
        clean = re.sub(r'&[a-z]+;', ' ', clean)
        clean = re.sub(r'&#\d+;', '', clean)  # HTML entities
        # Remove emoji and special chars, keep Hebrew, Cyrillic, Latin, punctuation
        clean = re.sub(r'[^\w\s\u0590-\u05FF\u0400-\u04FF.,!?:;()\-\"]+', ' ', clean)
        clean = re.sub(r'\s+', ' ', clean).strip()
        
        if len(clean) < 30:
            continue
        
        hebrew_ratio = _hebrew_ratio(clean[:300])
        if hebrew_ratio < min_hebrew_ratio:  # Shopping channels use 0.03
            continue
        
        results.append({
            "text": clean[:2000],
            "source": f"telegram/@{channel}",
            "title": f"@{channel}",
            "url": f"https://t.me/s/{channel}",
        })

    print(f"  ✓ Telegram @{channel}: {len(results)} messages")
    return results


# ── Helpers ──────────────────────────────────────────────────────────────

def _hebrew_ratio(text: str) -> float:
    """Ratio of Hebrew characters in text."""
    if not text:
        return 0.0
    hebrew_chars = sum(1 for c in text if "\u0590" <= c <= "\u05ff")
    total = len(text.strip())
    return hebrew_chars / max(total, 1)


def fetch_all_sources(limit: int = 15) -> list[dict]:
    """Fetch from all configured sources. Returns combined list."""
    all_texts = []

    # Reddit
    for sub in REDDIT_SUBREDDITS:
        try:
            posts = fetch_reddit(sub, limit=limit)
            all_texts.extend(posts)
        except Exception as e:
            print(f"  ✗ Reddit r/{sub} failed: {e}")
        time.sleep(1)  # Be nice to Reddit API

    # RSS
    try:
        import feedparser
        for name, url in RSS_FEEDS:
            try:
                articles = fetch_rss(name, url, limit=5)
                all_texts.extend(articles)
            except Exception as e:
                print(f"  ✗ RSS {name} failed: {e}")
            time.sleep(1)
    except ImportError:
        print("  ⚠ feedparser not installed — skipping RSS")

    return all_texts


# ── CLI test ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="all", choices=["all", "reddit", "rss"])
    ap.add_argument("--limit", type=int, default=10)
    args = ap.parse_args()

    if args.source in ("all", "reddit"):
        print("=== Reddit ===\n")
        for item in fetch_reddit(limit=args.limit):
            print(f"[{item['source']}] {item['title']}")
            print(f"  Hebrew ratio: {_hebrew_ratio(item['text']):.0%}")
            print(f"  {item['text'][:200]}...")
            print()

    if args.source in ("all", "rss"):
        print("=== RSS ===\n")
        import feedparser
        for name, url in RSS_FEEDS:
            for item in fetch_rss(name, url, limit=3):
                print(f"[{item['source']}] {item['title']}")
                print(f"  {item['text'][:200]}...")
                print()
