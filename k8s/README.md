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

## One thing that must be fixed first

`infer`'s `/transcribe` takes a storage *key* and reads the file off its own
filesystem. In compose that works because it and `worker` bind-mount the same
host directory; pods on different nodes cannot. Either give the endpoint an
upload path (small change to `TranscribeRequest`), or put `/storage` on an RWX
volume. Embeddings and reranking are unaffected — they pass text over HTTP.
