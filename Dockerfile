FROM python:3-slim

RUN apt-get update && apt-get install -y --no-install-recommends clamav ffmpeg wget ca-certificates && \
    mkdir -p /var/lib/clamav && \
    wget -q -t 3 -O /var/lib/clamav/main.cvd https://packages.clamav.net/main.cvd && \
    wget -q -t 3 -O /var/lib/clamav/daily.cvd https://packages.clamav.net/daily.cvd && \
    wget -q -t 3 -O /var/lib/clamav/bytecode.cvd https://packages.clamav.net/bytecode.cvd && \
    chmod 644 /var/lib/clamav/*.cvd && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/render/project/src

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV RENDER=true

CMD gunicorn app:app --bind 0.0.0.0:$PORT --timeout 300 --workers 1 --worker-class sync
