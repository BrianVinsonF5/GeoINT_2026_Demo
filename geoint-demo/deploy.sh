#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

NAMESPACE="geoint-demo"
REGISTRY_HOST=""
REGISTRY_ONLY="false"
REMOVE_ALL="false"

FRONTEND_IMAGE_NAME="geoint-frontend"
RAG_API_IMAGE_NAME="geoint-rag-api"
IMAGE_TAG="1.0.0"

usage() {
  cat <<USAGE
Usage: ./deploy.sh [options]

Options:
  --remove                     Remove everything deployed by this script
                               (deletes namespace: ${NAMESPACE}).
  --registry-host <host[:port]> Registry endpoint reachable by BOTH:
                               1) your external build workstation (push)
                               2) cluster nodes (pull)
                               Example: registry.geoint-demo.local:443
                               Also builds and pushes both custom images.
  --registry-only              Deploy only namespace/foundational objects and
                               the internal Docker registry.
  -h, --help                   Show this help message.
USAGE
}

pick_container_cli() {
  if command -v docker >/dev/null 2>&1; then
    echo "docker"
    return 0
  fi

  if command -v podman >/dev/null 2>&1; then
    echo "podman"
    return 0
  fi

  echo "ERROR: Neither docker nor podman was found in PATH." >&2
  exit 1
}

remove_all() {
  echo "Removing GEOINT demo resources..."
  if kubectl get namespace "$NAMESPACE" >/dev/null 2>&1; then
    kubectl delete namespace "$NAMESPACE" --wait=true
    echo "Namespace '$NAMESPACE' deleted."
  else
    echo "Namespace '$NAMESPACE' not found. Nothing to remove."
  fi
}

build_and_push_images() {
  local registry="$1"
  local cli
  cli="$(pick_container_cli)"

  local frontend_image="${registry}/${FRONTEND_IMAGE_NAME}:${IMAGE_TAG}"
  local rag_api_image="${registry}/${RAG_API_IMAGE_NAME}:${IMAGE_TAG}"

  echo "Building frontend image (${frontend_image}) with ${cli}..."
  "$cli" build -t "$frontend_image" ./frontend
  echo "Pushing frontend image (${frontend_image})..."
  "$cli" push "$frontend_image"

  echo "Building RAG API image (${rag_api_image}) with ${cli}..."
  "$cli" build -t "$rag_api_image" ./rag-api
  echo "Pushing RAG API image (${rag_api_image})..."
  "$cli" push "$rag_api_image"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --remove)
      REMOVE_ALL="true"
      shift
      ;;
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

if [[ "$REMOVE_ALL" == "true" ]]; then
  if [[ "$REGISTRY_ONLY" == "true" || -n "$REGISTRY_HOST" ]]; then
    echo "ERROR: --remove cannot be combined with --registry-only or --registry-host." >&2
    exit 1
  fi
  remove_all
  exit 0
fi

echo "[1/11] Creating namespace and foundational objects..."
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/secrets.yaml
kubectl apply -f k8s/configmaps/postgis-init.yaml
kubectl apply -f k8s/configmaps/geoserver-init.yaml
kubectl apply -f k8s/configmaps/nginx-config.yaml

echo "[2/11] Deploying internal Docker registry..."
kubectl apply -f k8s/registry/pvc.yaml
kubectl apply -f k8s/registry/deployment.yaml
kubectl apply -f k8s/registry/service.yaml
kubectl apply -f k8s/registry/ingress.yaml
kubectl -n "$NAMESPACE" rollout status deployment/internal-registry --timeout=300s

REGISTRY_INGRESS_HOST="registry.geoint-demo.local"
REGISTRY_EXTERNAL_HINT="${REGISTRY_INGRESS_HOST}:443"

