#!/bin/sh
# /init/bootstrap-nexus.sh
set -eu

log(){ printf '▶ %s\n' "$*"; }
die(){ printf '❌ %s\n' "$*" >&2; exit 1; }

# ────────────────────────────── required env ──────────────────────────────
: "${NEXUS_URL:?Set NEXUS_URL, e.g. http://nexus:8081}"
: "${NEW_ADMIN_PASS:?Set NEW_ADMIN_PASS}"
: "${PYPI_REPO:?Set PYPI_REPO, e.g. pypi-internal}"
: "${DOCKER_REPO:?Set DOCKER_REPO, e.g. docker-internal}"
: "${NEXUS_DOCKER_PORT:?Set NEXUS_DOCKER_PORT, e.g. 5001}"
DOCKER_SUBDOMAIN="${DOCKER_SUBDOMAIN:-false}"

# Optional service user envs
NEXUS_SVC_USER="${NEXUS_SVC_USER:-}"
NEXUS_SVC_PASS="${NEXUS_SVC_PASS:-}"
NEXUS_SVC_EMAIL="${NEXUS_SVC_EMAIL:-svc@local}"
NEXUS_SVC_RESET="${NEXUS_SVC_RESET:-0}"
# If you know exact role IDs, comma-separate them here; otherwise we’ll discover valid IDs
NEXUS_SVC_ROLES="${NEXUS_SVC_ROLES:-}"

# ───────────────── wait for Nexus ─────────────────
log "Waiting for Nexus at $NEXUS_URL …"
i=0
until curl -sf "$NEXUS_URL/service/rest/v1/status" >/dev/null 2>&1; do
  i=$((i+1)); [ "$i" -gt 120 ] && die "Nexus never became healthy"; sleep 2
done
log "Nexus is up."

# ───────── read first-run admin password or auth with NEW_ADMIN_PASS ─────────
ADMIN_FILE="/nexus-data/admin.password"
AUTH=""

auth_ok() {
  code="$(curl -sS -o /dev/null -w '%{http_code}' -u "$1" -H 'Accept: application/json' \
    "$NEXUS_URL/service/rest/v1/security/users")"
  [ "$code" = "200" ]
}

if [ -f "$ADMIN_FILE" ]; then
  OLD_ADMIN_PASS="$(tr -d '\r\n' < "$ADMIN_FILE")"
  code="$(curl -sS -o /dev/null -w '%{http_code}' \
    -u "admin:$OLD_ADMIN_PASS" -H 'Content-Type: text/plain' \
    -X PUT "$NEXUS_URL/service/rest/v1/security/users/admin/change-password" \
    --data "$NEW_ADMIN_PASS" || true)"
  [ "$code" = "204" ] && log "Admin password changed."
  AUTH="admin:$NEW_ADMIN_PASS"
else
  log "admin.password not found — trying NEW_ADMIN_PASS to auth…"
  if auth_ok "admin:$NEW_ADMIN_PASS"; then
    AUTH="admin:$NEW_ADMIN_PASS"
    log "Authenticated with NEW_ADMIN_PASS."
  else
    die "Cannot authenticate with NEW_ADMIN_PASS (HTTP 401)"
  fi
fi

# helpers
get_code() {
  curl -sS -o /dev/null -w '%{http_code}' -u "$AUTH" -H 'Accept: application/json' "$1"
}
api_json() {  # method path body-json
  method="$1"; path="$2"; body="$3"
  curl -sS -f -u "$AUTH" -H 'Content-Type: application/json' -H 'Accept: application/json' \
       -X "$method" "$NEXUS_URL$path" -d "$body" >/dev/null
}

# ───────── ensure DockerToken realm is active ─────────
log "Ensuring DockerToken realm is active…"
ACTIVE_JSON="$(curl -sS -f -u "$AUTH" -H 'Accept: application/json' \
  "$NEXUS_URL/service/rest/v1/security/realms/active" || echo '[]')"
