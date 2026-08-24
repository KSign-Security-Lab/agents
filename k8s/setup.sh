#!/usr/bin/env sh
# ---------------------------------------------------------------------------
#  One-time cluster bootstrap. Run on the GPU machines, not on a laptop.
#
#    k8s/setup.sh server            the first machine
#    k8s/setup.sh agent <ip> <tok>  every machine after it
#
#  Idempotent: re-running skips whatever is already in place, so it is safe to
#  use to check a node as well as to build one. This is the only script in the
#  repo, and it exists because installing a cluster is genuinely a one-off
#  sequence of unrelated commands — everything else is `docker compose` or
#  `pnpm run`.
# ---------------------------------------------------------------------------
set -eu

PLUGIN_VERSION="${PLUGIN_VERSION:-v0.20.0}"
ROLE="${1:-}"
say() { printf '\n\033[36m==> %s\033[0m\n' "$*"; }
die() { echo "error: $*" >&2; exit 1; }

case "$ROLE" in
  server) ;;
  agent) [ $# -ge 3 ] || die "usage: k8s/setup.sh agent <server-ip> <node-token>" ;;
  *) die "usage: k8s/setup.sh server | agent <server-ip> <node-token>" ;;
esac

# ---------------------------------------------------------------- prerequisites
say "checking the GPU prerequisites"
command -v nvidia-smi >/dev/null || die "nvidia-smi not found — install the NVIDIA driver first"
nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader | sed 's/^/  GPU /'

# k3s talks to containerd, not docker, but both need the same toolkit underneath.
if ! command -v nvidia-ctk >/dev/null 2>&1 && [ ! -e /usr/bin/nvidia-container-runtime ]; then
  die "NVIDIA Container Toolkit not found. Install it, then re-run:
  https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html"
fi
echo "  container toolkit present"

# --------------------------------------------------------------------- install
if command -v k3s >/dev/null 2>&1; then
  say "k3s already installed ($(k3s --version | head -1))"
else
  say "installing k3s ($ROLE)"
  if [ "$ROLE" = server ]; then
    # --write-kubeconfig-mode makes kubectl usable without sudo.
    curl -sfL https://get.k3s.io | sh -s - server --write-kubeconfig-mode 644
  else
    curl -sfL https://get.k3s.io | K3S_URL="https://$2:6443" K3S_TOKEN="$3" sh -
  fi
fi

if [ "$ROLE" = agent ]; then
  say "done — this node has joined. Check from the server with: kubectl get nodes"
  exit 0
fi

export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
say "waiting for the node to be Ready"
kubectl wait --for=condition=Ready node --all --timeout=180s

# --------------------------------------------------------------- device plugin
# Kubernetes has no concept of a GPU on its own. This DaemonSet finds the cards
# on each node and advertises them to the scheduler as nvidia.com/gpu, which is
# what k8s/vllm.yaml requests. Without it, pods stay Pending forever.
if kubectl -n kube-system get ds nvidia-device-plugin-daemonset >/dev/null 2>&1; then
  say "device plugin already installed"
else
  say "installing the NVIDIA device plugin $PLUGIN_VERSION"
  kubectl apply -f "https://raw.githubusercontent.com/NVIDIA/k8s-device-plugin/$PLUGIN_VERSION/deployments/static/nvidia-device-plugin.yml"
fi
kubectl -n kube-system rollout status ds/nvidia-device-plugin-daemonset --timeout=180s

# ------------------------------------------------------------------- verify
say "are the GPUs schedulable?"
kubectl get nodes -o=custom-columns='NODE:.metadata.name,GPUS:.status.capacity.nvidia\.com/gpu'
total=$(kubectl get nodes -o jsonpath='{range .items[*]}{.status.capacity.nvidia\.com/gpu}{"\n"}{end}' \
        | awk '{s+=$1} END {print s+0}')
[ "$total" -gt 0 ] || die "no GPUs advertised yet. Check: kubectl -n kube-system logs ds/nvidia-device-plugin-daemonset"
echo "  $total GPU(s) schedulable across the cluster"

cat <<EOF

Next:
  1. get agents/infer:dev onto this node — see k8s/README.md
  2. kubectl apply -k k8s/
  3. kubectl -n agents rollout status deploy/vllm     # first run pulls ~30GB
  4. kubectl -n agents get svc                        # note the NodePorts

To add another GPU machine later, on THIS node:
  cat /var/lib/rancher/k3s/server/node-token
then on the new one:
  k8s/setup.sh agent <this-node-ip> <that-token>
EOF
