#!/bin/bash
# Pull-based deploy for daber-dict.
#
# Source of truth is GitHub `main`. Run this on the server (/root/daber-dict)
# after a PR has been merged to `main`. The server only ever PULLS — it must
# never commit/push to `main` itself.
#
# Usage:  ./scripts/daber-dict-deploy.sh

set -euo pipefail

REPO="/root/daber-dict"
cd "$REPO"

# Refuse to deploy over uncommitted tracked changes — they would be a sign
# that someone edited prod directly. Untracked files are left untouched.
if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "ERROR: uncommitted tracked changes in $REPO — refusing to deploy." >&2
    echo "Commit/stash them or investigate before deploying. NEVER force." >&2
    git status -s >&2
    exit 1
fi

echo "Pulling origin/main (fast-forward only)..."
git pull --ff-only origin main

# Dedicated virtualenv for daber-dict — independent of any other project on the
# host (e.g. the Hermes agent). Created on first deploy, then kept in sync with
# requirements.txt so new dependencies install automatically.
if [ ! -d .venv ]; then
    echo "Creating dedicated virtualenv (.venv)..."
    python3 -m venv .venv
fi
echo "Syncing dependencies into .venv..."
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

echo "Restarting backend..."
systemctl restart daber-dict

echo "Deployed. HEAD now at: $(git rev-parse --short HEAD)"
