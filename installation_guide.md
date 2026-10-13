---
output: html_document
---


# Installation, Setup & Troubleshooting Guide (Development Environment)

This document provides a comprehensive guide for setting up and running the **BMD Software NetworkDashboards** development environment using Docker, along with a detailed record of technical issues encountered during initial execution and their resolutions.

---

## 1. Installation

Currently, we use docker to deploy our environment

### First Steps

1. Clone the repository with the command `git clone --recurse-submodules https://github.com/EHDEN/NetworkDashboards`. If you already cloned the repository without the `--recurse-submodules` option, run `git submodule update --init` to fetch the superset submodule.

2. Create a `.env` file on the `docker` directory, using `.env-example` as a reference, setting all necessary environment variables (`SUPERSET_MAPBOX_API_KEY` and `DASHBOARD_VIEWER_SECRET_KEY`).

   2.1 If you will use this application as a third-party application and will iframe it, set the variable `SINGLE_APPLICATION_MODE` to `False` and define the host of the main application on the variable `MAIN_APPLICATION_HOST`. Also make sure to add this last host to the list of `ALLOWED_HOSTS`.

### Dashboard Viewer setup

1. If you wish to expose the dashboard viewer app through a specific domain(s) you must add it/them to the `ALLOWED_HOSTS` list on file `dashboard_viewer/dashboard_viewer/settings.py` and remove the `'*'` entry.

2. In production, all of our docker services use prebuilt images present in DockerHub, however, if you are in development mode and are using the `docker-compose-dev.yml`, you should build the images for the dashboard containers with `docker-compose build`.

3. Set up the database and create an admin account for the dashboard viewer app: `docker-compose run --rm dashboard ./docker-init.sh`.

### Insert Concepts

The concepts table is not in the repository due to its dimension, therefore we use directly the Postgres console to insert this table in the installation.

