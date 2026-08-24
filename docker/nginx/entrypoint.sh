#!/bin/sh
# Render the vLLM upstream from the active compose profiles. Runs from nginx's
# /docker-entrypoint.d/ before the server starts.
#
# One backend uses a variable proxy_pass: nginx then resolves the name per
#   request and starts even while the replica is still loading its weights,
#   instead of dying with "host not found in upstream".
# Two needs real load balancing, which requires a static upstream block; both
#   replicas are created together by the profile, so the names resolve.
set -eu

# Derived from the active compose profiles rather than a second LLM_MODE knob:
# one place to say what this machine serves, nothing to keep in step.
case ",${COMPOSE_PROFILES:-}," in
  *,replica2,*) MODE=replica2 ;;
  *)            MODE=single ;;
esac

case "$MODE" in
  single)
    UPSTREAM_BLOCK="# single backend: resolved per request via the resolver directive"
    PROXY_TARGET="        set \$vllm_backend \"vllm-a:8000\";
        proxy_pass http://\$vllm_backend;"
    ;;
  replica2)
    UPSTREAM_BLOCK="upstream vllm_pool {
    least_conn;
    server vllm-a:8000 max_fails=3 fail_timeout=30s;
    server vllm-b:8000 max_fails=3 fail_timeout=30s;
    keepalive 32;
}"
    PROXY_TARGET="        proxy_pass http://vllm_pool;"
    ;;
esac

awk -v ub="$UPSTREAM_BLOCK" -v pt="$PROXY_TARGET" -v mode="$MODE" '
  /#UPSTREAM_BLOCK#/ { print ub; next }
  /#PROXY_TARGET#/   { print pt; next }
  { gsub(/#MODE#/, mode); print }
' /etc/nginx/templates/llm.conf.tmpl > /etc/nginx/conf.d/default.conf

echo "llm-gateway: LLM_MODE=$MODE"
