FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y curl ca-certificates build-essential && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://deb.nodesource.com/setup_18.x | bash - \
    && apt-get install -y nodejs

RUN curl -fsSL https://deb.nodesource.com/setup_18.x | bash - && apt-get install -y nodejs
COPY . /app
WORKDIR /app/frontend
RUN if [ -f package.json ]; then npm ci --silent && npm run build --silent; fi
WORKDIR /app/backend
RUN pip install --no-cache-dir -r /app/requirements.txt
ENV PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["/bin/bash", "./start.sh"]
