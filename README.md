# DABER (דבר) — Hebrew-Russian Dictionary

**Domain:** [slovar.daber.me](https://slovar.daber.me)
**Server IP:** 172.18.0.1 (internal) / Cloudflare-proxied externally
**GitHub:** [tima100faces/daber-bot-slovar](https://github.com/tima100faces/daber-bot-slovar)

---

## Stack

- **Backend:** Python 3.11 + FastAPI (uvicorn)
- **Database:** PostgreSQL 16 on port `5434`
- **Frontend:** Static HTML/CSS/JS (served by nginx)
- **Reverse proxy:** nginx on port 443 (Cloudflare SSL)
- **Process manager:** systemd (`daber-dict.service`)
- **Python venv:** `/usr/local/lib/hermes-agent/venv/`

---

## Directory structure

```
/root/daber-dict/
├── main.py              # FastAPI backend (all API endpoints)
├── schema.sql           # Full DB dump (for restore)
├── requirements.txt     # Python deps
├── .env                 # Environment (PG conn, API keys, TOTP secret)
├── static/              # Frontend (served directly by nginx)
│   ├── index.html       # Main dictionary page
│   ├── admin/           # Admin panel pages
│   ├── fonts/           # Arimo, Inter, JetBrains Mono (subset)
│   ├── icons/           # Tabler SVG icons
│   ├── components.css   # Shared CSS
│   └── design-system.css
├── enrichment/          # Facts generation pipeline
│   ├── run.py           # Cron-triggered enrichment entry point
│   ├── pipeline.py      # Gemini-based enrichment
│   ├── publish_one_fact.py
│   ├── balashon_facts.py
│   └── verify_words.py
├── scripts/
│   ├── daber-backup.sh  # Daily pg_dump (cron)
│   └── daber-dict-push.sh  # Auto git push (cron)
├── backups/             # PostgreSQL dumps (daily rotation)
└── enrich_verbs.py      # Sonnet-based verb enrichment (one-shot, ran 2026-06-06)
```

---

## Services & Ports

| Service | Port | Host | systemd unit |
|---------|------|------|-------------|
| FastAPI backend | `8090` | 127.0.0.1 only | `daber-dict.service` |
| PostgreSQL | `5434` | 127.0.0.1 only | `postgresql.service` |
| nginx (HTTPS) | `443` | All interfaces | `nginx.service` |

### nginx config

Located at `/etc/nginx/sites-available/slovar.daber.me` (symlinked to `sites-enabled`).

- `/static/` → served directly from `/root/daber-dict/static/`
- `/api/` → proxied to `http://127.0.0.1:8090/api/` (rate-limited: 10 req/s)
- `/admin` → proxied to `http://127.0.0.1:8090/admin`
- `/` → static files from `/root/daber-dict/static/` (SPA fallback)

SSL via Let's Encrypt (certbot), auto-renewal.

---

## Database

**Name:** `daber_dict`
**User:** `postgres` (trust auth on localhost)
**Tables (19):**

| Table | Rows (approx) | Description |
|-------|--------------|-------------|
| `words` | 8,048 | Dictionary words (IRIS source) |
| `verbs` | 4,748 | Hebrew verbs (Pealim source) |
| `verb_forms` | 126,051 | Conjugated verb forms |
| `verb_examples` | — | LLM-generated example sentences |
| `verb_synonyms` | — | LLM-generated synonyms |
| `verb_senses` | — | (unused) |
| `word_examples` | — | Word examples |
| `word_synonyms` | — | Word synonyms |
| `word_phrases` | — | Word phrases |
| `word_forms` | — | Word inflected forms |
| `word_frequencies` | — | Frequency data |
| `language_facts` | 122 | Published language facts (blog) |
| `word_verification` | — | Pending word verification queue |
| `pending_words` | — | User-submitted words |
| `user_feedback` | — | User error reports |
| `contact_messages` | — | Contact form submissions |
| `verb_audio` | — | TTS audio cache |
| `enrichment_costs` | — | AI enrichment cost tracking |
| `enrichment_settings` | — | Enrichment config |

### Connect

```bash
psql -h 127.0.0.1 -p 5434 -U postgres -d daber_dict
```

### Backup

Daily dump at 3:00 via cron → `/root/daber-dict/backups/daber_YYYYMMDD_0300.dump`

Manual backup:
```bash
cd /root/daber-dict
./backup.sh
```

Restore:
```bash
psql -h 127.0.0.1 -p 5434 -U postgres -d daber_dict < backups/daber_YYYYMMDD.dump
```

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/search?q=...&limit=20` | Full-text search (Hebrew/Russian) |
| GET | `/api/word/{headword}?type=verb` | Single verb detail |
| GET | `/api/word/{headword}?type=word` | Single word detail |
| GET | `/api/verb/{pealim_slug}` | Verb by Pealim slug |
| GET | `/api/letter/{letter}` | Words by first letter |
| GET | `/api/pos/{pos_slug}` | Words by POS |
| GET | `/api/random` | Random word |
| GET | `/api/stats` | Dictionary statistics |
| GET | `/api/facts` | Language facts (blog) |
| POST | `/admin/login` | Admin TOTP login |

---

## Admin Panel

- URL: `slovar.daber.me/admin/login`
- Auth: TOTP (6-digit, secret in `.env` → `ADMIN_TOTP_SECRET`)
- TOTP verify: POST `/admin/verify-totp` → session cookie via `itsdangerous`

---

## Environment (.env)

| Variable | Purpose |
|----------|---------|
| `PGHOST` | PostgreSQL host (`127.0.0.1`) |
| `PGPORT` | PostgreSQL port (`5434`) |
| `PGDB` | Database name (`daber_dict`) |
| `PGUSER` | Database user (`postgres`) |
| `ADMIN_TOTP_SECRET` | TOTP secret for admin login |
| `GOOGLE_API_KEY` | Gemini API key (legacy, used only by pipeline.py) |
| `ANTHROPIC_API_KEY` | Anthropic API key (primary — verb enrichment, verification, facts) |

---

## Enrichment Pipeline

### Verb enrichment (Sonnet)

One-shot script for new verbs: `enrich_verbs.py`
Populates: `verbs.translation_enriched`, `verb_examples`, `verb_synonyms`, `verbs.notes`

### Facts enrichment (Sonnet)

- `generate_facts.py` — generate facts from source material via Sonnet
- `balashon_facts.py` — weekly Balashon blog scraping → facts via Sonnet (cron Mon 9:00)

### Words enrichment (Gemini — legacy)

Cron-triggered pipeline:
- `pipeline.py` / `run.py` → daily word enrichment via Gemini 2.5 Flash (cron 10:00)
- ⚠️ This is the only pipeline still on Gemini. Planned migration to Sonnet.

---

## Cron Jobs (Hermes dev profile)

| Job | Schedule | Script | Description |
|-----|----------|--------|-------------|
| Backup | Daily 3:00 | `scripts/daber-backup.sh` | pg_dump to backups/ |
| Auto-push | Daily | `scripts/daber-dict-push.sh` | git commit + push |
| Facts generation | Daily 10:00 | `enrichment/run.py` | Generate + publish fact |
| Balashon scraping | Mon 9:00 | `enrichment/balashon_facts.py` | Scrape Balashon blog |

---

## Git

- **Remote:** `https://github.com/tima100faces/daber-bot-slovar.git`
- **Branch:** `main`
- **Auth:** GitHub token in remote URL (push via cron)

---

## Quick Commands

```bash
# Restart backend
systemctl restart daber-dict

# Check status
systemctl status daber-dict

# View logs
journalctl -u daber-dict -f

# Reload nginx
systemctl reload nginx

# DB connect
psql -h 127.0.0.1 -p 5434 -U postgres -d daber_dict

# Test search API
curl 'http://127.0.0.1:8090/api/search?q=שלום'

# Test health
curl -I https://slovar.daber.me
```

---

## Development Notes

- Static files are served directly by nginx — no need to restart backend for frontend changes
- Frontend JS is vanilla (no framework), inline in `index.html`
- Admin pages use a shared `_admin.css` + `_core.js` pattern
- CSS design tokens in `design-system.css`, shared components in `components.css`
- The backend Python venv uses system interpreter with deps installed globally (`/usr/local/lib/hermes-agent/venv/`)
- For new dependencies: `pip install <pkg>` (not `uv` or `pipx`)
- All project files must stay inside `/root/daber-dict/` (project isolation rule)
- Git remote token is embedded in the URL — be careful with public sharing