printf '%s\n' "$ACTIVE_JSON" | grep -q '"DockerToken"' \
  && log "DockerToken already active." \
  || {
    for payload in \
      '["NexusAuthenticatingRealm","DockerToken"]' \
      '["NexusAuthorizingRealm","NexusAuthenticatingRealm","DockerToken"]' \
      '["NexusBasicAuthorizingRealm","NexusBasicAuthenticationRealm","DockerToken"]'
    do
      if curl -sS -u "$AUTH" -H 'Content-Type: application/json' -H 'Accept: application/json' \
           -X PUT "$NEXUS_URL/service/rest/v1/security/realms/active" -d "$payload" >/dev/null 2>&1; then
        log "DockerToken added to active realms."
        break
      fi
    done
  }

# ───────── create PyPI hosted repo ─────────
if [ "$(get_code "$NEXUS_URL/service/rest/v1/repositories/$PYPI_REPO")" -eq 200 ]; then
  log "PyPI repo '$PYPI_REPO' already exists; skipping."
else
  log "Creating PyPI hosted repo '$PYPI_REPO'…"
  api_json POST "/service/rest/v1/repositories/pypi/hosted" "$(cat <<JSON
{
  "name": "$PYPI_REPO",
  "online": true,
  "storage": { "blobStoreName": "default", "strictContentTypeValidation": true, "writePolicy": "ALLOW" },
  "cleanup": { "policyNames": [] },
  "component": { "proprietaryComponents": true }
}
JSON
)"
  log "PyPI repo created."
fi

# ───────── create Docker hosted repo ─────────
if [ "$(get_code "$NEXUS_URL/service/rest/v1/repositories/$DOCKER_REPO")" -eq 200 ]; then
  log "Docker repo '$DOCKER_REPO' already exists; skipping."
else
  log "Creating Docker hosted repo '$DOCKER_REPO' on port $NEXUS_DOCKER_PORT (subdomain=$DOCKER_SUBDOMAIN)…"
  api_json POST "/service/rest/v1/repositories/docker/hosted" "$(cat <<JSON
{
  "name": "$DOCKER_REPO",
  "online": true,
  "storage": { "blobStoreName": "default", "strictContentTypeValidation": true, "writePolicy": "ALLOW" },
  "docker": { "v1Enabled": false, "forceBasicAuth": true, "httpPort": $NEXUS_DOCKER_PORT, "subdomain": ${DOCKER_SUBDOMAIN} }
}
JSON
)"
  log "Docker repo created."
fi

