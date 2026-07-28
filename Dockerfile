FROM python:3-slim

RUN apt-get update && apt-get install -y --no-install-recommends clamav ffmpeg curl ca-certificates && \
    mkdir -p /var/lib/clamav && \
    curl -sL --max-time 120 -A "ClamAV/1.4" -o /var/lib/clamav/main.cvd http://db.local.clamav.net/main.cvd && \
    chmod 644 /var/lib/clamav/main.cvd && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/render/project/src

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY clamd.conf /etc/clamav/clamd.conf

RUN mkdir -p /var/run/clamav && chmod 755 /var/run/clamav

ENV RENDER=true

CMD mkdir -p /var/run/clamav && \
    clamd --config-file /etc/clamav/clamd.conf &
    until clamdscan --ping --config-file /etc/clamav/clamd.conf 2>/dev/null; do sleep 1; done && \
    gunicorn app:app --bind 0.0.0.0:$PORT --timeout 300 --workers 1 --worker-class sync
