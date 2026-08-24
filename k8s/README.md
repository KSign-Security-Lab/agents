# k8s — the GPU side only

The dev side stays `docker compose`: a developer's laptop is not a cluster, and
`docker compose up -d` for postgres and redis is the right tool. What moves here
is the part that was fighting its tooling — serving the model across machines.

Adopting this **replaces** the `llm-gateway`, and with it:

| compose | k8s |
|---|---|
| `llm-gateway` + nginx template + entrypoint | a `Service` — kube-proxy load balances |
| `GPU_PEERS`, explicit IPs, no DNS | cluster DNS, `vllm.agents.svc.cluster.local` |
| `VLLM_GPUS=0,1` per machine, by hand | `nvidia.com/gpu: N`, the scheduler places it |
| the `vllm2` profile for an odd card count | `replicas:` — pods are the unit, not files |
| `docker compose up -d llm-gateway` to add a peer | `kubectl scale deploy/vllm --replicas=4` |

So on the GPU side it is a net *deletion*: about 150 lines of compose, nginx
config and shell, for ~15 lines of Service.

## Setup

`k8s/setup.sh` does the one-off bootstrap. Run it on the GPU machines, never on a
laptop. It is idempotent, so re-running is also how you check a node.

```bash
# the first machine
k8s/setup.sh server

# its join token, printed for the next machine
sudo cat /var/lib/rancher/k3s/server/node-token

# every machine after it
k8s/setup.sh agent <first-machine-ip> <that-token>
```

`sudo k8s/setup.sh verify` checks a node and changes nothing, which is the same
code path — so a healthy node and a freshly built one are verified identically.

What it checks, in order, refusing early with a message that says what to do:

1. Linux, root, and `curl`/`awk`/`sed`
2. the NVIDIA driver — prints each card and the driver version
3. the Container Toolkit — you already have this if vLLM runs under Docker here
4. the ports k3s needs between nodes (6443/tcp, 8472/udp, 10250/tcp). It reports
   an active `firewalld` or `ufw` rather than editing rules; a silent firewall
   change is worse than a warning
5. installs k3s, waits for the node to be Ready
6. **the `nvidia` RuntimeClass** — see below
7. the device plugin, patched onto that RuntimeClass, and its rollout
8. how many GPUs the scheduler can actually see, failing if zero
9. a **real GPU pod** — a Job running `nvidia-smi -L`. If that passes, the
   cluster can serve models; if not, it leaves the Job in place to inspect

### The RuntimeClass, which is the trap

k3s detects `nvidia-container-runtime` and registers it as a RuntimeClass named
`nvidia` — but it does **not** make it the default. A pod that doesn't ask for it
gets the plain runtime, sees no devices, and fails in a way that reads like a
driver problem. So `k8s/vllm.yaml` and `k8s/infer.yaml` both set:

```yaml
runtimeClassName: nvidia
```

The upstream device-plugin manifest doesn't set it either, so `setup.sh` patches
the DaemonSet. If k3s was installed *before* the toolkit, the RuntimeClass won't
exist — `systemctl restart k3s` creates it, and the script says so.

## Adding a machine, or more pods

Two different things, and only one of them involves a machine.

**More capacity on machines you already have** — just raise the replica count.
The Service picks the new pods up as they become ready; nothing else changes.

```bash
kubectl -n agents scale deploy/vllm --replicas=3
kubectl -n agents get pods -w
```

One pod per GPU is the default (`nvidia.com/gpu: 1`). The scheduler will not
place a fourth pod if there are only three free cards — it stays `Pending` until
one frees up, which is the honest failure mode.

**A new machine** — bootstrap it as an agent, and that's all:

```bash
k8s/setup.sh agent <server-ip> <token>      # on the new box
kubectl -n agents scale deploy/vllm --replicas=4
```

You do not touch `GPU_PEERS`, edit a gateway, or restart anything. The scheduler
notices the new node's cards, places a pod there, and the Service adds it to
rotation once it passes its readiness probe. This is the whole reason to move off
compose: adding a machine went from "edit .env on the gateway box and recreate
it" to "join the cluster".

**A bigger model instead of more copies** — one pod holding several cards:

```yaml
resources: { limits: { nvidia.com/gpu: 2 } }
```
with `TENSOR_PARALLEL: "2"` in `config.yaml`. That replaces the `vllm2` profile
from the compose setup: an odd card count is just a replica count and a
per-pod GPU count, not a second service definition.

## Prerequisites

`setup.sh` handles the first two; they are listed here so you know what it did.

- a cluster on the GPU machines — k3s is the least machinery for bare metal
- the NVIDIA device plugin, so `nvidia.com/gpu` is a schedulable resource
- the NVIDIA driver and Container Toolkit, which you already have if vLLM runs
  under Docker today — the device plugin is a separate, Kubernetes-only piece
- weights cached per node (`hostPath` below), or an RWX volume if you have one

## Use

```bash
kubectl apply -k k8s/
kubectl -n agents rollout status deploy/vllm      # first run pulls ~30GB
kubectl -n agents get svc
```

Then in each developer's `.env`, point at any node — the NodePorts are fixed:

```bash
GPU_HOST=<any-node-ip>
LLM_GATEWAY_PORT=30862      # the vllm Service NodePort
INFER_PORT=30863            # the infer Service NodePort
```

`COMPOSE_PROFILES` on their machine stays `dev`, and nothing else changes: the
application still only knows `LLM_BASE_URL` and `INFER_BASE_URL`.

## Not verified

Written against a cluster I do not have. The manifests are schema-valid
(`kubectl apply --dry-run=client`) and the kustomize build is clean, but nothing
here has scheduled a real pod or touched a real GPU. Treat the first
`rollout status` as the actual test.

## Getting `agents/infer:dev` onto the cluster

Kubernetes pulls images; it cannot use your local Docker daemon's cache. That
image is built by compose and exists nowhere a cluster can fetch it, so the pod
would sit in `ImagePullBackOff`. Either push it to a registry:

```bash
docker tag agents/infer:dev registry.internal:5000/agents/infer:0.1
docker push registry.internal:5000/agents/infer:0.1     # then edit infer.yaml
```

or, on k3s, import the tarball onto each node — no registry needed:

```bash
docker save agents/infer:dev | sudo k3s ctr images import -
```

`vllm/vllm-openai:v0.27.1` is pinned and pulls from Docker Hub, so it needs
nothing.

## A private registry, if you'd rather push than import

One container on the GPU server, and k3s has to be told to trust it:

```bash
docker run -d --restart=always -p 5000:5000 --name registry registry:2
```

```yaml
# /etc/rancher/k3s/registries.yaml on every node, then restart k3s
mirrors:
  "gpu-a:5000":
    endpoint: ["http://gpu-a:5000"]
configs:
  "gpu-a:5000":
    tls: { insecure_skip_verify: true }
```

Worth it once more than a couple of nodes pull the same image. For three nodes,
`k3s ctr images import` is fewer moving parts.

Running **k3s itself** in a container (k3d) is a different thing and not what you
want here — GPU passthrough into nested containers is avoidable pain. Install k3s
on the metal; the workloads are containers either way.
