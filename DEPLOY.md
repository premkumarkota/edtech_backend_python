
# Deployment Guide: Google Cloud Run

This guide explains how to deploy the EdTech Backend API to Google Cloud Run using either **Direct GitHub Integration** (Recommended) or **Manual Docker Deployment**.

## Prerequisites

1.  **Google Cloud Project**: You need a Google Cloud Project with billing enabled.
2.  **APIs Enabled**: Enable the following APIs in your project:
    *   Cloud Run API
    *   Artifact Registry API
    *   Cloud Build API
    *   Cloud SQL Admin API (if using Cloud SQL)
3.  **Database**: You cannot use `localhost` in the cloud. You must set up a cloud database.
    *   **Option A (Recommended)**: Create a **Cloud SQL for PostgreSQL** instance in Google Cloud.
    *   **Option B**: Use a managed database service like Supabase, Neon, or Railway.

---

## 1. Prepare Your Application

We have already created the necessary files for you:
*   `Dockerfile`: Defines how to build the application container.
*   `.dockerignore`: Excludes unnecessary files to keep the image small.
*   `requirements.txt`: Lists dependencies.

**Important**: Ensure your `requirements.txt` includes `gunicorn` or `uvicorn[standard]` (already verified).

---

## 2. Generate/Update Database URL

Your cloud application needs to connect to your cloud database.
Construct your production `DATABASE_URL`. It usually looks like this:

*   **Cloud SQL (Private IP)**: `postgresql://user:password@10.x.x.x:5432/dbname`
*   **Supabase/Neon**: `postgresql://user:password@host:5432/dbname`

---

## 3. Deploy using "Connect Repository" (Direct GitHub)

This is the easiest method. Google Cloud Build will automatically build and deploy your app whenever you push to GitHub.

1.  **Push your code to GitHub**:
    ```bash
    git add .
    git commit -m "Prepare for deployment"
    git push origin main
    ```

2.  **Go to Google Cloud Run Console**:
    *   Click **"Create Service"**.
    *   Select **"Continuously deploy new revisions from a source repository"**.
    *   Click **"SET UP WITH CLOUD BUILD"**.

3.  **Configure Build**:
    *   **Repository Provider**: GitHub.
    *   **Repository**: Select your `edtech-backend` repository.
    *   **Branch**: `^main$` (or your production branch).
    *   **Build Type**: Go, Node.js, Python, Java, .NET, Ruby, PHP, or **Dockerfile** (Select **Dockerfile**).
    *   **Source location**: `/` (root directory).
    *   Click **"Save"**.

4.  **Configure Service**:
    *   **Service Name**: `edtech-backend` (or similar).
    *   **Region**: Choose a region close to your users (e.g., `us-central1` or `asia-south1`).
    *   **Authentication**: Select **"Allow unauthenticated invocations"** (public API).

5.  **Environment Variables (CRITICAL)**:
    *   Expand the **"Container, Networking, Security"** section.
    *   Go to the **"Variables & Secrets"** tab.
    *   Add the following variables:
        *   `DATABASE_URL`: `postgresql://user:password@host:port/dbname` (YOUR CLOUD DB URL)
        *   `SECRET_KEY`: `your-production-secret-key`
        *   `DEBUG`: `False`
    *   **Firebase**: If using Firebase, ensure your Cloud Run Service Account has the "Firebase Admin" role. We updated the code to use **Application Default Credentials (ADC)**, so you don't need to upload a JSON key file.

6.  **Deploy**:
    *   Click **"Create"**.
    *   Cloud Build will start building your container. Initial deployment may take a few minutes.

---

## 4. Deploy using Docker (Manual)

If you prefer to build locally and upload:

1.  **Install Google Cloud SDK** (`gcloud` CLI).
2.  **Login**:
    ```bash
    gcloud auth login
    gcloud config set project YOUR_PROJECT_ID
    ```
3.  **Build and Deploy**:
    ```bash
    gcloud run deploy edtech-backend --source . --region us-central1 --allow-unauthenticated
    ```
    *   Follow the prompts.
    *   When asked, set environment variables.

---

## Troubleshooting

*   **502 Bad Gateway**: Usually means the application failed to start. check the **Logs** tab in Cloud Run.
*   **Database Connection Error**:
    *   Ensure your database is publicly accessible (for Supabase/Neon) OR
    *   If using Cloud SQL, ensure the **Cloud SQL Auth Proxy** is enabled or you are using the correct connection details (Instance Connection Name).
