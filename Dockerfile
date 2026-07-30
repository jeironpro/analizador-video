FROM python:3.14-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    clamav \
    clamav-freshclam \
    ffmpeg \
    ca-certificates \
    && freshclam \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 appuser

WORKDIR /opt/render/project/src

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chown -R appuser:appuser /opt/render/project/src

ENV RENDER=true

USER appuser

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:${PORT:-8080}/health')" || exit 1

CMD ["/bin/sh", "/opt/render/project/src/start.sh"]
