#!/usr/bin/env bash
# Collection and delivery acknowledgements use the same narrow set of paths.
set -euo pipefail
git config user.name "page-watch-bot"
git config user.email "page-watch-bot@users.noreply.github.com"
git add -- seen_pythia_google.json reports/pythia_google_latest.md reports/pythia-google/
if git diff --staged --quiet; then
  exit 0
fi
git commit -m "Update Pythia Google search state and reports"
for attempt in 1 2 3; do
  if git push origin HEAD:main; then
    exit 0
  fi
  # Other watchers can update different files on main. A genuine conflict is
  # an error; never overwrite another writer's state or force-push.
  git pull --rebase origin main
done
git push origin HEAD:main
