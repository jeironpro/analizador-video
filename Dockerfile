FROM python:3-slim

RUN apt-get update && apt-get install -y --no-install-recommends clamav ffmpeg curl ca-certificates && \
    mkdir -p /var/lib/clamav && \
    curl -sL -A "ClamAV/1.4.3" -o /var/lib/clamav/main.cvd http://db.local.clamav.net/main.cvd && \
    curl -sL -A "ClamAV/1.4.3" -o /var/lib/clamav/daily.cvd http://db.local.clamav.net/daily.cvd && \
    curl -sL -A "ClamAV/1.4.3" -o /var/lib/clamav/bytecode.cvd http://db.local.clamav.net/bytecode.cvd && \
    chmod 644 /var/lib/clamav/*.cvd && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/render/project/src

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV RENDER=true

CMD gunicorn app:app --bind 0.0.0.0:$PORT --timeout 300 --workers 1 --worker-class sync
