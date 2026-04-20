#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="geoint-demo"
REGISTRY_HOST=""
REGISTRY_ONLY="false"

usage() {
  cat <<USAGE
Usage: ./deploy.sh [options]

Options:
  --registry-host <host[:port]> Registry endpoint reachable by BOTH:
                               1) your external build workstation (push)
                               2) cluster nodes (pull)
                               Example: registry.geoint-demo.local:80
                               When set, app deployments use:
                               <host[:port]>/geoint-frontend:1.0.0
                               <host[:port]>/geoint-rag-api:1.0.0
  --registry-only              Deploy only namespace/foundational objects and
                               the internal Docker registry.
  -h, --help                   Show this help message.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --registry-host)
      REGISTRY_HOST="${2:-}"
      if [[ -z "$REGISTRY_HOST" ]]; then
        echo "ERROR: --registry-host requires a value (host[:port])." >&2
        exit 1
      fi
      shift 2
      ;;
    --registry-only)
      REGISTRY_ONLY="true"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: Unknown option: $1" >&2
      usage
      exit 1
      ;;
  esac
done

echo "[1/10] Creating namespace and foundational objects..."
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/secrets.yaml
kubectl apply -f k8s/configmaps/postgis-init.yaml
kubectl apply -f k8s/configmaps/geoserver-init.yaml
kubectl apply -f k8s/configmaps/nginx-config.yaml

echo "[2/10] Deploying internal Docker registry..."
kubectl apply -f k8s/registry/pvc.yaml
kubectl apply -f k8s/registry/deployment.yaml
kubectl apply -f k8s/registry/service.yaml
kubectl apply -f k8s/registry/ingress.yaml
kubectl -n "$NAMESPACE" rollout status deployment/internal-registry --timeout=300s

REGISTRY_INGRESS_HOST="registry.geoint-demo.local"
REGISTRY_EXTERNAL_HINT="${REGISTRY_INGRESS_HOST}:80"

if [[ "$REGISTRY_ONLY" == "true" ]]; then
  echo ""
  echo "Internal registry is ready."
  echo "Registry ingress details:"
  kubectl -n "$NAMESPACE" get ingress internal-registry-ingress
  echo ""
  echo "Registry endpoint via ingress host: ${REGISTRY_EXTERNAL_HINT}"
  echo "Add this host entry to your workstation and cluster nodes as needed:"
  echo "  <INGRESS_IP_OR_DNS> ${REGISTRY_INGRESS_HOST}"
  echo "IMPORTANT: registry:2 is HTTP by default."
  echo "Configure your Docker/Podman client and cluster node runtimes to trust this insecure registry host[:port]."
  echo "Then build/tag/push images and rerun ./deploy.sh --registry-host <host[:port]>."
  exit 0
fi

echo "[3/10] Deploying PostGIS..."
kubectl apply -f k8s/postgis/pvc.yaml
kubectl apply -f k8s/postgis/deployment.yaml
kubectl apply -f k8s/postgis/service.yaml
kubectl -n "$NAMESPACE" rollout status deployment/postgis --timeout=300s

echo "[4/10] Deploying GeoServer..."
kubectl apply -f k8s/geoserver/pvc.yaml
kubectl apply -f k8s/geoserver/deployment.yaml
kubectl apply -f k8s/geoserver/service.yaml
kubectl -n "$NAMESPACE" rollout status deployment/geoserver --timeout=600s

echo "[5/10] Running GeoServer initialization job (workspace/datastore/layers)..."
kubectl apply -f k8s/geoserver/init-job.yaml
kubectl -n "$NAMESPACE" wait --for=condition=complete job/geoserver-init-job --timeout=480s

echo "[6/10] Deploying ChromaDB..."
kubectl apply -f k8s/chromadb/pvc.yaml
kubectl apply -f k8s/chromadb/deployment.yaml
kubectl apply -f k8s/chromadb/service.yaml
kubectl -n "$NAMESPACE" rollout status deployment/chromadb --timeout=300s

echo "[7/10] Deploying RAG API..."
kubectl apply -f k8s/rag-api/deployment.yaml
kubectl apply -f k8s/rag-api/service.yaml

if [[ -n "$REGISTRY_HOST" ]]; then
  kubectl -n "$NAMESPACE" set image deployment/rag-api \
    rag-api="${REGISTRY_HOST}/geoint-rag-api:1.0.0"
fi

kubectl -n "$NAMESPACE" rollout status deployment/rag-api --timeout=600s

echo "[8/10] Deploying frontend..."
kubectl apply -f k8s/frontend/deployment.yaml
kubectl apply -f k8s/frontend/service.yaml

if [[ -n "$REGISTRY_HOST" ]]; then
  kubectl -n "$NAMESPACE" set image deployment/geoint-frontend \
    frontend="${REGISTRY_HOST}/geoint-frontend:1.0.0"
fi

kubectl -n "$NAMESPACE" rollout status deployment/geoint-frontend --timeout=300s

echo "[9/10] Applying ingress and network policies..."
kubectl apply -f k8s/ingress.yaml
kubectl apply -f k8s/network-policies.yaml

echo "[10/10] Deployment completed."
echo ""
echo "=== GEOINT Demo Access Instructions ==="
INGRESS_HOST="geoint-demo.local"
INGRESS_ADDR="$(kubectl -n ingress-nginx get svc ingress-nginx-controller -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || true)"

if [[ -z "${INGRESS_ADDR}" ]]; then
  INGRESS_ADDR="<INGRESS_IP_OR_DNS>"
fi

echo "1) Add to your hosts file:"
echo "   ${INGRESS_ADDR} ${INGRESS_HOST}"
echo "2) Open:"
echo "   http://${INGRESS_HOST}/"
echo "   http://${INGRESS_HOST}/geoserver/web/"
echo "   http://${INGRESS_HOST}/api/health"
echo ""
echo "Internal registry service:"
echo "   kubectl -n ${NAMESPACE} get ingress internal-registry-ingress"
echo "   Registry endpoint via ingress host: ${REGISTRY_EXTERNAL_HINT}"
echo "   hosts entry: <INGRESS_IP_OR_DNS> ${REGISTRY_INGRESS_HOST}"
echo "   Note: if push/pull fails with HTTPS client error, trust this host[:port] as an insecure registry."
echo ""
if [[ -n "$REGISTRY_HOST" ]]; then
  echo "Custom app images deployed from: ${REGISTRY_HOST}"
else
  echo "Tip: set --registry-host to deploy app images from internal registry."
  echo "Example: ./deploy.sh --registry-host ${REGISTRY_EXTERNAL_HINT}"
fi

echo ""
echo "GeoServer credentials are in k8s/secrets.yaml."
echo ""
echo "Cluster objects summary:"
kubectl -n "$NAMESPACE" get pods,svc,ingress