1. Get your concept csv file from [Athena](https://athena.ohdsi.org/)

2. Copy the file into postgres container

   ```sh
   docker cp concept.csv dashboard_viewer_postgres_1:/tmp/
   ```

3. Enter in the postgres container:

   ```sh
   docker exec -it dashboard_viewer_postgres_1 bash
   ```

4. Enter in the `achilles`  database (value of the variable `POSTGRES_ACHILLES_DB` on the .env file) with the `root` user (value of the variable `POSTGRES_ROOT_USER` on the .env file):

   ```
   psql achilles root
   ```

5. Create the `concept` table

   ```sql
   CREATE TABLE concept (
     concept_id         INTEGER        NOT NULL,
     concept_name       VARCHAR(255)   NOT NULL,
     domain_id          VARCHAR(20)    NOT NULL,
     vocabulary_id      VARCHAR(20)    NOT NULL,
     concept_class_id   VARCHAR(20)    NOT NULL,
     standard_concept   VARCHAR(1)     NULL,
     concept_code       VARCHAR(50)    NOT NULL,
     valid_start_date   DATE           NOT NULL,
     valid_end_date     DATE           NOT NULL,
     invalid_reason     VARCHAR(1)     NULL
   );
   ```

6. Copy the CSV file content to the table (this could take a while)

   To get both `'` (single quotes) and `"` (double quotes) on the `concept_name` column we use a workaround by setting the quote character to one that should never be in the text. Here we used `\b` (backslash).

   ```sql
   COPY public.concept FROM '/tmp/concept.csv' WITH CSV HEADER
     DELIMITER E'\t' QUOTE E'\b';
   ```

7. Create index in table (this could take a while):

   ```sql
   CREATE INDEX concept_concept_id_index ON concept (concept_id);
   CREATE INDEX concept_concept_name_index ON concept (concept_name);
   ```

8. Set the owner of the `concept` table to the `achilles` user (value of the variable `POSTGRES_ACHILLES_USER` on the .env file):

   ```
   ALTER TABLE concept OWNER TO achilles
   ```

---

## 2. Docker Images and Service Mapping

The project architecture relies on pre-built images hosted on Docker Hub (or built locally from project contexts):

* **Dashboard Viewer:** `bmdsoftware/dashboard:5.0.3-RC`
* **Apache Superset:** `bmdsoftware/superset:5.0.2`
* **PostgreSQL Database:** `postgres:10`
* **Redis Cache:** `redis:3.2`
* **Nginx Reverse Proxy:** `nginx:1.18.0`

### Image Pulling and Loading

When running `docker-compose up` or `build`, Docker downloads or reuses locally cached instances of these base images. In the development environment (`docker-compose-dev.yml`), local source code (`../dashboard_viewer`) is mounted directly into running containers via volume bindings to allow.

If you have access to these instances, you can load them manually, as shown in this example.

```powershell
    docker load -i bmdsoftware_superset_5.0.2.tar.gz
    docker load -i bmdsoftware_dashboards_5.0.3-RC.tar.gz
```

---

## 3. Step-by-Step Environment Setup

For dashboard and superset setup you can follow this sequence in PowerShell/Terminal to build, initialize, and start all containers:


1. **(Optional)** Stop existing containers and purge corrupted volumes/orphans

    ```powershell
    docker-compose -f docker-compose-dev.yml down -v --remove-orphans
    ```

2. Build the Dashboard image (using --no-cache to apply setuptools fixes)

    ```powershell
    docker-compose -f docker-compose-dev.yml build --no-cache dashboard
    ```

3. Start all services

    ```powershell
    docker-compose -f docker-compose-dev.yml up -d 
    ```

4. Execute the Django initialization script (database migrations)

    ```powershell
    docker-compose -f docker-compose-dev.yml run --rm dashboard ./docker-init.sh
    ```

5. Initialize Superset roles, permissions, and database

    ```powershell
    docker-compose -f docker-compose-dev.yml run --rm superset-init
    ```

6. Refresh materialized queries and collect static assets

    ```powershell
    docker-compose -f docker-compose-dev.yml run --rm dashboard python manage.py refresh_mat_views
    docker-compose -f docker-compose-dev.yml run --rm dashboard python manage.py collectstatic --noinput
    ```

---

## 4. Troubleshooting Matrix from previous version

To prevent Python package incompatibilities and missing static asset errors, the following modifications were implemented in build scripts and runtime configurations.

| Error Message / Symptom | Root Cause | Resolution |
| :--- | :--- | :--- |
| **`./docker-init.sh: line 2: $'\r': command not found or exec /app/docker-entrypoint-worker.sh: no such file or directory`** | Shell scripts were saved with Windows carriage returns (**CRLF**) instead of Unix line endings (**LF**). | Open scripts (`docker-init.sh`, `docker-entrypoint.sh`, `docker-entrypoint-worker.sh`) in VS Code, switch line endings from **CRLF to LF** in the bottom status bar, and save. |
| **`ModuleNotFoundError: No module named 'pkg_resources'`** | `setuptools >= 80` removed `pkg_resources`, breaking `django-model-utils==4.2.0`. | Pin `"setuptools<80"` in `dashboard_viewer/Dockerfile` and rebuild the image with `--no-cache`. |
| **`wait-for-it: timeout occurred after waiting 15 seconds for postgres:5432`** | PostgreSQL container entered a crash loop due to corrupted files in local directory `./volumes/postgres`. | Change `postgres` volume mapping in `docker-compose-dev.yml` to use a named volume (`postgres_data:/var/lib/postgresql/data`) and run `docker-compose down -v`. |
| **`FileNotFoundError: [Errno 2] No such file or directory: '/app/node_modules'`** | `collectstatic` failed looking for `node_modules`, missing from the mounted local volume. | Execute `docker-compose -f docker-compose-dev.yml run --rm dashboard npm install` before running `collectstatic`. |
| **`502 Bad Gateway (Nginx 1.18.0)`** | Nginx started before Django was ready, or `nginx` service was omitted from dev compose. | Include `nginx` service in `docker-compose-dev.yml` with `dashboard_static` volume and restart Nginx (`docker-compose restart nginx`) or access Django directly at port `8000`. |
| **`ModuleNotFoundError / Legacy Debian Package Deprecation (Python Environment Upgrade)`** | Older Python base images relied on deprecated Debian releases (e.g., Buster) or incompatible `setuptools` releases that broke dependencies such as `django-model-utils`. | Update the `Dockerfile` base image to `python:3.9-bookworm`, pin `setuptools<80` and `pip<24.1` during the pip installation step, and rebuild the service using `docker-compose -f docker-compose-dev.yml build --no-cache dashboard`. |
| **`Manifest for bmdsoftware/dashboard:5.0.3-RC not found: manifest unknown or Unexpected Image Runtime Error`** | Docker attempted to pull an incorrect, outdated, or mis-tagged base image from the remote repository, resulting in missing entrypoints or broken runtime dependencies. | Verify and update the image tags in `docker-compose-dev.yml` to point to the correct release (`bmdsoftware/dashboard:5.0.3-RC`), or manually load the verified local image archive using `docker load -i <image_name>.tar`. |

---

## 5. Application Endpoints

Once all services are running:

* **Dashboard Viewer (via Nginx Proxy):** [http://localhost](http://localhost)
* **Dashboard Viewer (Direct Django Dev Port):** [http://localhost:8000](http://localhost:8000)
* **Apache Superset:** [http://localhost:8088](http://localhost:8088)

---

## 6. Useful Maintenance & Operational Commands

Here are some essential management commands for daily development and environment control that can be helpful:

### Container Management & Status

```powershell
# View running services and their container status
docker-compose -f docker-compose-dev.yml ps

# View live aggregate logs from all running containers
docker-compose -f docker-compose-dev.yml logs -f

# Follow logs for a specific service (e.g., dashboard or postgres)
docker-compose -f docker-compose-dev.yml logs -f dashboard

# Stop all running containers without destroying data
docker-compose -f docker-compose-dev.yml stop

# Restart a specific service (useful after updating config files)
docker-compose -f docker-compose-dev.yml restart dashboard
docker-compose -f docker-compose-dev.yml restart nginx