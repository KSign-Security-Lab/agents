#!/bin/sh
# Render the vLLM upstream from LLM_MODE. Runs from nginx's
# /docker-entrypoint.d/ before the server starts.
#
# single / tp2 have exactly one backend, so they use a variable proxy_pass:
#   nginx then resolves the name per request and will start even while the
#   replica is still loading, instead of dying with "host not found in upstream".
# dp2 needs real load balancing, which requires a static upstream block; both
#   replicas are created together by the compose profile, so the names resolve.
set -eu

MODE="${LLM_MODE:-single}"

case "$MODE" in
  single|tp2)
    [ "$MODE" = single ] && BACKEND="vllm-a:8000" || BACKEND="vllm-tp:8000"
    UPSTREAM_BLOCK="# single backend: resolved per request via the resolver directive"
    PROXY_TARGET="        set \$vllm_backend \"$BACKEND\";
        proxy_pass http://\$vllm_backend;"
    ;;
  dp2)
    UPSTREAM_BLOCK="upstream vllm_pool {
    least_conn;
    server vllm-a:8000 max_fails=3 fail_timeout=30s;
    server vllm-b:8000 max_fails=3 fail_timeout=30s;
    keepalive 32;
}"
    PROXY_TARGET="        proxy_pass http://vllm_pool;"
    ;;
  *)
    echo "llm-gateway: unknown LLM_MODE='$MODE' (expected single|tp2|dp2)" >&2
    exit 1
    ;;
esac

awk -v ub="$UPSTREAM_BLOCK" -v pt="$PROXY_TARGET" -v mode="$MODE" '
  /#UPSTREAM_BLOCK#/ { print ub; next }
  /#PROXY_TARGET#/   { print pt; next }
  { gsub(/#MODE#/, mode); print }
' /etc/nginx/templates/llm.conf.tmpl > /etc/nginx/conf.d/default.conf

echo "llm-gateway: LLM_MODE=$MODE"
