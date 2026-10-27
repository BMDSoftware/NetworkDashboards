# Installation, Setup & Troubleshooting Guide 

This document provides a comprehensive guide for setting up and running the **BMD Software NetworkDashboards** development environment using Docker, along with a detailed record of technical issues encountered during initial execution and their resolutions.

---

## 1. Prerequisites & Environment Configuration

Currently, we use docker to deploy our environment

### First Steps

1. Clone the repository with the command `git clone --recurse-submodules https://github.com/EHDEN/NetworkDashboards`. If you already cloned the repository without the `--recurse-submodules` option, run `git submodule update --init` to fetch the superset submodule.

2. Create a `.env` file on the `docker` directory, using `.env-example` as a reference, setting all necessary environment variables (`SUPERSET_MAPBOX_API_KEY` and `DASHBOARD_VIEWER_SECRET_KEY`).

   2.1 If you will use this application as a third-party application and will iframe it, set the variable `SINGLE_APPLICATION_MODE` to `False` and define the host of the main application on the variable `MAIN_APPLICATION_HOST`. Also make sure to add this last host to the list of `ALLOWED_HOSTS`.

### Dashboard Viewer setup

1. If you wish to expose the dashboard viewer app through a specific domain(s) you must add it/them to the `ALLOWED_HOSTS` list on file `dashboard_viewer/dashboard_viewer/settings.py` and remove the `'*'` entry.

## 2. Docker Compose Environments: Production vs. Development

### Production vs. Development Compose Files

The architecture supports two deployment configurations depending on the target environment:

* **Production Environment (`docker-compose.yml`):**  
  Uses pre-configured production images or service definitions. It runs without local volume mounts for application source code, ensuring stability, security, and reproducibility across deployments.

* **Development Environment (`docker-compose-dev.yml`):**  
  Tailored for local feature development, debugging, and testing. It mounts local repository directories (such as `../dashboard_viewer`) directly into the running containers as live volumes. This enables real-time code reloading without needing to rebuild the entire container image for every code change.

### Image Architecture and Local Loading

In **development mode**, the services map to the base service images defined in the compose configurations:

* **Dashboard Viewer**
* **Apache Superset**
* **PostgreSQL Database**
* **Redis Cache**
* **Nginx Reverse Proxy**

When running `docker-compose -f docker-compose-dev.yml up` or `build`, Docker downloads or builds locally cached instances of these base images. If you have access to local image archive files (`.tar.gz`), you can import them manually into Docker prior to startup using `docker load -i <image_archive>.tar.gz`.

---

## 3. Step-by-Step Environment Setup

All step-by-step commands in this guide target the **local development** setup using `-f docker-compose-dev.yml`. 

> **Note on Command Syntax:**  
> The command workflow for production (`docker-compose.yml`) and development (`docker-compose-dev.yml`) is essentially the same. If running in production, you simply omit the `-f docker-compose-dev.yml` flag (letting Docker use the default `docker-compose.yml`) and skip the local `build` step.

For **local development**, follow this 5-step sequence in PowerShell/Terminal to build, initialize, and start all containers:

### Step 1: Clean Environment (Optional)

Stop existing containers and purge corrupted volumes or orphan containers:

```powershell
docker-compose -f docker-compose-dev.yml down -v --remove-orphans
```

### Step 2: Build & Start Containers

Because pre-built images may not be available in a remote registry, you should build the local images prior to starting the environment:

```powershell
docker-compose -f docker-compose-dev.yml build --no-cache dashboard
docker-compose -f docker-compose-dev.yml up -d 
```

### Step 3: Base Database Initialization

Execute initialization scripts to run Django migrations and set up Superset permissions:

```powershell
docker-compose -f docker-compose-dev.yml run --rm dashboard ./docker-init.sh
```

```powershell
docker-compose -f docker-compose-dev.yml run --rm superset-init
```

### Step 4: Import Athena Concepts Table

The concepts table is not in the repository due to its dimension, therefore we use directly the Postgres console to insert this table in the installation.

1. Get your concept csv file from [Athena](https://athena.ohdsi.org/)

2. Copy the file into postgres container

   ```powershell
   docker cp concept.csv dashboard_viewer-postgres-1:/tmp/
   ```

3. Enter in the postgres container:

   ```powershell
   docker exec -it dashboard_viewer-postgres-1 bash
   ```

4. Enter in the `achilles`  database (value of the variable `POSTGRES_ACHILLES_DB` on the .env file) with the `root` user (value of the variable `POSTGRES_ROOT_USER` on the .env file):

   ```sql
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
   CREATE INDEX concept_concept_id_text ON concept ((concept_id::text));
   ```

8. Set the owner of the `concept` table to the `achilles` user (value of the variable `POSTGRES_ACHILLES_USER` on the .env file):

   ```sql
   ALTER TABLE concept OWNER TO achilles;
   ```
   
9. Exit psql (`\q`) and the container shell (`exit`).
   
### Step 5: Post-Setup & Asset compilation

In development mode, ensure node modules are present in the mounted local directory before collecting static assets:

```powershell
docker-compose -f docker-compose-dev.yml run --rm dashboard npm install
docker-compose -f docker-compose-dev.yml run --rm dashboard python manage.py refresh_mat_views
docker-compose -f docker-compose-dev.yml run --rm dashboard python manage.py collectstatic --noinput
```

---

## 4. Troubleshooting Matrix

To prevent Python package incompatibilities and missing static asset errors, the following modifications were implemented in build scripts and runtime configurations.

| Error Message / Symptom | Root Cause | Resolution |
| :--- | :--- | :--- |
| **`FileNotFoundError: [Errno 2] No such file or directory: '/app/node_modules'`** | `collectstatic` failed looking for `node_modules`, missing from the mounted local volume. | Execute `docker-compose -f docker-compose-dev.yml run --rm dashboard npm install` before running `collectstatic`. |
| **`Missing Image / Image Build Failure`** | Container failed to start because base images were not built locally or pre-loaded. | Ensure you execute `docker-compose -f docker-compose-dev.yml build --no-cache dashboard` before bringing up the services, or manually load offline image archives using `docker load -i <image_name>.tar.gz`. |

---

## 5. Application Endpoints

Once all services are running:

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
```
