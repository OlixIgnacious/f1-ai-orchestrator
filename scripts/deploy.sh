#!/bin/bash
# One-command deploy for f1-orchestrator.
# Builds the Docker image, pushes to Artifact Registry, then applies
# cloudrun-service.yaml with the new image tag.
#
# Usage:
#   bash scripts/deploy.sh              # deploy latest commit
#   bash scripts/deploy.sh --no-build   # apply YAML only (existing image)

set -e

PROJECT=f1-command-center-dev
REGION=us-central1
REPO=us-central1-docker.pkg.dev/$PROJECT/f1-command-center
IMAGE=$REPO/f1-orchestrator
SERVICE_YAML=cloudrun-service.yaml

NO_BUILD=false
for arg in "$@"; do
  [[ "$arg" == "--no-build" ]] && NO_BUILD=true
done

# ── Ensure correct project ────────────────────────────────────────────────────
echo "Setting project to $PROJECT..."
gcloud config set project "$PROJECT"

# ── Build & push image ────────────────────────────────────────────────────────
if [ "$NO_BUILD" = false ]; then
  SHA=$(git rev-parse --short HEAD)
  TAG="$IMAGE:$SHA"
  LATEST="$IMAGE:latest"

  echo ""
  echo "Building image: $TAG"
  docker build -t "$TAG" -t "$LATEST" .

  echo "Pushing image..."
  docker push "$TAG"
  docker push "$LATEST"
  echo "Image pushed ✓"
else
  # Use the latest image already in the registry
  TAG="$IMAGE:latest"
  echo "Skipping build — using $TAG"
fi

# ── Patch IMAGE_PLACEHOLDER in service YAML ───────────────────────────────────
echo ""
echo "Patching $SERVICE_YAML with image: $TAG"
PATCHED_YAML=$(mktemp /tmp/cloudrun-service-XXXXXX.yaml)
sed "s|IMAGE_PLACEHOLDER|$TAG|g" "$SERVICE_YAML" > "$PATCHED_YAML"

# ── Create GCS cache bucket if it doesn't exist ──────────────────────────────
if ! gsutil ls "gs://f1-command-center-dev-f1-cache" &>/dev/null; then
  echo "Creating GCS cache bucket..."
  gsutil mb -l "$REGION" "gs://f1-command-center-dev-f1-cache"
fi

# ── Apply service YAML ────────────────────────────────────────────────────────
echo "Deploying service..."
gcloud run services replace "$PATCHED_YAML" \
  --region="$REGION" \
  --project="$PROJECT"

rm -f "$PATCHED_YAML"

echo ""
echo "Deploy complete ✓"
echo "Service URL:"
gcloud run services describe f1-orchestrator \
  --region="$REGION" \
  --format="value(status.url)"
