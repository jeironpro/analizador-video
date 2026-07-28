FROM python:3-slim

RUN apt-get update && apt-get install -y --no-install-recommends clamav clamav-daemon ffmpeg curl ca-certificates && \
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

CMD ["/bin/sh", "/opt/render/project/src/start.sh"]
