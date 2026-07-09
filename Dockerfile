FROM nvidia/cuda:11.8.0-devel-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 LC_ALL=C.UTF-8 LANG=C.UTF-8

# system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv python3-dev \
    build-essential git wget curl ca-certificates libgomp1 \
    libgl1 libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install --upgrade pip setuptools wheel

WORKDIR /app

# cache-friendly deps
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# app
COPY . /app

# avoid "permission denied"
RUN if [ -f /app/run_all.sh ]; then chmod +x /app/run_all.sh; fi

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT:-8080}/health" || exit 1

CMD ["bash","-lc","if [ -f /app/run_all.sh ]; then exec /app/run_all.sh; else exec python3 /app/web.py; fi"]
