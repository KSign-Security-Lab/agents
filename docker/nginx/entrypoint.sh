#!/bin/sh
# Render the vLLM upstream list. Runs from nginx's /docker-entrypoint.d/ before
# the server starts.
#
# You only ever name the OTHER machines. This machine's own vllm is always in the
# pool — the gateway and vllm are both in the `gpu` profile, so wherever this runs
# there is a local one — and it is reached as the compose service `vllm` on its
# internal port 8000, which is not the port other machines use. Making people
# write that themselves meant one line mixing a service name on an internal port
# with hostnames on a published port, which is a needless thing to have to know.
#
#   GPU_PEERS unset          -> just this machine
#   GPU_PEERS=gpu-b,gpu-c    -> this machine plus two others, on VLLM_PORT
#   GPU_PEERS=gpu-b:9000     -> ...on a port of its own, if one differs
#
# Entries may be hostnames or IPs. Blank entries and spaces are ignored.
set -eu

TMP="${TMPDIR:-/tmp}"
CONF_DIR="${CONF_DIR:-/etc/nginx/conf.d}"
TMPL="${TMPL:-/etc/nginx/templates/llm.conf.tmpl}"

PEER_PORT="${VLLM_PORT:-8601}"
LOCAL="vllm:8000"

PEERS=""
for peer in $(printf '%s' "${GPU_PEERS:-}" | tr -d '[:space:]' | tr ',' ' '); do
  case "$peer" in
    "")   continue ;;
    *:*)  PEERS="$PEERS,$peer" ;;            # explicit port
    *)    PEERS="$PEERS,$peer:$PEER_PORT" ;; # default port
  esac
done

UPSTREAMS="$LOCAL$PEERS"
COUNT=$(printf '%s' "$UPSTREAMS" | tr ',' '\n' | grep -c . || true)
[ "$COUNT" -ge 1 ] || { echo "llm-gateway: no upstreams" >&2; exit 1; }

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
