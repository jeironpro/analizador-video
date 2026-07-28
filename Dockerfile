FROM python:3-slim

RUN apt-get update && apt-get install -y --no-install-recommends clamav ffmpeg && \
    freshclam --show-progress || true && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/render/project/src

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV RENDER=true

CMD gunicorn app:app --bind 0.0.0.0:$PORT --timeout 300 --workers 1 --worker-class sync
