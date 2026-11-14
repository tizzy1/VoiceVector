#!/bin/sh
# Usage: sh tools/git/install-hook.sh <tudor|sanju|bhavin>
set -e
case "$1" in tudor|sanju|bhavin) ;; *) echo "usage: $0 <tudor|sanju|bhavin>"; exit 1 ;; esac
root=$(git rev-parse --show-toplevel)
git config sweatbox.owner "$1"
cp "$root/tools/git/pre-commit" "$root/.git/hooks/pre-commit"
chmod +x "$root/.git/hooks/pre-commit"
echo "Ownership hook installed for: $1"
