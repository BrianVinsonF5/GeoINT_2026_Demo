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
`geoint-demo` namespace and exposes it for external workstations:

- Deployment: `k8s/registry/deployment.yaml`
- Service: `k8s/registry/service.yaml` (`LoadBalancer` on port `5000`, with
  NodePort fallback `32000`)
- Storage: `k8s/registry/pvc.yaml`

### 4.1 Deploy only the internal registry first

```bash
chmod +x deploy.sh
./deploy.sh --registry-only
```

Find a reachable endpoint from your **external build workstation** and from
**cluster nodes**:

```bash
kubectl -n geoint-demo get svc internal-registry-service -o wide
```

Preferred endpoint (external LB):

```text
<EXTERNAL_LB_IP_OR_DNS>:5000
```

Fallback endpoint (if LB is not available):

```text
<NODE_IP>:32000
```

### 4.2 Build, tag, and push app images to the internal registry

```bash
# Set your registry endpoint
REGISTRY_HOST=<EXTERNAL_LB_IP_OR_DNS>:5000

# Frontend image
docker build -t ${REGISTRY_HOST}/geoint-frontend:1.0.0 ./frontend
docker push ${REGISTRY_HOST}/geoint-frontend:1.0.0

# RAG API image
docker build -t ${REGISTRY_HOST}/geoint-rag-api:1.0.0 ./rag-api
docker push ${REGISTRY_HOST}/geoint-rag-api:1.0.0
```

### 4.3 Deploy the full stack using images from the internal registry

```bash
./deploy.sh --registry-host <EXTERNAL_LB_IP_OR_DNS>:5000
```

`deploy.sh` applies base manifests, deploys the internal registry, and then sets
image references on `rag-api` and `geoint-frontend` deployments to:

- `<EXTERNAL_LB_IP_OR_DNS>:5000/geoint-rag-api:1.0.0`
- `<EXTERNAL_LB_IP_OR_DNS>:5000/geoint-frontend:1.0.0`

If your environment does not provide a LoadBalancer address, use NodePort
instead:

- `<NODE_IP>:32000/geoint-rag-api:1.0.0`
- `<NODE_IP>:32000/geoint-frontend:1.0.0`

> Note: If your cluster runtime blocks plain HTTP registries, configure each node
> to trust your internal registry as an insecure registry (or add TLS/auth to the
> registry).

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

Before deploying, set AWS credentials in `k8s/secrets.yaml` under `bedrock-secret`:
- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`
- `AWS_SESSION_TOKEN` (optional)

You can also adjust these RAG API Bedrock settings in `k8s/rag-api/deployment.yaml`:
- `AWS_REGION`
- `BEDROCK_MODEL_ID`

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
