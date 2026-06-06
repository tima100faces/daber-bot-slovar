#!/bin/bash
# Auto-push daber-dict changes to GitHub
# Runs daily at 4am via cron (profile: default)

REPO="/root/daber-dict"
cd "$REPO" || exit 1

# Check if there's anything to commit
if git diff --quiet && git diff --cached --quiet && [ -z "$(git ls-files --others --exclude-standard)" ]; then
    echo "Nothing to push — repo is clean"
    exit 0
fi

git add -A
git commit -m "auto: daily push $(date +%Y-%m-%d)"
git push origin main
echo "Pushed to origin/main"
