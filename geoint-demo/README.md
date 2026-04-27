# GEOINT Demo Platform (Kubernetes + F5 Integration)

Production-ready GEOINT demo stack combining:

1. **Geospatial application**: PostGIS + GeoServer + OpenLayers frontend
2. **RAG AI assistant**: FastAPI + ChromaDB + AWS Bedrock

The design is optimized for an F5 tradeshow demo to highlight:
- Layer 4/7 load balancing
- WAF protection for GeoServer and frontend/API paths
- API security for `/api/chat`
- AI Gateway guardrails in front of LLM-backed RAG

---

## 1) Architecture

```text
                           +----------------------------------+
                           |          F5 BIG-IP /             |
                           |       NGINX Ingress Layer        |
                           |  (LB, WAF, API Sec, AI Gateway)  |
                           +-----------------+----------------+
                                             |
                                   geoint-demo.local
                                             |
        +---------------------+--------------+--------------------------+
        |                     |                                         |
   / (frontend)          /geoserver                               /api/chat
        |                     |                                         |
 +------+-------+      +------+-------+                         +-------+-------+
 | OpenLayers   |      | GeoServer    |<----------------------->| RAG API       |
 | Frontend x2  |      | WMS/WFS      |                         | FastAPI        |
 +------+-------+      +------+-------+                         +---+-------+----+
        |                     |                                     |       |
        |                     |                                     |       |
        |               +-----v------+                         +----v--+ +----------------+
        |               | PostGIS    |                         |Chroma | | AWS Bedrock    |
        |               | geoint_db  |                         |DB     | | (Claude model) |
        |               +------------+                         +-------+ +----------------+
```

---

## 2) Repository Layout

```text
geoint-demo/
├── README.md
├── deploy.sh
├── k8s/
│   ├── namespace.yaml
│   ├── secrets.yaml
│   ├── ingress.yaml
│   ├── network-policies.yaml
│   ├── configmaps/
│   │   ├── postgis-init.yaml
│   │   ├── geoserver-init.yaml
│   │   └── nginx-config.yaml
│   ├── registry/
│   │   ├── deployment.yaml
│   │   ├── ingress.yaml
│   │   ├── service.yaml
│   │   └── pvc.yaml
│   ├── postgis/
│   │   ├── deployment.yaml
│   │   ├── service.yaml
│   │   └── pvc.yaml
│   ├── geoserver/
│   │   ├── deployment.yaml
│   │   ├── service.yaml
│   │   ├── pvc.yaml
│   │   └── init-job.yaml
│   ├── frontend/
│   │   ├── deployment.yaml
│   │   └── service.yaml
│   ├── chromadb/
│   │   ├── deployment.yaml
│   │   ├── service.yaml
│   │   └── pvc.yaml
│   ├── ollama/               # Optional legacy local LLM stack (not used by deploy.sh)
│   │   ├── deployment.yaml
│   │   ├── service.yaml
│   │   └── pvc.yaml
│   └── rag-api/
│       ├── deployment.yaml
│       └── service.yaml
├── frontend/
│   ├── Dockerfile
│   ├── nginx.conf
│   ├── package.json
│   └── src/
│       ├── index.html
│       ├── app.js
│       └── style.css
└── rag-api/
    ├── Dockerfile
    ├── requirements.txt
    └── app.py
```

---

## 3) Prerequisites

- Kubernetes cluster (v1.27+ recommended)
- `kubectl` configured to target cluster
- Ingress controller installed (NGINX ingress class used in manifests)
- Docker/Podman to build custom images
- Internet access for AWS Bedrock API access and Python/NPM dependencies

Optional (for GPU acceleration):
- NVIDIA GPU nodes + device plugin

---

## 4) Internal Docker Registry + Image Push/Pull

This repo now includes an internal registry deployment (`registry:2`) in the
`geoint-demo` namespace and exposes it through the existing NGINX ingress
controller:

- Deployment: `k8s/registry/deployment.yaml`
- Service: `k8s/registry/service.yaml` (`ClusterIP` on port `5000`)
- Ingress: `k8s/registry/ingress.yaml` (host `registry.geoint-demo.local`)
- Storage: `k8s/registry/pvc.yaml`

The registry ingress is configured for large Docker layer uploads with:
- `nginx.ingress.kubernetes.io/proxy-body-size: "0"`
- `nginx.ingress.kubernetes.io/proxy-request-buffering: "off"`

### 4.1 Deploy only the internal registry first

```bash
chmod +x deploy.sh
./deploy.sh --registry-only
```

Check the registry ingress:

```bash
kubectl -n geoint-demo get ingress internal-registry-ingress
```

Use your ingress controller address with this host header / DNS name:

```text
registry.geoint-demo.local:443
```

### 4.2 Build, tag, and push app images to the internal registry

```bash
# Set your registry endpoint
REGISTRY_HOST=registry.geoint-demo.local

# Frontend image
docker build -t ${REGISTRY_HOST}/geoint-frontend:1.0.0 ./frontend
docker push ${REGISTRY_HOST}/geoint-frontend:1.0.0

# RAG API image
docker build -t ${REGISTRY_HOST}/geoint-rag-api:1.0.0 ./rag-api
docker push ${REGISTRY_HOST}/geoint-rag-api:1.0.0
```

