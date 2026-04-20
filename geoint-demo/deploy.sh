#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="geoint-demo"

echo "[1/9] Creating namespace and foundational objects..."
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/secrets.yaml
kubectl apply -f k8s/configmaps/postgis-init.yaml
kubectl apply -f k8s/configmaps/geoserver-init.yaml
kubectl apply -f k8s/configmaps/nginx-config.yaml

echo "[2/9] Deploying PostGIS..."
kubectl apply -f k8s/postgis/pvc.yaml
kubectl apply -f k8s/postgis/deployment.yaml
kubectl apply -f k8s/postgis/service.yaml
kubectl -n "$NAMESPACE" rollout status deployment/postgis --timeout=300s

echo "[3/9] Deploying GeoServer..."
kubectl apply -f k8s/geoserver/pvc.yaml
kubectl apply -f k8s/geoserver/deployment.yaml
kubectl apply -f k8s/geoserver/service.yaml
kubectl -n "$NAMESPACE" rollout status deployment/geoserver --timeout=600s

echo "[4/9] Running GeoServer initialization job (workspace/datastore/layers)..."
kubectl apply -f k8s/geoserver/init-job.yaml
kubectl -n "$NAMESPACE" wait --for=condition=complete job/geoserver-init-job --timeout=480s

echo "[5/9] Deploying ChromaDB..."
kubectl apply -f k8s/chromadb/pvc.yaml
kubectl apply -f k8s/chromadb/deployment.yaml
kubectl apply -f k8s/chromadb/service.yaml
kubectl -n "$NAMESPACE" rollout status deployment/chromadb --timeout=300s

echo "[6/9] Deploying RAG API..."
kubectl apply -f k8s/rag-api/deployment.yaml
kubectl apply -f k8s/rag-api/service.yaml
kubectl -n "$NAMESPACE" rollout status deployment/rag-api --timeout=600s

echo "[7/9] Deploying frontend..."
kubectl apply -f k8s/frontend/deployment.yaml
kubectl apply -f k8s/frontend/service.yaml
kubectl -n "$NAMESPACE" rollout status deployment/geoint-frontend --timeout=300s

echo "[8/9] Applying ingress and network policies..."
kubectl apply -f k8s/ingress.yaml
kubectl apply -f k8s/network-policies.yaml

echo "[9/9] Deployment completed."
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
echo "GeoServer credentials are in k8s/secrets.yaml."
echo ""
echo "Cluster objects summary:"
kubectl -n "$NAMESPACE" get pods,svc,ingress
