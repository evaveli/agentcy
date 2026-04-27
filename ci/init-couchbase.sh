#!/usr/bin/env bash
set -euo pipefail

# ──────────────────────────────────────────────────────────────────────────────
# 0) tiny helpers
# ──────────────────────────────────────────────────────────────────────────────
die() { echo "❌ $*" >&2; exit 1; }
log() { echo "▶ $*"; }

# ──────────────────────────────────────────────────────────────────────────────
# 1) mandatory env
# ──────────────────────────────────────────────────────────────────────────────
: "${CB_CONN_STR:?CB_CONN_STR not set}"
: "${CB_USER:?CB_USER not set}"
: "${CB_PASS:?CB_PASS not set}"
: "${CB_BUCKET:?CB_BUCKET not set}"

# Optional overrides
: "${CB_SCOPE:=_default}"
: "${EPHEMERAL_BUCKET:=${CB_BUCKET_EPHEMERAL}}"

# ──────────────────────────────────────────────────────────────────────────────
# 2) derive host / port
# ──────────────────────────────────────────────────────────────────────────────
CB_HOST_PORT="${CB_CONN_STR#couchbase://}"
CB_HOST="${CB_HOST_PORT%%:*}"
CB_PORT="${CB_HOST_PORT##*:}"
[[ "$CB_PORT" == "$CB_HOST_PORT" ]] && CB_PORT=8091
CLUSTER="$CB_HOST:$CB_PORT"

# ──────────────────────────────────────────────────────────────────────────────
# 3) wait for Couchbase
# ──────────────────────────────────────────────────────────────────────────────
log "⏳ waiting for Couchbase at $CLUSTER …"
until curl -fs "http://$CLUSTER/ui/index.html" >/dev/null 2>&1; do sleep 3; done
log "✅ Couchbase reachable"

# ──────────────────────────────────────────────────────────────────────────────
# 4) initialise cluster once
# ──────────────────────────────────────────────────────────────────────────────
if ! couchbase-cli server-list -c "$CLUSTER" -u "$CB_USER" -p "$CB_PASS" >/dev/null 2>&1
then
  log "🔧 initialising single-node cluster…"
  couchbase-cli cluster-init -c "$CLUSTER"           \
    --cluster-username "$CB_USER"                    \
    --cluster-password "$CB_PASS"                    \
    --cluster-name ci-cluster                        \
    --services data,index,query                      \
    --cluster-ramsize 512
else
  log "ℹ️ cluster already initialised"
fi

# ──────────────────────────────────────────────────────────────────────────────
# 5) buckets
# ──────────────────────────────────────────────────────────────────────────────
mkbucket () {
  couchbase-cli bucket-create -c "$CLUSTER" -u "$CB_USER" -p "$CB_PASS" \
    --bucket "$1" --bucket-type couchbase --bucket-ramsize "$2" --enable-flush 1 \
    --storage-backend couchstore --bucket-replica 0 \
    2>/dev/null || true
}
log "🪣 ensuring buckets exist…"
mkbucket "$CB_BUCKET"        256
mkbucket "$EPHEMERAL_BUCKET" 128

# ──────────────────────────────────────────────────────────────────────────────
# 6) scope   (skip if _default or name starts with “_”)
# ──────────────────────────────────────────────────────────────────────────────
if [[ "$CB_SCOPE" != "_default" && "${CB_SCOPE:0:1}" != "_" ]]; then
  for B in "$CB_BUCKET" "$EPHEMERAL_BUCKET"; do
    couchbase-cli collection-manage -c "$CLUSTER" -u "$CB_USER" -p "$CB_PASS" \
      --bucket "$B" --create-scope "$CB_SCOPE" 2>/dev/null || true
  done
fi

# ──────────────────────────────────────────────────────────────────────────────
# 7) collect collection-name envs
# ──────────────────────────────────────────────────────────────────────────────
#   accepted patterns:
#       CB_COLLECTIONS_<name>=foo
#       CB_COLLECTIONS__<name>=foo
#   same for EPHEMERAL_COLLECTIONS_
#
mapfile -t PERSISTENT_COLS < <(
  env | grep -E '^CB_COLLECTIONS(_{1,2})' | cut -d= -f2
)
mapfile -t EPHEMERAL_COLS  < <(
  env | grep -E '^EPHEMERAL_COLLECTIONS_' | cut -d= -f2
)

[[ ${#PERSISTENT_COLS[@]} -eq 0 ]] && die "No persistent collection envs found"
[[ ${#EPHEMERAL_COLS[@]}  -eq 0 ]] && die "No ephemeral collection envs found"

mkcols () {
  local bucket=$1; shift; local -a cols=("$@")
  for col in "${cols[@]}"; do
    couchbase-cli collection-manage -c "$CLUSTER" -u "$CB_USER" -p "$CB_PASS" \
      --bucket "$bucket" --create-collection "${CB_SCOPE}.${col}" 2>/dev/null || true
  done
}

log "📑 creating persistent collections in  $CB_BUCKET.$CB_SCOPE …"
mkcols "$CB_BUCKET" "${PERSISTENT_COLS[@]}"

log "📑 creating *ephemeral* collections in $EPHEMERAL_BUCKET.$CB_SCOPE …"
mkcols "$EPHEMERAL_BUCKET" "${EPHEMERAL_COLS[@]}"

echo "🎉 Couchbase bootstrap complete!"
