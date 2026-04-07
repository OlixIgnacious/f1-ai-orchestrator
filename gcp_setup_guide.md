# GCP Setup Guide for GitHub CI/CD

Before your GitHub Action can deploy to Cloud Run, you must set up the necessary infrastructure and security permissions in your GCP project: `f1-command-center-dev`.

## 1. Enable Required APIs

Run this command to enable the necessary services:

```bash
gcloud services enable \
    artifactregistry.googleapis.com \
    run.googleapis.com \
    iam.googleapis.com \
    secretmanager.googleapis.com \
    cloudbuild.googleapis.com \
    alloydb.googleapis.com
```

## 2. Create Artifact Registry

Create a repository to store your Docker images:

```bash
gcloud artifacts repositories create f1-command-center \
    --repository-format=docker \
    --location=us-central1 \
    --description="Docker repository for F1 AI Orchestrator"
```

## 3. Set up Workload Identity Federation

This allows GitHub to securely deploy to GCP without needing a Service Account Key.

### Create the Identity Pool and Provider

```bash
# Create the pool
gcloud iam workload-identity-pools create github-pool \
    --location="global" \
    --display-name="GitHub Pool"

# Create the provider
gcloud iam workload-identity-pools providers create-oidc github-provider \
    --location="global" \
    --workload-identity-pool="github-pool" \
    --display-name="GitHub Provider" \
    --attribute-mapping="google.subject=assertion.sub,attribute.actor=assertion.actor,attribute.repository=assertion.repository" \
    --attribute-condition="attribute.repository == 'olixignacious/f1-ai-orchestrator'" \
    --issuer-uri="https://token.actions.githubusercontent.com"
```

### Create and Bind the Service Account

```bash
# Create the Service Account
gcloud iam service-accounts create f1-deployer \
    --display-name="F1 Deployment Service Account"

# Allow GitHub to impersonate the Service Account
# REPLACE 'your-username/your-repo' with your actual GitHub repo name
export REPO="olixignacious/f1-ai-orchestrator"
export PROJECT_ID="f1-command-center-dev"

gcloud iam service-accounts add-iam-policy-binding "f1-deployer@${PROJECT_ID}.iam.gserviceaccount.com" \
    --project="${PROJECT_ID}" \
    --role="roles/iam.workloadIdentityUser" \
    --member="principalSet://iam.googleapis.com/projects/$(gcloud projects list --filter="projectId:${PROJECT_ID}" --format="value(projectNumber)")/locations/global/workloadIdentityPools/github-pool/attribute.repository/${REPO}"
```

### Grant Permissions to the Service Account

```bash
# General permissions for deployment
gcloud projects add-iam-policy-binding ${PROJECT_ID} \
    --member="serviceAccount:f1-deployer@${PROJECT_ID}.iam.gserviceaccount.com" \
    --role="roles/run.admin"

gcloud projects add-iam-policy-binding ${PROJECT_ID} \
    --member="serviceAccount:f1-deployer@${PROJECT_ID}.iam.gserviceaccount.com" \
    --role="roles/iam.serviceAccountUser"

gcloud projects add-iam-policy-binding ${PROJECT_ID} \
    --member="serviceAccount:f1-deployer@${PROJECT_ID}.iam.gserviceaccount.com" \
    --role="roles/artifactregistry.writer"
```

## 4. Configure Secret Manager

Store your sensitive values securely.

```bash
# Create and add secrets
echo -n "your-alloydb-password" | gcloud secrets create ALLOYDB_PASSWORD --data-file=-
echo -n "your-encryption-key" | gcloud secrets create TOKEN_ENCRYPTION_KEY --data-file=-
echo -n "your-oauth-secret" | gcloud secrets create GOOGLE_OAUTH_CLIENT_SECRET --data-file=-

# Grant the Cloud Run service account access to read secrets
# (Assuming you use the default compute service account for Cloud Run)
gcloud secrets add-iam-policy-binding ALLOYDB_PASSWORD \
    --member="serviceAccount:$(gcloud projects list --filter="projectId:${PROJECT_ID}" --format="value(projectNumber)")-compute@developer.gserviceaccount.com" \
    --role="roles/secretmanager.secretAccessor"
# Repeat for other secrets
```

## 5. Enable AlloyDB Client on Cloud Run SA

```bash
gcloud projects add-iam-policy-binding ${PROJECT_ID} \
    --member="serviceAccount:$(gcloud projects list --filter="projectId:${PROJECT_ID}" --format="value(projectNumber)")-compute@developer.gserviceaccount.com" \
    --role="roles/alloydb.client"
```

Once these steps are completed, you can push your code to the `main` branch of your GitHub repository to trigger the first deployment.
