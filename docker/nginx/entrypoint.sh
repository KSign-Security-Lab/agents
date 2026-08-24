#!/bin/sh
# Render the vLLM upstream from LLM_UPSTREAMS. Runs from nginx's
# /docker-entrypoint.d/ before the server starts.
#
# LLM_UPSTREAMS is a comma-separated host:port list, and nothing here cares
# whether those hosts are sibling containers or other machines:
#   vllm:8000                    the local container (default)
#   gpu-a:8601,gpu-b:8601        two GPU servers, pooled
#
# One entry uses a variable proxy_pass so nginx resolves the name per request
#   and starts even while the model is still loading, instead of dying with
#   "host not found in upstream" — a 30GB download takes a while.
# Two or more needs real load balancing, which requires a static upstream block.
#   Those names must resolve at start-up, which they do: sibling containers are
#   created together, and remote hosts are ordinary DNS.
#
# Substitution is `sed r`, not awk -v: the blocks are multi-line, and awk -v
# rejects embedded newlines on some awks (BSD), which made this untestable
# outside the container.
set -eu

TMP="${TMPDIR:-/tmp}"
CONF_DIR="${CONF_DIR:-/etc/nginx/conf.d}"
TMPL="${TMPL:-/etc/nginx/templates/llm.conf.tmpl}"

# One place for config, and this is not it: LLM_UPSTREAMS comes from .env at the
# repo root, the same file compose interpolates and apps/api/app/config.py reads.
#
# A bind-mounted list file was tried instead, because environment is fixed when a
# container is created — so an edit could then be applied with `nginx -s reload`,
# gracefully, without recreating anything. It was dropped: a second config file
# in a second directory costs more than the reload saves, given `docker compose
# up -d llm-gateway` recreates this stateless container in well under a second.
UPSTREAMS=$(printf '%s' "${LLM_UPSTREAMS:-vllm:8000}" | tr -d '[:space:]' \
            | tr ',' '\n' | grep . | paste -sd, -)
COUNT=$(printf '%s' "$UPSTREAMS" | tr ',' '\n' | grep -c . || true)
[ "$COUNT" -ge 1 ] || { echo "llm-gateway: LLM_UPSTREAMS has no entries" >&2; exit 1; }

if [ "$COUNT" -eq 1 ]; then
  MODE="1 backend"
  printf '%s\n' "# single backend: resolved per request via the resolver directive" > "$TMP/up.part"
  printf '        set $vllm_backend "%s";\n        proxy_pass http://$vllm_backend;\n' \
    "$UPSTREAMS" > "$TMP/pt.part"
else
  MODE="$COUNT backends"
  {
    echo "upstream vllm_pool {"
    echo "    least_conn;"
    printf '%s' "$UPSTREAMS" | tr ',' '\n' | grep . | \
      sed 's/^/    server /; s/$/ max_fails=3 fail_timeout=30s;/'
    echo "    keepalive 32;"
    echo "}"
  } > "$TMP/up.part"
  printf '        proxy_pass http://vllm_pool;\n' > "$TMP/pt.part"
fi

sed -e "/#UPSTREAM_BLOCK#/r $TMP/up.part" -e "/#UPSTREAM_BLOCK#/d" \
    -e "/#PROXY_TARGET#/r $TMP/pt.part"   -e "/#PROXY_TARGET#/d" \
    -e "s/#MODE#/$MODE/" "$TMPL" > "$CONF_DIR/default.conf"
rm -f "$TMP/up.part" "$TMP/pt.part"

echo "llm-gateway: $MODE -> $UPSTREAMS"
