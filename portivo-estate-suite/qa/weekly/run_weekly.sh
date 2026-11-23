#!/usr/bin/env bash
# Weekly read-only quality run. Safe to run repeatedly: it only reads.
# Output: docs/quality/reports/weekly/<date>.md and qa/weekly/history/<date>.json
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
OUT_MD="$ROOT/docs/quality/reports/weekly"
OUT_JSON="$HERE/history"
mkdir -p "$OUT_MD" "$OUT_JSON"
DEPS="${TMPDIR:-/tmp}/portivo-qa-deps"
mkdir -p "$DEPS"
[ -d "$DEPS/node_modules/axe-core" ] || npm install --prefix "$DEPS" --no-audit --no-fund axe-core@4.10.2 >/dev/null 2>&1
export NODE_PATH="$(npm root -g):$DEPS/node_modules"
TMP="$(mktemp -d)"
node "$HERE/run_weekly.js" "$TMP"
RC=$?
cp "$TMP"/*.md "$OUT_MD"/ && cp "$TMP"/*.json "$OUT_JSON"/
echo "report: $OUT_MD/$(ls "$TMP"/*.md | xargs -n1 basename)"
exit $RC
