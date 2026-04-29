# CIS NodePort Example Configurations (LTM / ASM / AFM)

This folder provides three **example CIS Ingress resources** for the GEOINT demo
namespace (`geoint-demo`) when BIG-IP is integrated with Kubernetes via
**Container Ingress Services (CIS)** and the cluster is exposed in
**NodePort mode**.

> These are demonstration manifests. Update VIPs, hostnames, and BIG-IP policy
> object names to match your environment before applying.

## Files

- `ltm-nodeport-ingress.yaml`  
  Basic LTM virtual server for frontend (`/` -> `frontend-service:80`).

- `asm-nodeport-ingress.yaml`  
  LTM + ASM (WAF) example using:
  - `virtual-server.f5.com/waf: "/Common/geoint-asm-policy"`
  - Routes for `/` and `/geoserver`.

- `afm-nodeport-ingress.yaml`  
  LTM + AFM firewall policy example using:
  - `virtual-server.f5.com/firewall: "/Common/geoint-afm-policy"`
  - Route for `/api` -> `rag-api-service:8000`.

## Apply examples

```bash
kubectl apply -f k8s/cis-examples/ltm-nodeport-ingress.yaml
kubectl apply -f k8s/cis-examples/asm-nodeport-ingress.yaml
kubectl apply -f k8s/cis-examples/afm-nodeport-ingress.yaml
```

## Notes for CIS NodePort deployments

1. CIS should be deployed with NodePort pool member mode (or equivalent flag for
   your CIS version) so BIG-IP members are Kubernetes node IPs and NodePorts.
2. The Kubernetes services used by these Ingress resources (`frontend-service`,
   `geoserver-service`, `rag-api-service`) remain `ClusterIP`; CIS resolves to
   NodePort members through the Kubernetes API.
3. Ensure DNS/hosts entries resolve each demo hostname to the matching VIP:
   - `geoint-ltm.demo.local` -> `10.1.10.110`
   - `geoint-asm.demo.local` -> `10.1.10.111`
   - `geoint-afm.demo.local` -> `10.1.10.112`
