FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    patch \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN git clone https://github.com/pallets/click.git /app \
    && cd /app && git checkout 934813e4d421071a1b3db3973c02fe2721359a6e
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -e . "pytest<9"
