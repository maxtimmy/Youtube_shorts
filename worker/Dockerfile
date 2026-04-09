FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir openai-whisper

WORKDIR /work

CMD ["bash", "-c", "tail -f /dev/null"]