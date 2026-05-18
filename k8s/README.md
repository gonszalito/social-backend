# Kubernetes Deployment

This folder contains deploy-ready manifests for `gobig-social-backend`.

## Files

- `namespace.yaml`: creates namespace `gobig`.
- `configmap.yaml`: non-sensitive runtime config.
- `deployment.yaml`: app workload (`uvicorn` on port `8000`).
- `service.yaml`: ClusterIP service (`80 -> 8000`).
- `ingress.yaml`: external routing (replace host).
- `hpa.yaml`: CPU-based autoscaling.
- `kustomization.yaml`: one-command apply via Kustomize.
- `secret.example.yaml`: local template for required secrets.

## One-time setup

1. Replace `gobig-api.example.com` in `ingress.yaml`.
2. Push image to your registry and update the image in `deployment.yaml`
   or let CI overwrite it during deploy.
3. Deployment now sources runtime env vars from repo file `.env.prod`:
   - CI creates `gobig-api-secrets` using `--from-env-file=.env.prod`.
4. Create IAM public key secret:
   - `kubectl apply -f k8s/secret.example.yaml` (replace key content first), or
   - manage IAM key secret from CI (`GOBIG_IAM_PUBLIC_KEY_PEM`).

## Deploy

```bash
kubectl apply -k k8s
kubectl -n gobig rollout status deployment/gobig-api
```
