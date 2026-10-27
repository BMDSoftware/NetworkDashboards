FROM python:3.9-bookworm

# Install Debian packages
RUN apt-get update --allow-releaseinfo-change && \
    apt-get install -y wait-for-it && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Node packages
COPY package.json .
RUN npm install

# Install Python packages
COPY requirements.txt .

RUN pip install --no-cache "setuptools<80" "pip<24.1" && \
    pip install --no-cache -r requirements.txt

EXPOSE 8000

COPY . .

CMD ./docker-entrypoint.sh
