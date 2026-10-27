#!/usr/bin/env bash
# Smoke test for a running VaultMind container (or any deployment).
#   bash e2e/docker-smoke.sh http://localhost:8000                 # first boot, demo data on
#   bash e2e/docker-smoke.sh http://localhost:8000 --after-restart # data from the e2e audit survived
set -euo pipefail
BASE="${1:-http://localhost:8000}"
MODE="${2:-}"
pass=0
fail() { echo "FAIL  $1"; exit 1; }
ok() { echo "ok    $1"; pass=$((pass + 1)); }
json() { python3 -c "import json,sys; d=json.load(sys.stdin); print(eval(sys.argv[1]))" "$1"; }

headers=$(curl -fsS -D - -o /tmp/index.html "$BASE/")
grep -qi "^content-type: text/html" <<<"$headers" || fail "homepage is HTML"
grep -qi "^content-security-policy: .*script-src 'self'" <<<"$headers" || fail "homepage sends a CSP"
grep -qi "^x-frame-options: DENY" <<<"$headers" || fail "homepage forbids framing"
grep -qi "^x-content-type-options: nosniff" <<<"$headers" || fail "nosniff header"
ok "homepage served with security headers"

[ "$(curl -fsS "$BASE/api/health" | json 'd["status"]')" = "ok" ] || fail "liveness"
[ "$(curl -fsS "$BASE/api/ready" | json 'd["database"]')" = "ok" ] || fail "readiness checks the database"
ok "health and readiness endpoints"

[ "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/api/no-such-route")" = "404" ] || fail "unknown API route is a 404"
[ "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/docs")" != "200" ] || [ "$(curl -s "$BASE/docs" | grep -c swagger || true)" = "0" ] || fail "API docs hidden in production"
ok "unknown API routes 404; API docs not exposed"

asset=$(grep -oE '/assets/index-[A-Za-z0-9_-]+\.js' /tmp/index.html | head -1)
[ -n "$asset" ] || fail "index.html references a bundle"
ah=$(curl -fsS -D - -o /dev/null -H 'Accept-Encoding: gzip' "$BASE$asset")
grep -qi "^cache-control: .*immutable" <<<"$ah" || fail "hashed assets are cached long-term"
grep -qi "^content-encoding: gzip" <<<"$ah" || fail "assets are compressed"
ok "bundle is gzip-compressed and cached as immutable"

[ "$(curl -fsS "$BASE/robots.txt" | head -1)" = "User-agent: *" ] || fail "robots.txt"
[ "$(curl -fsS "$BASE/api/meta" | json 'len(d["teams"])')" = "0" ] || fail "team list hidden from signed-out visitors"
ok "robots.txt served; signed-out metadata is minimal"

token=$(curl -fsS -X POST "$BASE/api/auth/login" -H 'Content-Type: application/json' \
  -d '{"email":"dev@northwind.example","password":"demo1234"}' | json 'd["access_token"]')
auth=(-H "Authorization: Bearer $token")
[ "$(curl -fsS "${auth[@]}" "$BASE/api/auth/me" | json 'd["is_admin"]')" = "True" ] || fail "admin sign-in"
count=$(curl -fsS "${auth[@]}" "$BASE/api/documents" | json 'len(d)')
[ "$count" -ge 20 ] || fail "seeded documents visible to admin (got $count)"
ok "sign-in works and $count documents are visible to the admin"

if [ "$MODE" = "--after-restart" ]; then
  docs=$(curl -fsS "${auth[@]}" "$BASE/api/documents?q=payouts")
  id=$(json '[x["id"] for x in d if x["title"].lower() == "payouts"][0]' <<<"$docs") || fail "document uploaded before the restart still exists"
  [ "$(curl -s -o /dev/null -w '%{http_code}' "${auth[@]}" "$BASE/api/documents/$id/download")" = "200" ] || fail "its file survived on the volume"
  ok "data uploaded before the restart is still there (database and file volume)"
fi

echo "$pass smoke checks passed against $BASE"