if [[ "$REGISTRY_ONLY" == "true" ]]; then
  echo ""
  echo "Internal registry is ready."
  echo "Registry ingress details:"
  kubectl -n "$NAMESPACE" get ingress internal-registry-ingress
  echo ""
  echo "Registry endpoint via ingress host: ${REGISTRY_EXTERNAL_HINT}"
  echo "Add this host entry to your workstation and cluster nodes as needed:"
  echo "  <INGRESS_IP_OR_DNS> ${REGISTRY_INGRESS_HOST}"
  echo "IMPORTANT: configure k8s/secrets.yaml internal-registry-tls with a real cert/key for ${REGISTRY_INGRESS_HOST}."
  echo "Then trust that certificate on your Docker/Podman client and cluster node runtimes."
  echo "Then rerun ./deploy.sh --registry-host <host[:port]> to build/push custom images and deploy apps."
  exit 0
fi

if [[ -z "$REGISTRY_HOST" ]]; then
  echo "ERROR: --registry-host is required for full deploy so custom images can be built and pushed." >&2
  echo "Example: ./deploy.sh --registry-host ${REGISTRY_EXTERNAL_HINT}" >&2
  exit 1
fi

echo "[3/11] Building and pushing custom app images..."
build_and_push_images "$REGISTRY_HOST"

echo "[4/11] Deploying PostGIS..."
kubectl apply -f k8s/postgis/pvc.yaml
kubectl apply -f k8s/postgis/deployment.yaml
kubectl apply -f k8s/postgis/service.yaml
kubectl -n "$NAMESPACE" rollout status deployment/postgis --timeout=300s

echo "[5/11] Deploying GeoServer..."
kubectl apply -f k8s/geoserver/pvc.yaml
kubectl apply -f k8s/geoserver/deployment.yaml
kubectl apply -f k8s/geoserver/service.yaml
kubectl -n "$NAMESPACE" rollout status deployment/geoserver --timeout=600s

echo "[6/11] Running GeoServer initialization job (workspace/datastore/layers)..."
kubectl apply -f k8s/geoserver/init-job.yaml
kubectl -n "$NAMESPACE" wait --for=condition=complete job/geoserver-init-job --timeout=480s

echo "[7/11] Deploying ChromaDB..."
kubectl apply -f k8s/chromadb/pvc.yaml
kubectl apply -f k8s/chromadb/deployment.yaml
kubectl apply -f k8s/chromadb/service.yaml
kubectl -n "$NAMESPACE" rollout status deployment/chromadb --timeout=300s

echo "[8/11] Deploying RAG API..."
kubectl apply -f k8s/rag-api/deployment.yaml
kubectl apply -f k8s/rag-api/service.yaml
kubectl -n "$NAMESPACE" set image deployment/rag-api \
  rag-api="${REGISTRY_HOST}/${RAG_API_IMAGE_NAME}:${IMAGE_TAG}"
kubectl -n "$NAMESPACE" rollout status deployment/rag-api --timeout=600s

echo "[9/11] Deploying frontend..."
kubectl apply -f k8s/frontend/deployment.yaml
kubectl apply -f k8s/frontend/service.yaml
kubectl -n "$NAMESPACE" set image deployment/geoint-frontend \
  frontend="${REGISTRY_HOST}/${FRONTEND_IMAGE_NAME}:${IMAGE_TAG}"
kubectl -n "$NAMESPACE" rollout status deployment/geoint-frontend --timeout=300s

echo "[10/11] Applying ingress and network policies..."
kubectl apply -f k8s/ingress.yaml
kubectl apply -f k8s/network-policies.yaml

echo "[11/11] Deployment completed."
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
echo "   Note: if push/pull fails with certificate errors, verify/trust the cert in secret/internal-registry-tls."
echo ""
echo "Custom app images built, pushed, and deployed from: ${REGISTRY_HOST}"
echo ""
echo "GeoServer credentials are in k8s/secrets.yaml."
echo ""
echo "Cluster objects summary:"
kubectl -n "$NAMESPACE" get pods,svc,ingress
