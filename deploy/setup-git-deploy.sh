#!/bin/bash
# Richtet das Git-Deployment auf dem Server ein: nacktes Repository anlegen,
# Hook installieren, Fernziel ausgeben. Mehrfach ausführbar.

set -euo pipefail
cd "$(dirname "$0")/.."

WORK_TREE="$(pwd)"
GIT_DIR="${SL_OFFICE_GIT_DIR:-$HOME/sl-office.git}"
BRANCH="${SL_OFFICE_BRANCH:-main}"

if [ ! -d "$GIT_DIR" ]; then
    echo "Lege nacktes Repository an: $GIT_DIR"
    git init --bare --initial-branch="$BRANCH" "$GIT_DIR"
else
    echo "Nacktes Repository vorhanden: $GIT_DIR"
fi

echo "Installiere Hook"
install -m 0755 deploy/post-receive "$GIT_DIR/hooks/post-receive"

# Der Hook liest diese Werte, damit abweichende Pfade nicht im Skript stehen.
git --git-dir="$GIT_DIR" config sl-office.worktree "$WORK_TREE"

cat <<TEXT

Fertig. Auf dem Arbeitsrechner einrichten mit:

    git remote add server ssh://$USER@$(hostname -f 2>/dev/null || hostname):$GIT_DIR
    git push server $BRANCH

Voraussetzungen auf dem Server:
  * sudoers-Eintrag für den Neustart:  sudo install -m 0440 deploy/sudoers-sl-office /etc/sudoers.d/sl-office
  * .env mit SL_OFFICE_ENV, SL_OFFICE_SECRET_KEY und SL_OFFICE_DATABASE_URL
TEXT
