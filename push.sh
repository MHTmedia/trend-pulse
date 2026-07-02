#!/bin/bash
# Usage: ./push.sh "your commit message"
# If no message provided, uses a default.
MSG="${1:-Update}"
cd "$(dirname "$0")"
git add .
git commit -m "$MSG"
git push
