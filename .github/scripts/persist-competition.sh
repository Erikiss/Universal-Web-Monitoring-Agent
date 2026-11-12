#!/usr/bin/env bash
# Scope writes to this tracer; concurrent monitors own their separate state.
set -euo pipefail
git config user.name "page-watch-bot"
git config user.email "page-watch-bot@users.noreply.github.com"
git add -- seen_competitions.json reports/competitions/
if git diff --staged --quiet; then
  exit 0
fi
git commit -m "Update Competition Tracer state and reports"
for attempt in 1 2 3; do
  if git push origin HEAD:main; then
    exit 0
  fi
  # Rebase disjoint daily-report writes from other jobs; never force-push.
  # A real conflict fails visibly rather than overwriting another writer.
  git pull --rebase origin main
done
git push origin HEAD:main
