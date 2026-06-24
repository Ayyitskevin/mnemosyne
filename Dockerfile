FROM python:3.12-slim-bookworm

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libjpeg62-turbo zlib1g \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src
COPY templates ./templates

RUN pip install --no-cache-dir -e '.[r2]'

ENV MNEMOSYNE_DB=/data/mnemosyne.db
ENV MNEMOSYNE_UPLOAD_DIR=/data/uploads
ENV MNEMOSYNE_HOST=0.0.0.0
ENV MNEMOSYNE_PORT=8000

RUN mkdir -p /data/uploads

EXPOSE 8000

CMD ["python", "-m", "mnemosyne", "serve", "--port", "8000"]