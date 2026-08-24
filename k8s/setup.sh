#!/usr/bin/env sh
# ---------------------------------------------------------------------------
#  One-time cluster bootstrap, for the GPU machines.
#
#    sudo k8s/setup.sh server                     the first machine
#    sudo k8s/setup.sh agent <server-ip> <token>  every machine after it
#    sudo k8s/setup.sh verify                     check a node, change nothing
#
#  Every step is idempotent and checks before it acts, so re-running is safe and
#  is also how you diagnose a node. It ends by running a real GPU pod: if that
#  passes, the cluster can serve models.
# ---------------------------------------------------------------------------
set -eu

PLUGIN_VERSION="${PLUGIN_VERSION:-v0.20.0}"
PLUGIN_URL="https://raw.githubusercontent.com/NVIDIA/k8s-device-plugin/${PLUGIN_VERSION}/deployments/static/nvidia-device-plugin.yml"
ROLE="${1:-}"

say()  { printf '\n\033[36m==> %s\033[0m\n' "$*"; }
ok()   { printf '    \033[32mok\033[0m   %s\n' "$*"; }
warn() { printf '    \033[33mwarn\033[0m %s\n' "$*"; }
die()  { printf '\n\033[31merror\033[0m %s\n' "$*" >&2; exit 1; }

case "$ROLE" in
  server|verify) ;;
  agent) [ $# -ge 3 ] || die "usage: k8s/setup.sh agent <server-ip> <node-token>" ;;
  *) die "usage: k8s/setup.sh server | agent <server-ip> <node-token> | verify" ;;
esac

# ------------------------------------------------------------------ the host
say "host"
[ "$(uname -s)" = Linux ] || die "k3s is Linux only; this is $(uname -s). Run this on the GPU machine."
[ "$ROLE" = verify ] || [ "$(id -u)" -eq 0 ] || die "installing k3s needs root — re-run with sudo"
for c in curl awk sed; do command -v "$c" >/dev/null || die "$c not found, and this script needs it"; done
ok "$(uname -sr)"

say "NVIDIA driver"
command -v nvidia-smi >/dev/null || die "nvidia-smi not found. Install the driver, then re-run."
nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader | sed 's/^/    GPU /'
DRIVER=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1)
ok "driver $DRIVER"

say "NVIDIA Container Toolkit"
# k3s speaks to containerd rather than docker, but both sit on this same toolkit.
if command -v nvidia-ctk >/dev/null 2>&1; then ok "nvidia-ctk $(nvidia-ctk --version 2>/dev/null | head -1)"
elif [ -e /usr/bin/nvidia-container-runtime ]; then ok "nvidia-container-runtime present"
else
  die "Container Toolkit not found. If vLLM runs under Docker on this box you have
      it already; otherwise:
      https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html"
fi

# k3s needs these open between nodes. Reported, not changed — the firewall is
# the machine owner's business, and a silent rule change is worse than a note.
say "ports k3s needs between nodes"
echo "    6443/tcp  api server        8472/udp  flannel vxlan"
echo "    10250/tcp kubelet metrics"
if command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
  warn "firewalld is active — open those, or joins and pod networking will hang"
elif command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
  warn "ufw is active — open those, or joins and pod networking will hang"
else
  ok "no host firewall detected as active"
fi

# --------------------------------------------------------------------- install
if [ "$ROLE" = verify ]; then
  command -v k3s >/dev/null || die "k3s is not installed on this node"
  ok "k3s $(k3s --version | head -1 | awk '{print $3}')"
elif command -v k3s >/dev/null 2>&1; then
  say "k3s"
  ok "already installed: $(k3s --version | head -1 | awk '{print $3}')"
else
  say "installing k3s ($ROLE)"
  if [ "$ROLE" = server ]; then
    # 644 so kubectl works without sudo afterwards.
    curl -sfL https://get.k3s.io | sh -s - server --write-kubeconfig-mode 644
  else
    curl -sfL https://get.k3s.io | K3S_URL="https://$2:6443" K3S_TOKEN="$3" sh -
  fi
  ok "installed"
fi

if [ "$ROLE" = agent ]; then
  say "done"
  echo "    This node has joined. On the server:  kubectl get nodes"
  exit 0
fi

export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
[ -r "$KUBECONFIG" ] || die "cannot read $KUBECONFIG — run as root, or on the server node"
K() { kubectl "$@"; }

say "node readiness"
K wait --for=condition=Ready node --all --timeout=180s >/dev/null
K get nodes -o=custom-columns='NAME:.metadata.name,STATUS:.status.conditions[-1].type,VERSION:.status.nodeInfo.kubeletVersion' | sed 's/^/    /'

