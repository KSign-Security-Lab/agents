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

# Two ways to say where the models are, file first:
#   docker/nginx/upstreams   one host:port per line, #comments allowed. Mounted
#                            read-only, so editing it and reloading nginx needs
#                            no container recreate — see upstreams.example.
#   LLM_UPSTREAMS            a comma-separated list in .env. Simpler for one
#                            machine, but baked in at create time, so changing
#                            it needs `docker compose up -d llm-gateway`.
LIST_FILE="${LIST_FILE:-/etc/nginx/agents/upstreams}"
if [ -s "$LIST_FILE" ]; then
  SOURCE="$LIST_FILE"
  RAW=$(sed 's/#.*//' "$LIST_FILE" | tr '\n' ',')
else
  SOURCE="LLM_UPSTREAMS"
  RAW="${LLM_UPSTREAMS:-vllm:8000}"
fi

# Tolerate spaces, blank lines and trailing commas from either source.
UPSTREAMS=$(printf '%s' "$RAW" | tr -d '[:space:]' | tr ',' '\n' | grep . | paste -sd, -)
COUNT=$(printf '%s' "$UPSTREAMS" | tr ',' '\n' | grep -c . || true)
[ "$COUNT" -ge 1 ] || { echo "llm-gateway: no upstreams found in $SOURCE" >&2; exit 1; }

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

echo "llm-gateway: $MODE from $SOURCE -> $UPSTREAMS"
