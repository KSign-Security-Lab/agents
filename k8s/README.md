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

## Prerequisites

- a cluster on the GPU machines — k3s is the least machinery for bare metal
- the NVIDIA device plugin, so `nvidia.com/gpu` is a schedulable resource
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
