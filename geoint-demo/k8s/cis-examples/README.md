# CIS Ingress Example Configurations (LTM / ASM / AFM)

This folder provides three **example CIS-managed Kubernetes Ingress resources**
for the GEOINT demo namespace (`geoint-demo`) when BIG-IP is integrated with
Kubernetes via **Container Ingress Services (CIS)**.

> These are demonstration manifests. Update VIPs, hostnames, and BIG-IP policy
> object names to match your environment before applying.

## Files

- `ltm-ingresslink.yaml`  
  Basic LTM CIS Ingress for frontend (`/` -> `frontend-service:80`).
  Includes:
  - `ingressClassName: f5`
  - cert-manager TLS annotation (`cert-manager.io/cluster-issuer: "lab-ca-issuer"`)
  - BIG-IP LTM annotations (`virtual-server.f5.com/*`)

- `asm-ingresslink.yaml`  
  LTM + ASM (WAF) CIS Ingress example using:
  - `virtual-server.f5.com/waf: "/Common/geoint-asm-policy"`
  - Routes for `/` and `/geoserver`

- `afm-ingresslink.yaml`  
  LTM + AFM firewall policy CIS Ingress example using:
  - `virtual-server.f5.com/firewall: "/Common/geoint-afm-policy"`
  - Route for `/api` -> `rag-api-service:8000`.

## Apply examples

```bash
kubectl apply -f k8s/cis-examples/ltm-ingresslink.yaml
kubectl apply -f k8s/cis-examples/asm-ingresslink.yaml
kubectl apply -f k8s/cis-examples/afm-ingresslink.yaml
```

## Notes for CIS-managed Ingress deployments

1. CIS must watch standard Kubernetes `Ingress` resources and be configured for
   the `f5` ingress class.
2. cert-manager must be installed and `ClusterIssuer/lab-ca-issuer` must exist.
3. Application services (`frontend-service`, `geoserver-service`,
   `rag-api-service`) remain `ClusterIP`; CIS programs BIG-IP directly from
   these Ingress resources.
4. Ensure DNS/hosts entries resolve each demo hostname to the matching VIP:
   - `geoint-ltm.demo.local` -> `10.1.10.110`
   - `geoint-asm.demo.local` -> `10.1.10.111`
   - `geoint-afm.demo.local` -> `10.1.10.112`
