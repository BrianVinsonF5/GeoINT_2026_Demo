# GEOINT Demo Platform (Kubernetes + F5 Integration)

Production-ready GEOINT demo stack combining:

1. **Geospatial application**: PostGIS + GeoServer + OpenLayers frontend
2. **RAG AI assistant**: FastAPI + ChromaDB + Google AI (Gemini Flash)

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
        |               | PostGIS    |                         |Chroma | | Google AI      |
        |               | geoint_db  |                         |DB     | | (Gemini Flash) |
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
- Internet access for Google AI API access and Python/NPM dependencies

Optional (for GPU acceleration):
- NVIDIA GPU nodes + device plugin

---

## 4) GitHub Container Registry (GHCR) + Image Push/Pull

This repo now uses GitHub Container Registry (`ghcr.io`) for custom app images.
The local in-cluster registry manifests have been removed.

### 4.1 Configure pull credentials in Kubernetes

`k8s/secrets.yaml` includes `ghcr-pull-secret` (`kubernetes.io/dockerconfigjson`).
Update placeholders before deploy:

- `GITHUB_USERNAME`
- `GITHUB_PAT` (must include `read:packages` for private image pulls)

Then apply secrets:

```bash
kubectl apply -f k8s/secrets.yaml
```

### 4.2 Build and push app images to GHCR

```bash
# optional explicit login
docker login ghcr.io -u <github-username>

# frontend
docker build -t ghcr.io/<owner>/geoint-frontend:1.0.4 ./frontend
docker push ghcr.io/<owner>/geoint-frontend:1.0.4

# rag api
docker build -t ghcr.io/<owner>/geoint-rag-api:1.0.4 ./rag-api
docker push ghcr.io/<owner>/geoint-rag-api:1.0.4
```

### 4.3 Deploy the full stack using GHCR images

```bash
chmod +x deploy.sh
./deploy.sh --ghcr-owner <owner> --github-username <github-username> --github-pat <github-pat>
```

You can also omit `--github-username/--github-pat` if your local Docker/Podman
is already logged in to `ghcr.io`.

---

## 5) Deploy

```bash
chmod +x deploy.sh
./deploy.sh
```

`deploy.sh` applies manifests in dependency order and waits for readiness.

Optional deploy flags:

- `--ghcr-owner <owner>`: GitHub owner/org used in image paths
- `--github-username <username>`: GHCR push auth username
- `--github-pat <token>`: GHCR push auth token (`write:packages`)
- `--image-tag <tag>`: image tag override (default in script: `1.0.4`)

Before deploying, set the Gemini API key in `k8s/secrets.yaml` under `gemini-secret`:
- `GEMINI_API_KEY`

You can also adjust these RAG API Gemini settings in `k8s/rag-api/deployment.yaml`:
- `GEMINI_MODEL` (default: `gemini-3.1-flash-lite-preview`)

Optional advanced Gemini API settings (in `rag-api/app.py` env):
- `GEMINI_API_ENDPOINT` (defaults to `https://generativelanguage.googleapis.com/v1beta`)

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
- Check RAG API env vars for Gemini:
  ```bash
  kubectl -n geoint-demo describe deploy/rag-api
  ```

---

## 10) Cleanup

```bash
kubectl delete namespace geoint-demo
```

This removes all deployments, services, jobs, PVC claims, and policies in the demo namespace.

If you previously configured a local Docker/Podman insecure registry entry for
older versions of this demo, you may also want to remove it after cleanup.
