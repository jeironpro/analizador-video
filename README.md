# VidScan

Aplicación web para escanear, validar y almacenar archivos de video. Utiliza ClamAV (daemon clamd) para antivirus y ffmpeg/ffprobe para análisis de codecs, metadatos y miniaturas, con procesamiento multi-worker vía Redis (RQ) y persistencia en PostgreSQL.

## Stack

| Componente       | Tecnología                                     |
|------------------|------------------------------------------------|
| Backend          | Flask + Gunicorn (gthread, multi-worker)       |
| Base de datos    | PostgreSQL / SQLite (local)                    |
| Cola             | Redis + RQ (RQ Workers)                        |
| Contenedor       | docker-compose (local) + Caddy (HTTP, :8080)   |
| Análisis         | ffprobe / ffmpeg (duración, bitrate, thumbnail)|
| Antivirus        | ClamAV daemon (clamd) con fallback clamscan    |
| Frontend         | HTML + CSS + JS vanilla (SSE en tiempo real)   |
| Calidad          | Ruff, type hints, pytest                       |

## Funcionalidades

- Subida múltiple de videos (50 MB – 500 MB) con arrastrar y soltar
- Validación client-side de tamaño antes de subir
- Reintento automático de upload (hasta 3 intentos)
- Cola de procesamiento persistente en PostgreSQL + Redis (RQ)
- Procesamiento concurrente con **N workers RQ** (escala horizontalmente)
- Validación de tamaño, tipo MIME (python-magic), codecs de video/audio, resolución, FPS, contenedor
- Escaneo antivirus con **clamd** (daemon en :3310) vía stream; fallback a `clamscan` local; un virus detectado rechaza el archivo
- Análisis enriquecido: duración, bitrate global, streams (codec/resolución/FPS/canales)
- **Miniaturas** generadas con ffmpeg (`/api/thumbnail/<id>`)
- Reintento automático de items fallidos gestionado por RQ (configurable, default 3)
- Detección de metadatos sospechosos
- Cálculo de hash SHA-256 por cada video almacenado
- Resultado formateado como bloque clave:valor en el terminal al finalizar
- Limpieza automática de sesiones expiradas (CleanupDaemon, corre una sola vez en el worker)
- Streaming de logs en tiempo real por SSE por cada item
- Actualización de la cola en vivo vía SSE global (sin polling)
- Limitación de tasa en upload (sliding window en Redis, default 20 req / 60s)
- Límite de items por sesión (default 20)
- Esqueletos shimmer (skeleton screens) mientras carga la cola y los videos
- Notificaciones toast (success, error, warning, info)
- Páginas de error personalizadas (404, 500)
- Descarga / eliminación de videos almacenados
- Sesiones anónimas compartibles por enlace (expiran tras inactividad)
- Apagado graceful (SIGTERM)
- Logging estructurado en JSON (`LOG_FORMAT=json`)

## Arquitectura

```diagram
                      ┌─────────────┐
                      │  Navegador  │
                      └──────┬──────┘
                             │ HTTP (Caddy :8080)
                       ┌─────┴─────┐
                       │   Caddy   │  → reverse proxy
                       └─────┬─────┘
                             │
                      ┌──────┴──────┐
                      │  Gunicorn   │  (N web workers, gthread)
                      │    Flask    │
                      └──────┬──────┘
                             │ enqueue
                      ┌──────┴──────┐
                      │    Redis    │  → cola RQ + rate limiter
                      └──────┬──────┘
                             │ consume
                      ┌──────┴──────┐
                      │  RQ Worker  │  × N (procesan videos)
                      │ (worker.py) │
                      └──────┬──────┘
                             │
        ┌────────────────────┼────────────────────┬───────────────┐
        │                    │                    │               │
 ┌──────┴──────┐      ┌──────┴──────┐      ┌──────┴──────┐  ┌─────┴──────┐
 │ PostgreSQL  │      │    Disco    │      │   clamd     │  │  ffmpeg/   │
 │ (queue +    │      │   /data/    │      │  (ClamAV)   │  │  ffprobe   │
 │ sessions +  │      │  uploads/   │      │   :3310     │  │            │
 │ videos)     │      │   temp/     │      │             │  │            │
 └─────────────┘      └─────────────┘      └─────────────┘  └────────────┘
```

