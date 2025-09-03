#
# Licensed to the Apache Software Foundation (ASF) under one or more
# contributor license agreements.  See the NOTICE file distributed with
# this work for additional information regarding copyright ownership.
# The ASF licenses this file to You under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance with
# the License.  You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

######################################################################
# Node stage to deal with static asset construction
######################################################################
ARG PY_VER=3.9-slim-bookworm

# if BUILDPLATFORM is null, set it to 'amd64' (or leave as is otherwise).
ARG BUILDPLATFORM=${BUILDPLATFORM:-amd64}
FROM --platform=${BUILDPLATFORM} node:16-bookworm-slim AS superset-node

ARG NPM_BUILD_CMD="build"

RUN apt-get update -qq \
    && apt-get install -yqq --no-install-recommends \
        build-essential \
        python3

ENV BUILD_CMD=${NPM_BUILD_CMD} \
    PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true

# NPM ci first, as to NOT invalidate previous steps except for when package.json changes
WORKDIR /app/superset-frontend

RUN --mount=type=bind,target=/frontend-mem-nag.sh,src=./repo/docker/frontend-mem-nag.sh \
    /frontend-mem-nag.sh

RUN --mount=type=bind,target=./package.json,src=./repo/superset-frontend/package.json \
    --mount=type=bind,target=./package-lock.json,src=./repo/superset-frontend/package-lock.json \
    npm ci

COPY ./repo/superset-frontend ./

# Add our boxplot
COPY ./box-plot-overrides ./plugins/plugin-chart-echarts/
RUN rm -f ./plugins/plugin-chart-echarts/src/BoxPlot/controlPanel.ts

# This seems to be the most expensive step
RUN npm run ${BUILD_CMD}

######################################################################
# Final lean image...
######################################################################
FROM python:${PY_VER} AS lean

WORKDIR /app
ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    SUPERSET_ENV=production \
    FLASK_APP="superset.app:create_app()" \
    PYTHONPATH="/app/pythonpath" \
    SUPERSET_HOME="/app/superset_home" \
    SUPERSET_PORT=8088

RUN mkdir -p ${PYTHONPATH} superset/static superset-frontend apache_superset.egg-info requirements \
    && useradd --user-group -d ${SUPERSET_HOME} -m --no-log-init --shell /bin/bash superset \
    && apt-get update -qq && apt-get install -yqq --no-install-recommends \
        build-essential \
        curl \
        default-libmysqlclient-dev \
        libsasl2-dev \
        libsasl2-modules-gssapi-mit \
        libpq-dev \
        libecpg-dev \
        libldap2-dev \
    && touch superset/static/version_info.json \
    && chown -R superset:superset ./* \
    && rm -rf /var/lib/apt/lists/*

# Copying configs. No need for a volume
COPY ./repo/docker/pythonpath_dev/superset_config.py ./superset_config_docker.py ./pythonpath_docker/

COPY --chown=superset:superset ./repo/setup.py ./repo/MANIFEST.in ./repo/README.md ./
# setup.py uses the version information in package.json
COPY --chown=superset:superset ./repo/superset-frontend/package.json superset-frontend/
RUN --mount=type=bind,target=./requirements/local.txt,src=./repo/requirements/local.txt \
    --mount=type=bind,target=./requirements/development.txt,src=./repo/requirements/development.txt \
    --mount=type=bind,target=./requirements/base.txt,src=./repo/requirements/base.txt \
    --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache -r requirements/local.txt

COPY --chown=superset:superset --from=superset-node /app/superset/static/assets superset/static/assets
## Lastly, let's install superset itself
COPY --chown=superset:superset ./repo/superset superset
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache -e . \
    && flask fab babel-compile --target superset/translations \
    && chown -R superset:superset superset/translations

COPY --chmod=755 ./repo/docker/run-server.sh /usr/bin/

# copy scripts to start containers to avoid volumes
COPY --chmod=755 ./repo/docker/docker-bootstrap.sh ./repo/docker/docker-init.sh ./docker/

USER superset
HEALTHCHECK CMD curl -f "http://localhost:$SUPERSET_PORT/health"

EXPOSE ${SUPERSET_PORT}

CMD ["/usr/bin/run-server.sh"]