# ---------------------------------------------------------------- runtimeclass
# The gotcha that costs an afternoon. k3s notices nvidia-container-runtime and
# adds it to containerd as a RuntimeClass named "nvidia" — it does NOT make it
# the default. A pod that does not ask for it gets the plain runtime, sees no
# devices, and fails in a way that looks like a driver problem.
say "nvidia RuntimeClass"
if K get runtimeclass nvidia >/dev/null 2>&1; then
  ok "present — manifests must set runtimeClassName: nvidia (k8s/vllm.yaml does)"
  RUNTIME=nvidia
else
  warn "absent. k3s creates it when it finds the toolkit at install time; if the
         toolkit was installed after k3s, restart k3s and re-run:
           systemctl restart k3s"
  RUNTIME=""
fi

# --------------------------------------------------------------- device plugin
say "NVIDIA device plugin $PLUGIN_VERSION"
if K -n kube-system get ds nvidia-device-plugin-daemonset >/dev/null 2>&1; then
  ok "already installed"
else
  [ "$ROLE" = verify ] && die "device plugin not installed; run: sudo k8s/setup.sh server"
  K apply -f "$PLUGIN_URL"
  ok "applied"
fi
# The upstream manifest ships no runtimeClassName, so on k3s it lands on the
# plain runtime and advertises nothing. Patch it, idempotently.
if [ -n "$RUNTIME" ]; then
  current=$(K -n kube-system get ds nvidia-device-plugin-daemonset \
            -o jsonpath='{.spec.template.spec.runtimeClassName}' 2>/dev/null || true)
  if [ "$current" != "$RUNTIME" ]; then
    K -n kube-system patch ds nvidia-device-plugin-daemonset --type merge \
      -p "{\"spec\":{\"template\":{\"spec\":{\"runtimeClassName\":\"$RUNTIME\"}}}}" >/dev/null
    ok "patched onto runtimeClassName=$RUNTIME"
  else
    ok "runtimeClassName=$RUNTIME already set"
  fi
fi
K -n kube-system rollout status ds/nvidia-device-plugin-daemonset --timeout=180s >/dev/null
ok "rolled out"

# -------------------------------------------------------------------- capacity
say "GPUs the scheduler can see"
K get nodes -o=custom-columns='NODE:.metadata.name,GPUS:.status.capacity.nvidia\.com/gpu' | sed 's/^/    /'
TOTAL=$(K get nodes -o jsonpath='{range .items[*]}{.status.capacity.nvidia\.com/gpu}{"\n"}{end}' \
        | awk '{s+=$1} END {print s+0}')
[ "$TOTAL" -gt 0 ] || die "no GPUs advertised. Look at:
      kubectl -n kube-system logs ds/nvidia-device-plugin-daemonset"
ok "$TOTAL schedulable"

# ------------------------------------------------------------------ smoke test
# The only check that proves it: schedule a pod that asks for a GPU and see
# whether nvidia-smi works inside it.
say "smoke test — a real pod asking for a GPU"
K delete job gpu-smoke --ignore-not-found >/dev/null 2>&1 || true
cat <<EOF | K apply -f - >/dev/null
apiVersion: batch/v1
kind: Job
metadata: { name: gpu-smoke }
spec:
  backoffLimit: 0
  template:
    spec:
      restartPolicy: Never
      ${RUNTIME:+runtimeClassName: $RUNTIME}
      containers:
        - name: smi
          image: nvidia/cuda:12.4.1-base-ubuntu22.04
          command: ["nvidia-smi", "-L"]
          resources: { limits: { nvidia.com/gpu: 1 } }
EOF
if K wait --for=condition=complete job/gpu-smoke --timeout=300s >/dev/null 2>&1; then
  K logs job/gpu-smoke | sed 's/^/    /'
  ok "a pod got a GPU"
  K delete job gpu-smoke >/dev/null 2>&1 || true
else
  K describe job gpu-smoke | tail -15 | sed 's/^/    /'
  K logs job/gpu-smoke 2>&1 | tail -10 | sed 's/^/    /' || true
  die "the smoke test did not complete. The pod above says why; it is usually the
      RuntimeClass or the device plugin. The job is left in place for you to
      inspect: kubectl describe job gpu-smoke"
fi

cat <<EOF

$(printf '\033[32mCluster is ready.\033[0m')

  1. get agents/infer:dev onto the nodes      see k8s/README.md
  2. kubectl apply -k k8s/
  3. kubectl -n agents rollout status deploy/vllm    # first run pulls ~30GB
  4. kubectl -n agents get svc                       # note the NodePorts

To add another GPU machine, print the token here:
  sudo cat /var/lib/rancher/k3s/server/node-token
then on the new machine:
  sudo k8s/setup.sh agent $(hostname -I 2>/dev/null | awk '{print $1}') <token>
EOF