# ───────── service user creation (skip updates if user already exists) ─────────
if [ -n "$NEXUS_SVC_USER" ] && [ -n "$NEXUS_SVC_PASS" ]; then
  log "Ensuring service user '$NEXUS_SVC_USER' exists…"

  # Validate a role ID by exact endpoint (avoids brittle JSON grepping)
  role_exists() {
    code="$(curl -sS -o /dev/null -w '%{http_code}' -u "$AUTH" -H 'Accept: application/json' \
      "$NEXUS_URL/service/rest/v1/security/roles/$1" || true)"
    [ "$code" = "200" ]
  }

  # Build roles list (we still build for first-time creation; ignored if user already exists)
  if [ -n "$NEXUS_SVC_ROLES" ]; then
    IFS=','; set -- $NEXUS_SVC_ROLES; IFS=' '
    WANT_ROLES=""
    for r in "$@"; do
      r="$(printf '%s' "$r" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
      [ -z "$r" ] && continue
      if role_exists "$r"; then
        WANT_ROLES="${WANT_ROLES:+$WANT_ROLES,}\"$r\""
      else
        log "Role '$r' not found on server; skipping."
      fi
    done
  else
    WANT_ROLES=""
    for cand in \
      "nx-repository-admin-pypi-$PYPI_REPO" \
      "nx-repository-admin-pypi-hosted-$PYPI_REPO" \
      "nx-repository-admin-docker-$DOCKER_REPO" \
      "nx-repository-admin-docker-hosted-$DOCKER_REPO"
    do
      if role_exists "$cand"; then
        WANT_ROLES="${WANT_ROLES:+$WANT_ROLES,}\"$cand\""
      fi
    done
    [ -z "$WANT_ROLES" ] && WANT_ROLES="\"nx-admin\""
  fi
  ROLES_JSON="[$WANT_ROLES]"

  # Helper: POST that returns "<body>\n<code>"
  post_user() {
    curl -sS -u "$AUTH" -H 'Content-Type: application/json' -H 'Accept: application/json' \
      -X POST "$NEXUS_URL/service/rest/v1/security/users" -d "$1" -w '\n%{http_code}' || true
  }

  # Does user already exist? If yes, do NOTHING (as requested) and succeed.
  user_exists=false
  BODY="$(curl -sS -f -u "$AUTH" -H 'Accept: application/json' \
    "$NEXUS_URL/service/rest/v1/security/users?userId=$NEXUS_SVC_USER" || echo '[]')"
  printf '%s' "$BODY" | grep -q "\"userId\":\"$NEXUS_SVC_USER\"" && user_exists=true
  if [ "$user_exists" = false ]; then
    CODE="$(curl -sS -o /dev/null -w '%{http_code}' -u "$AUTH" \
      -H 'Accept: application/json' "$NEXUS_URL/service/rest/v1/security/users/$NEXUS_SVC_USER" || true)"
    [ "$CODE" = "200" ] && user_exists=true
  fi

  if [ "$user_exists" = true ]; then
    log "User '$NEXUS_SVC_USER' already exists; leaving unchanged."
    # Optional: allow password reset without touching roles
    if [ "$NEXUS_SVC_RESET" = "1" ]; then
      pw_code="$(curl -sS -o /dev/null -w '%{http_code}' \
        -u "$AUTH" -H 'Content-Type: text/plain' \
        -X PUT "$NEXUS_URL/service/rest/v1/security/users/$NEXUS_SVC_USER/change-password" \
        --data "$NEXUS_SVC_PASS" || true)"
      [ "$pw_code" = "204" ] && log "Password reset OK." || log "Password reset returned HTTP $pw_code."
    fi
  else
    # Create user (first attempt with desired roles)
    RESP="$(post_user "$(cat <<JSON
{
  "userId": "$NEXUS_SVC_USER",
  "firstName": "Service",
  "lastName": "User",
  "emailAddress": "$NEXUS_SVC_EMAIL",
  "password": "$NEXUS_SVC_PASS",
  "status": "active",
  "roles": $ROLES_JSON
}
JSON
)")"
    CODE="$(printf '%s' "$RESP" | tail -n1)"
    BODY="$(printf '%s' "$RESP" | sed '$d')"

    case "$CODE" in
      200|201)
        log "Service user created (HTTP $CODE)."
        ;;
      *)
        # If server says duplicate, treat as success and DO NOT attempt to update.
        if echo "$BODY" | grep -qi 'DuplicateUserException\|already exists'; then
          log "Create reported 'already exists'; not modifying existing user."
        else
          # If some other error (e.g., bad role IDs), try once with nx-admin; if that also duplicates, treat as success.
          log "Create failed with HTTP $CODE; retrying with nx-admin."
          RESP="$(post_user "$(cat <<JSON
{
  "userId": "$NEXUS_SVC_USER",
  "firstName": "Service",
  "lastName": "User",
  "emailAddress": "$NEXUS_SVC_EMAIL",
  "password": "$NEXUS_SVC_PASS",
  "status": "active",
  "roles": ["nx-admin"]
}
JSON
)")"
          CODE="$(printf '%s' "$RESP" | tail -n1)"
          BODY="$(printf '%s' "$RESP" | sed '$d')"
          if [ "$CODE" != "200" ] && [ "$CODE" != "201" ]; then
            if echo "$BODY" | grep -qi 'DuplicateUserException\|already exists'; then
              log "Create (nx-admin) reported 'already exists'; not modifying existing user."
            else
              die "Failed to create service user (HTTP $CODE)"
            fi
          else
            log "Service user created with nx-admin."
          fi
        fi
        ;;
    esac
  fi
else
  log "NEXUS_SVC_USER/NEXUS_SVC_PASS not set; skipping service-user creation."
fi

log "🎉 Nexus bootstrap complete."
