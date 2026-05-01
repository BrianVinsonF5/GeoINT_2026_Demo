#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

NAMESPACE="geoint-demo"
GHCR_OWNER="brianvinsonf5"
GHCR_REGISTRY="ghcr.io"
GHCR_PULL_SECRET_NAME="ghcr-pull-secret"
REMOVE_ALL="false"

GITHUB_USERNAME="${GITHUB_USERNAME:-}"
GITHUB_PAT="${GITHUB_PAT:-}"

FRONTEND_IMAGE_NAME="geoint-frontend"
RAG_API_IMAGE_NAME="geoint-rag-api"
IMAGE_TAG="1.0.4"

usage() {
  cat <<USAGE
Usage: ./deploy.sh [options]

Options:
  --remove                     Remove everything deployed by this script
                               (deletes namespace: ${NAMESPACE}).
  --ghcr-owner <owner>         GitHub owner/org for GHCR images.
                               Example: brianvinsonf5 (default: ${GHCR_OWNER})
  --github-username <username> GitHub username for ghcr.io login (push).
                               Optional if already logged in.
  --github-pat <token>         GitHub PAT with packages:write scope for push.
                               Optional if already logged in.
  --image-tag <tag>            Image tag to build/push/deploy.
                               Default: ${IMAGE_TAG}
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
  local owner="$1"
  local cli
  cli="$(pick_container_cli)"

  local frontend_image="${GHCR_REGISTRY}/${owner}/${FRONTEND_IMAGE_NAME}:${IMAGE_TAG}"
  local rag_api_image="${GHCR_REGISTRY}/${owner}/${RAG_API_IMAGE_NAME}:${IMAGE_TAG}"

  if [[ -n "$GITHUB_USERNAME" && -n "$GITHUB_PAT" ]]; then
    echo "Logging into ${GHCR_REGISTRY} as ${GITHUB_USERNAME}..."
    echo "$GITHUB_PAT" | "$cli" login "$GHCR_REGISTRY" -u "$GITHUB_USERNAME" --password-stdin
  else
    echo "Skipping GHCR login (expecting existing docker/podman login)."
  fi

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
    --ghcr-owner)
      GHCR_OWNER="${2:-}"
      [[ -n "$GHCR_OWNER" ]] || { echo "ERROR: --ghcr-owner requires a value." >&2; exit 1; }
      shift 2
      ;;
    --github-username)
      GITHUB_USERNAME="${2:-}"
      [[ -n "$GITHUB_USERNAME" ]] || { echo "ERROR: --github-username requires a value." >&2; exit 1; }
      shift 2
      ;;
    --github-pat)
      GITHUB_PAT="${2:-}"
      [[ -n "$GITHUB_PAT" ]] || { echo "ERROR: --github-pat requires a value." >&2; exit 1; }
      shift 2
      ;;
    --image-tag)
      IMAGE_TAG="${2:-}"
      [[ -n "$IMAGE_TAG" ]] || { echo "ERROR: --image-tag requires a value." >&2; exit 1; }
      shift 2
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
  remove_all
  exit 0
fi

echo "[1/9] Creating namespace and foundational objects..."
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/secrets.yaml
kubectl apply -f k8s/configmaps/postgis-init.yaml
kubectl apply -f k8s/configmaps/geoserver-init.yaml
kubectl apply -f k8s/configmaps/nginx-config.yaml

echo "[2/9] Building and pushing custom app images to GHCR..."
build_and_push_images "$GHCR_OWNER"

echo "[3/9] Deploying PostGIS..."
kubectl apply -f k8s/postgis/pvc.yaml
kubectl apply -f k8s/postgis/deployment.yaml
kubectl apply -f k8s/postgis/service.yaml
kubectl -n "$NAMESPACE" rollout status deployment/postgis --timeout=300s

echo "[4/9] Deploying GeoServer..."
kubectl apply -f k8s/geoserver/pvc.yaml
kubectl apply -f k8s/geoserver/deployment.yaml
kubectl apply -f k8s/geoserver/service.yaml
kubectl -n "$NAMESPACE" rollout status deployment/geoserver --timeout=600s

echo "[5/9] Running GeoServer initialization job..."
kubectl apply -f k8s/geoserver/init-job.yaml
kubectl -n "$NAMESPACE" wait --for=condition=complete job/geoserver-init-job --timeout=480s

echo "[6/9] Deploying ChromaDB..."
kubectl apply -f k8s/chromadb/pvc.yaml
kubectl apply -f k8s/chromadb/deployment.yaml
kubectl apply -f k8s/chromadb/service.yaml
kubectl -n "$NAMESPACE" rollout status deployment/chromadb --timeout=300s

echo "[7/9] Deploying RAG API..."
kubectl apply -f k8s/rag-api/deployment.yaml
kubectl apply -f k8s/rag-api/service.yaml
kubectl -n "$NAMESPACE" set image deployment/rag-api \
  rag-api="${GHCR_REGISTRY}/${GHCR_OWNER}/${RAG_API_IMAGE_NAME}:${IMAGE_TAG}"
kubectl -n "$NAMESPACE" rollout status deployment/rag-api --timeout=600s

echo "[8/9] Deploying frontend..."
kubectl apply -f k8s/frontend/deployment.yaml
kubectl apply -f k8s/frontend/service.yaml
kubectl -n "$NAMESPACE" set image deployment/geoint-frontend \
  frontend="${GHCR_REGISTRY}/${GHCR_OWNER}/${FRONTEND_IMAGE_NAME}:${IMAGE_TAG}"
kubectl -n "$NAMESPACE" rollout status deployment/geoint-frontend --timeout=300s

echo "[9/9] Applying ingress and network policies..."
kubectl apply -f k8s/ingress.yaml
kubectl apply -f k8s/network-policies.yaml

echo ""
echo "Deployment completed."
echo "GHCR images deployed from: ${GHCR_REGISTRY}/${GHCR_OWNER}"
echo "Kubernetes pull secret expected: ${GHCR_PULL_SECRET_NAME}"
echo ""
kubectl -n "$NAMESPACE" get pods,svc,ingress
