# DABER (דבר) — Hebrew-Russian Dictionary

**Domain:** [slovar.daber.me](https://slovar.daber.me)
**Server IP:** 172.18.0.1 (internal) / Cloudflare-proxied externally
**GitHub:** [tima100faces/daber-bot-slovar](https://github.com/tima100faces/daber-bot-slovar)

---

## Stack

- **Backend:** Python 3.11 + FastAPI (uvicorn)
- **Database:** PostgreSQL 14 on port `5434` (dedicated instance, separate from the system `14/main` cluster on `5432`)
- **Frontend:** Static HTML/CSS/JS (served by nginx)
- **Reverse proxy:** nginx on port 443 (Cloudflare SSL)
- **Process manager:** systemd (`daber-dict.service`)
- **Python venv:** `/root/daber-dict/.venv/` — **dedicated**, independent of any other project on the host (the Hermes agent and the dictionary no longer share an interpreter). Created/synced automatically by the deploy script from `requirements.txt`.

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
│   ├── pipeline.py      # Sonnet-based enrichment (migrated off Gemini)
│   ├── publish_one_fact.py
│   ├── balashon_facts.py
│   └── verify_words.py
├── scripts/
│   ├── daber-backup.sh       # Daily pg_dump (cron)
│   ├── daber-dict-deploy.sh  # Pull-based deploy (run on server after merge to main)
│   └── daber-dict-push.sh     # RETIRED auto-push — now a harmless no-op stub
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
| `language_facts` | 122 | Language facts (snapshot 06.06.2026: 37 published + 85 drafts) |
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
| `GOOGLE_API_KEY` | Gemini API key (not used in pipeline, kept for future) |
| `ANTHROPIC_API_KEY` | Anthropic API key (primary — all enrichment, verification, facts) |

---

## Enrichment Pipeline

### Verb enrichment (Sonnet)

One-shot script for new verbs: `enrich_verbs.py`
Populates: `verbs.translation_enriched`, `verb_examples`, `verb_synonyms`, `verbs.notes`

### Facts enrichment (Sonnet)

- `generate_facts.py` — generate facts from source material via Sonnet
- `balashon_facts.py` — weekly Balashon blog scraping → facts via Sonnet (cron Mon 9:00)

### Words enrichment (Sonnet)

Cron-triggered pipeline:
- `run.py` → daily word extraction from RSS/Reddit/Telegram → pending_words (cron 10:00)
- `pipeline.py` → LLM extraction + verification + insert

---

## Cron Jobs (Hermes dev profile)

| Job | Schedule | Script | Description |
|-----|----------|--------|-------------|
| Backup | Daily 3:00 | `scripts/daber-backup.sh` | pg_dump to backups/ |
| Facts generation | Daily 10:00 | `enrichment/run.py` | Generate + publish fact |
| Balashon scraping | Mon 9:00 | `enrichment/balashon_facts.py` | Scrape Balashon blog |

> **Retired:** the `daber-dict-push.sh` auto-push job. Deploy is now pull-based — see
> [Git & Deploy](#git--deploy). Verify with Hermes that no scheduler still triggers it.

---

## Git & Deploy

**Single source of truth = GitHub `main`.** Development happens locally (branch → PR →
merge to `main`); the server is a deploy target that **pulls**, never a writer that pushes.

- **Remote:** `https://github.com/tima100faces/daber-bot-slovar.git`
- **Branch:** `main`
- **Auth:** GitHub token in remote URL

### Deploy (pull-based)

After a PR is merged to `main`, deploy on the server:

```bash
cd /root/daber-dict
git pull --ff-only origin main
.venv/bin/pip install -q -r requirements.txt   # sync deps into the dedicated venv
systemctl restart daber-dict                    # only needed for backend changes; static is served live by nginx
```

Prefer the wrapper `scripts/daber-dict-deploy.sh`: it refuses to deploy over
uncommitted tracked changes (a sign of direct prod edits), then pulls, creates the
dedicated `.venv` if missing, installs `requirements.txt` into it, and restarts.
New Python dependencies therefore install themselves on deploy — just add them to
`requirements.txt`, never `pip install` into another project's venv.

If `git pull --ff-only` fails (non-fast-forward / local changes), **stop** — never force.
Commit/stash server-side edits first, or investigate the divergence.

> The old server→GitHub auto-push (`scripts/daber-dict-push.sh`) is **retired**. The server
> must not commit/push to `main` on its own — that caused two writers racing the same branch.

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
- The backend runs in its own dedicated virtualenv `/root/daber-dict/.venv/` (independent of the Hermes agent's venv)
- For new dependencies: add to `requirements.txt` — the deploy script installs them into `.venv`. Do not `pip install` into another project's venv.
- All project files must stay inside `/root/daber-dict/` (project isolation rule)
- **Independence from Hermes:** the dictionary is self-contained (own code, own `.venv`, own PostgreSQL on `5434`, own `.env`) so it can be migrated to another project later. One remaining coupling: the enrichment cron jobs are still scheduled via the Hermes scheduler (see Cron Jobs) — not yet moved to a standalone scheduler.
- Git remote token is embedded in the URL — be careful with public sharing
