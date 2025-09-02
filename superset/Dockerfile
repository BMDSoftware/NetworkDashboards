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
FROM --platform=${BUILDPLATFORM} node:16 AS superset-node

RUN echo "deb http://security.debian.org/debian-security bullseye-security main contrib non-free" > /etc/apt/sources.list

RUN apt update

ARG NPM_BUILD_CMD="build"

RUN cat /etc/apt/sources.list

RUN apt-get update \
    && apt-get install -yq --no-install-recommends \
        python3 \
        make \
        gcc \
        g++

ENV BUILD_CMD=${NPM_BUILD_CMD} \
    PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true

# NPM ci first, as to NOT invalidate previous steps except for when package.json changes
WORKDIR /app/superset-frontend

COPY ./repo/docker/frontend-mem-nag.sh /

RUN /frontend-mem-nag.sh

COPY ./repo/superset-frontend/package*.json ./

RUN npm ci 

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

ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    SUPERSET_ENV=production \
    FLASK_APP="superset.app:create_app()" \
    PYTHONPATH="/app/pythonpath" \
    SUPERSET_HOME="/app/superset_home" \
    SUPERSET_PORT=8088

RUN mkdir -p ${PYTHONPATH} \
    && useradd --user-group -d ${SUPERSET_HOME} -m --no-log-init --shell /bin/bash superset \
    && apt-get update -q \
    && apt-get install -yq --no-install-recommends \
        build-essential \
        curl \
        default-libmysqlclient-dev \
        libsasl2-dev \
        libsasl2-modules-gssapi-mit \
        libpq-dev \
        libecpg-dev \
        libldap2-dev \
    && rm -rf /var/lib/apt/lists/*

    # Copying configs. No need for a volume
COPY ./repo/docker/pythonpath_dev/superset_config.py ./superset_config_docker.py /app/pythonpath_docker/

COPY --chown=superset:superset ./repo/requirements/*.txt  /app/requirements/
COPY --chown=superset:superset ./repo/setup.py ./repo/MANIFEST.in ./repo/README.md /app/
# setup.py uses the version information in package.json
COPY --chown=superset:superset ./repo/superset-frontend/package.json /app/superset-frontend/

RUN cd /app && mkdir -p superset/static \
    && touch superset/static/version_info.json \
    && pip install --no-cache-dir -r requirements/local.txt

COPY --chown=superset:superset --from=superset-node /app/superset/static/assets /app/superset/static/assets
## Lastly, let's install superset itself
COPY --chown=superset:superset ./repo/superset /app/superset

RUN cd /app && chown -R superset:superset ./* \
    && pip install --no-cache-dir -e . \
    && flask fab babel-compile --target superset/translations

COPY --chmod=755 ./repo/docker/run-server.sh /usr/bin/

# copy scripts to start containers to avoid volumes
COPY ./repo/docker/docker-bootstrap.sh ./repo/docker/docker-init.sh /app/docker/

RUN chmod a+x /usr/bin/run-server.sh

WORKDIR /app
USER superset

HEALTHCHECK CMD curl -f "http://localhost:$SUPERSET_PORT/health"

EXPOSE ${SUPERSET_PORT}

CMD ["/usr/bin/run-server.sh"]


