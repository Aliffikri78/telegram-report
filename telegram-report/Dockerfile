FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1     PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends     build-essential     libglib2.0-0     libgl1     tzdata     && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY app/requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install -r /app/requirements.txt

COPY app /app

VOLUME ["/data"]

ENV SAVE_ROOT=/data/photos     TIME_BEFORE_HOUR=12     TIME_AFTER_HOUR=15

EXPOSE 8080

ENTRYPOINT ["/bin/bash", "/app/start.sh"]
