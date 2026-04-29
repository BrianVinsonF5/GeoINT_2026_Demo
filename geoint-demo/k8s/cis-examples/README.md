# CIS IngressLink Example Configurations (LTM / ASM / AFM)

This folder provides three **example NGINX Ingress + CIS IngressLink resources**
for the GEOINT demo namespace (`geoint-demo`) when BIG-IP is integrated with
Kubernetes via **Container Ingress Services (CIS)** and an existing
**NGINX Ingress Controller** on the cluster.

> These are demonstration manifests. Update VIPs, hostnames, and BIG-IP policy
> object names to match your environment before applying.

## Files

- `ltm-ingresslink.yaml`  
  Basic LTM IngressLink virtual server for frontend (`/` -> `frontend-service:80`).
  Includes:
  - NGINX-managed `Ingress` (`ingressClassName: nginx`)
  - CIS `IngressLink` (`virtualServerAddress: 10.1.10.110`)

- `asm-ingresslink.yaml`  
  LTM + ASM (WAF) IngressLink example using:
  - `virtual-server.f5.com/waf: "/Common/geoint-asm-policy"`
  - Routes for `/` and `/geoserver` through NGINX ingress

- `afm-ingresslink.yaml`  
  LTM + AFM firewall policy IngressLink example using:
  - `virtual-server.f5.com/firewall: "/Common/geoint-afm-policy"`
  - Route for `/api` -> `rag-api-service:8000` through NGINX ingress.

## Apply examples

```bash
kubectl apply -f k8s/cis-examples/ltm-ingresslink.yaml
kubectl apply -f k8s/cis-examples/asm-ingresslink.yaml
kubectl apply -f k8s/cis-examples/afm-ingresslink.yaml
```

## Notes for CIS IngressLink + existing NGINX Ingress deployments

1. CIS must watch `IngressLink` resources (CRD installed) and be configured for
   NGINX IngressLink integration.
2. Ensure your NGINX controller pods are healthy and reachable in-cluster; CIS
   uses the `IngressLink` selector labels to discover the target controller pods.
3. The `spec.selector.matchLabels` on each `IngressLink` must match labels on
   your NGINX controller pods. If your chart/deployment uses different labels,
   update these selectors before apply.
4. Application services (`frontend-service`, `geoserver-service`,
   `rag-api-service`) remain `ClusterIP`; NGINX handles L7 routing while BIG-IP
   fronts NGINX through IngressLink.
5. Ensure DNS/hosts entries resolve each demo hostname to the matching VIP:
   - `geoint-ltm.demo.local` -> `10.1.10.110`
   - `geoint-asm.demo.local` -> `10.1.10.111`
   - `geoint-afm.demo.local` -> `10.1.10.112`
