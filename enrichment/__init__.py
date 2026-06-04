"""
Daber enrichment pipeline.

Fetches Hebrew texts from multiple sources, extracts unknown words via LLM,
inserts them into pending_words for manual review.

Usage:
  python3 -m enrichment.run
  python3 -m enrichment.run --source reddit --limit 5
"""
