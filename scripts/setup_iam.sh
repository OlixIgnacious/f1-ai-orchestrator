#!/bin/bash
# IAM setup for f1-orchestrator Cloud Run service.
# Run once after billing is restored, before deploying.
#
# Usage: bash scripts/setup_iam.sh

set -e

PROJECT=f1-command-center-dev
REGION=us-central1
SA=521055768390-compute@developer.gserviceaccount.com

echo "Setting up IAM for project: $PROJECT"
echo "Service account: $SA"
echo ""

# ── Core roles ───────────────────────────────────────────────────────────────
for ROLE in \
  roles/aiplatform.user \
  roles/documentai.apiUser \
  roles/storage.objectAdmin \
  roles/alloydb.client \
  roles/secretmanager.secretAccessor; do

  echo "Granting $ROLE..."
  gcloud projects add-iam-policy-binding "$PROJECT" \
    --member="serviceAccount:$SA" \
    --role="$ROLE" \
    --condition=None \
    --quiet
done

# ── GCS FUSE requires the service account to access the cache bucket ─────────
echo ""
echo "Granting storage access on cache bucket..."
gsutil iam ch \
  "serviceAccount:$SA:roles/storage.objectAdmin" \
  "gs://f1-command-center-dev-f1-cache"

echo "Granting storage access on regulations bucket..."
gsutil iam ch \
  "serviceAccount:$SA:roles/storage.objectAdmin" \
  "gs://f1-command-center-dev-f1-regulations"

# ── Secret Manager secrets (create if they don't exist) ──────────────────────
echo ""
echo "Ensuring Secret Manager secrets exist..."
for SECRET in DOCUMENT_AI_PROCESSOR_ID; do
  if ! gcloud secrets describe "$SECRET" --project="$PROJECT" &>/dev/null; then
    echo "  Creating placeholder secret: $SECRET"
    echo -n "PLACEHOLDER" | gcloud secrets create "$SECRET" \
      --project="$PROJECT" \
      --replication-policy="automatic" \
      --data-file=-
    echo "  ⚠  Update $SECRET value once Document AI processor is created:"
    echo "     echo -n 'YOUR_PROCESSOR_ID' | gcloud secrets versions add $SECRET --data-file=-"
  else
    echo "  ✓ $SECRET already exists"
  fi
done

echo ""
echo "IAM setup complete ✓"
