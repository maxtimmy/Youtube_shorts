FROM python:3.11-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends docker.io \
    && rm -rf /var/lib/apt/lists/*

COPY dashboard /app

EXPOSE 8787

CMD ["python", "/app/server.py"]
