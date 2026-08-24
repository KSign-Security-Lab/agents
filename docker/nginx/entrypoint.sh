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
#   GPU_PEERS unset                         -> just this machine
#   GPU_PEERS=10.0.0.12:8601,10.0.0.13:8601 -> plus those two
#
# The port is required, not defaulted: which port a peer publishes is that
# machine's business, and guessing it here would mean a silent 502 when it
# differs. Entries are passed to nginx unchanged, so use IPs — a hostname is
# resolved by the CONTAINER's DNS, which never reads the host's /etc/hosts.
set -eu

TMP="${TMPDIR:-/tmp}"
CONF_DIR="${CONF_DIR:-/etc/nginx/conf.d}"
TMPL="${TMPL:-/etc/nginx/templates/llm.conf.tmpl}"

# This machine's own vllm, as a sibling container on its internal port. Never
# written down: wherever this gateway runs, the gpu profile put a vllm with it.
LOCAL="vllm:8000"

PEERS=""
for peer in $(printf '%s' "${GPU_PEERS:-}" | tr -d '[:space:]' | tr ',' ' '); do
  case "$peer" in
    "") continue ;;
    *:[0-9]*) PEERS="$PEERS,$peer" ;;
    *) echo "llm-gateway: GPU_PEERS entry '$peer' needs a port, e.g. $peer:8601" >&2
       exit 1 ;;
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