Flujo: el web worker guarda el upload en `temp/`, registra el item en PostgreSQL y encola el `temp_id` en RQ. Un worker RQ ejecuta el pipeline (validación → clamd → ffprobe → thumbnail → movimiento a `uploads/` → hash), escribiendo logs y estado en PostgreSQL para el SSE.

## Despliegue con Docker (recomendado)

Servicios: `db` (PostgreSQL), `redis`, `clamav` (daemon clamd), `app` (gunicorn × 2), `worker` (RQ × 2), `caddy` (reverse proxy HTTP en :8080).

```bash
# Crea el archivo .env con las credenciales (ignorado por git)
cat > .env << EOF
SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
POSTGRES_PASSWORD=vidscan_local_db_pass
EOF

docker compose up --build
# Abrir http://localhost:8080 (local) o http://192.168.1.XX:8080 (LAN)
```

El contenedor caddy publica el puerto `8080:80` del host. La app se sirve por HTTP sin TLS (ideal para LAN).

### Variables de entorno

| Variable             | Obligatorio | Default                    | Descripción                                        |
|----------------------|-------------|----------------------------|----------------------------------------------------|
| `DATABASE_URL`       | Sí          | `sqlite:///videos.db`      | Conexión a PostgreSQL                              |
| `REDIS_URL`          | No          | `redis://localhost:6379/0` | Conexión a Redis                                   |
| `CLAMAV_HOST`        | No          | (vacío → clamscan local)   | Host del daemon clamd                              |
| `CLAMAV_PORT`        | No          | `3310`                     | Puerto de clamd                                    |
| `CLAMAV_MAX_MB`      | No          | `500`                      | Tamaño máx. escaneable (`--max-filesize`)          |
| `RQ_QUEUE`           | No          | `vidscan`                  | Nombre de la cola RQ                               |
| `RQ_WORKERS`         | No          | `1`                        | Nº de procesos worker (solo `start-worker.sh`)     |
| `WEB_WORKERS`        | No          | `2`                        | Nº de workers gunicorn                             |
| `SECRET_KEY`         | No          | Auto-generado              | Clave secreta de Flask                             |
| `UPLOAD_DIR`         | No          | `instance/`                | Directorio base para uploads y temp                |
| `SESSION_DAYS`       | No          | `7`                        | Días antes de expirar sesión inactiva              |
| `ITEM_TIMEOUT`       | No          | `600`                      | Timeout de job RQ (segundos)                       |
| `JOB_TIMEOUT`        | No          | `1200`                     | Timeout de job (override de `ITEM_TIMEOUT`)        |
| `MAX_RETRIES`        | No          | `3`                        | Reintentos máximos por item fallido                |
| `MAX_QUEUE_ITEMS`    | No          | `20`                       | Máximo de items en cola por sesión                 |
| `RATE_LIMIT_UPLOAD`  | No          | `20`                       | Máximo de uploads por ventana                      |
| `RATE_LIMIT_WINDOW`  | No          | `60`                       | Ventana de rate limiting (segundos)                |
| `LOG_FORMAT`         | No          | `text`                     | `json` para logging estructurado                   |
| `DEBUG`              | No          | `false`                    | `true` activa modo debug                           |

## Desarrollo local

```bash
git clone <repo>
cd analizador-video
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python app.py
# Abrir http://localhost:5001
```

Por defecto usa SQLite (`videos.db`). Para usar PostgreSQL/Redis, exporta `DATABASE_URL` y `REDIS_URL`.

### Workers

Para procesamiento asíncrono (recomendado), inicia Redis y un worker:

```bash
# terminal 1
python worker.py
# terminal 2
python app.py
```

Si Redis no está disponible, los jobs se procesan en línea dentro del request (fallback).

### Tests

```bash
pytest
```

### Calidad de código

```bash
ruff check .
```

### Pre-commit

```bash
pre-commit install
pre-commit run --all-files
```

## Scripts

| Script                          | Descripción                                                  |
|---------------------------------|--------------------------------------------------------------|
| `start.sh`                      | Web: espera Postgres/Redis, migraciones, gunicorn            |
| `start-worker.sh`               | Worker: espera Redis, migraciones, lanza RQ × N              |
| `worker.py`                     | Entrypoint de worker RQ (maneja fallos y cleanup)            |
| `docker-compose.yml`            | Entorno completo: db + redis + clamav + app + worker + caddy |
| `Caddyfile`                     | Reverse proxy HTTP por IP (:8080)                            |

## Licencia

MIT — ver [LICENSE](LICENSE).