### 4.3 Deploy the full stack using images from the internal registry

```bash
./deploy.sh --registry-host registry.geoint-demo.local:443
```

`deploy.sh` applies base manifests, deploys the internal registry, and then sets
image references on `rag-api` and `geoint-frontend` deployments to:

- `registry.geoint-demo.local:443/geoint-rag-api:1.0.0`
- `registry.geoint-demo.local:443/geoint-frontend:1.0.0`

> Note: This repo now configures TLS on the registry ingress using secret
> `internal-registry-tls` in `k8s/secrets.yaml`. Replace placeholder cert/key
> values with a valid cert for `registry.geoint-demo.local` before pushing images.

### 4.4 Configure registry TLS certificate trust

The ingress now terminates TLS for `registry.geoint-demo.local` using
`internal-registry-tls` in `k8s/secrets.yaml`.

1. Replace `tls.crt` and `tls.key` placeholders in `k8s/secrets.yaml` with a
   real certificate and key for `registry.geoint-demo.local`.
2. Apply/update secrets:

```bash
kubectl apply -f k8s/secrets.yaml
```

3. Trust that certificate authority (or self-signed cert) on:
   - your external Docker/Podman build workstation (push)
   - each Kubernetes node runtime (pull)

If Docker reports cert errors, install the cert in Docker's trust store for
`registry.geoint-demo.local:443` and restart Docker.

If push fails with `413 Request Entity Too Large`, re-apply
`k8s/registry/ingress.yaml` and confirm those two NGINX ingress annotations are
present on `internal-registry-ingress`.

---

## 5) Deploy

```bash
chmod +x deploy.sh
./deploy.sh
```

`deploy.sh` applies manifests in dependency order and waits for readiness.

Optional deploy flags:

- `--registry-only`: deploy only foundational objects + internal registry
- `--registry-host <host:port>`: update app deployments to pull from registry

Before deploying, set the Bedrock API key in `k8s/secrets.yaml` under `bedrock-secret`:
- `BEDROCK_API_KEY` (value typically starts with `bedrock-api-key-`)

You can also adjust these RAG API Bedrock settings in `k8s/rag-api/deployment.yaml`:
- `AWS_REGION`
- `BEDROCK_MODEL_ID`

Optional advanced Bedrock API key settings (in `rag-api/app.py` env):
- `BEDROCK_RUNTIME_ENDPOINT` (defaults to `https://bedrock-runtime.<AWS_REGION>.amazonaws.com`)
- `BEDROCK_API_KEY_HEADER` (defaults to `x-api-key`)
- `BEDROCK_API_KEY_AUTH_SCHEME` (`bearer` to also send `Authorization: Bearer <key>`)

---

## 6) Access

Add host entry on your workstation:

```text
<INGRESS_IP> geoint-demo.local
```

Then browse:
- `http://geoint-demo.local/` (OpenLayers dashboard)
- `http://geoint-demo.local/geoserver/web/` (GeoServer UI)
- `http://geoint-demo.local/api/health` (RAG API health)

GeoServer admin credentials come from `k8s/secrets.yaml`.

---

## 7) F5 Integration Points (Demo Talking Points)

1. **Load Balancing**
   - Frontend deployment runs with 2 replicas.
   - Demonstrate active distribution and health-based failover.

2. **WAF Protection**
   - Apply WAF policy to `/geoserver` and `/` paths.
   - Show policy behavior for malicious payloads and blocked signatures.

3. **API Security**
   - Protect `/api/chat` with schema validation, rate limiting, JWT/OAuth checks.
   - Demonstrate positive security and anomaly detection.

4. **AI Gateway Guardrails**
   - Place AI Gateway in front of `/api/chat`.
   - Enforce:
     - Prompt injection detection
     - Sensitive data redaction
     - Topic constraints (GEOINT-only policy)
     - Response safety filtering

5. **Observability**
   - Use F5 telemetry for request traces, block events, and model/API usage trends.

Ingress file includes F5 annotation placeholders for BIG-IP/NGINX integration.

---

## 8) Demo Flow

1. Open map and toggle layers (installations, imagery, reports).
2. Click features to show WFS-derived attribute popup.
3. Ask chatbot:
   - “Show all satellite imagery with <30% cloud cover”
   - “What military installations are in Europe?”
   - “Summarize recent GEOINT reports for the Middle East”
4. Show coordinate extraction and map zoom markers from AI output.
5. Demonstrate F5 controls:
   - WAF on GeoServer endpoints
   - API protections for chatbot
   - AI Gateway guardrail event logging

---

## 9) Troubleshooting

- Check pods:
  ```bash
  kubectl -n geoint-demo get pods
  ```
- Check GeoServer init job:
  ```bash
  kubectl -n geoint-demo logs job/geoserver-init-job
  ```
- Check RAG API logs:
  ```bash
  kubectl -n geoint-demo logs deploy/rag-api -f
  ```
- Check RAG API env vars for Bedrock:
  ```bash
  kubectl -n geoint-demo describe deploy/rag-api
  ```

---

## 10) Cleanup

```bash
kubectl delete namespace geoint-demo
```

This removes all deployments, services, jobs, PVC claims, and policies in the demo namespace.

If you configured a local Docker/Podman insecure registry entry for this demo,
you may also want to remove it after cleanup.
