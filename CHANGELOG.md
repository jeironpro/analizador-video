# Changelog

Todas las cambios notables en este proyecto se documentarán en este archivo.

El formato está basado en [Keep a Changelog](https://keepachangelog.com/),
y el proyecto adhiere a [Semantic Versioning](https://semver.org/).

## [0.7.0] — 2026-07-30

### Añadido
- Columna `sha256` en modelo `Video` y migración Alembic
- Cálculo de hash SHA-256 al almacenar cada video
- Bloque de resultado formateado (clave:valor) en el terminal al finalizar procesamiento
- Estilo `.result` en terminal (azul claro) para líneas de resultado
- Limpieza automática de items `done`/`error` de la cola (`_cleanup_done_items`)
- Pruebas unitarias para `RateLimiter`, SSE helpers, `_extract_fps`, `_check_suspicious_metadata` (19 tests)

### Cambiado
- `services/queue.py`: `_process_item` refactorizado en `_run_validation_step`, `_run_analysis_step`, `_store_video`
- `video_analyzer.py`: `analyze_video` refactorizado; extraídos `_ffprobe`, `_extract_fps`, `_check_suspicious_metadata`
- Límites de upload dinámicos desde `/api/config` en la zona de drag & drop
- Tarjetas de video muestran tipo MIME, resultado ClamAV y hash SHA-256
- Eliminados ~23 imports sin uso en todo el proyecto (ruff F401)

### Corregido
- Código muerto en `appendTerminal` (doble condición `complete`)
- Parches de tests apuntaban a `services.queue.magic` en vez de `services.validation.magic`

## [0.6.0] — 2026-07-30

### Añadido
- `services/config.py` con constantes centralizadas de validación (tamaños, MIME, retries, sesión)
- Endpoint `/api/config` que expone configuración al frontend
- Encabezados CSP (`Content-Security-Policy`, `X-Content-Type-Options`)
- Atributos `integrity` + `crossorigin` en CDN de Bootstrap, Bootstrap Icons y Google Fonts
- `services/rate_limiter.py`: clase `RateLimiter` (sliding window)
- `services/sse.py`: helpers `sse_step()`, `sse_complete()`, `sse_error()`
- `services/validation.py`: `validate_file_size()`, `validate_mime_type()`
- `services/system.py`: `_read_cgroup_mem()`, `_get_container_memory_total()`
- Migración Alembic `8438c06a97d5`: FK constraints, columnas JSON para `logs`/`result`
- `.dockerignore`

### Cambiado
- `app.py`: CSP via `@app.after_request`; importa de módulos extraídos; `_migrate_schema/sessions` eliminados
- `models.py`: ForeignKeys reales (`Video.session_id → sessions.code`, `QueueItem.session_id → sessions.code`); `QueueItem.logs`/`result` pasan a `db.JSON`
- `Dockerfile`: imagen `python:3.14-slim`, usuario no root (`appuser`), `HEALTHCHECK`, `freshclam`, sin `curl`/User-Agent
- `start.sh`: gunicorn con `--worker-class sync` en lugar de gevent
- `requirements.in`: eliminado `gevent`
- `services/queue.py`: importa de `services.validation`; `json.loads`/`json.dumps` eliminados gracias a `db.JSON`
- Fuente cambiada a Poppins en todas las templates
- Navbar "Analizador de Video" → "VidScan"
- Scroll de contenedor a viewport (`body{flex;min-height:100vh}`)
- Barra de progreso secuencial (por archivo, no salta entre archivos)
- Esqueletos solo en carga inicial (no en actualizaciones SSE)

### Corregido
- `SECRET_KEY`: warn si no está configurada (sin fallback silencioso)
- Dead CSS (`.min-h-0`, `.btn-sm-custom`) eliminado
- Terminal usa `'Courier New'` en lugar de `'JetBrains Mono'/'Fira Code'`

## [0.5.0] — 2026-07-29

### Añadido
- Configuración de Ruff en `pyproject.toml` (select I, W, E, UP, line-length 120)
- Type hints completos (`from __future__ import annotations`) en todos los módulos Python
- GitHub Actions CI (Python 3.12 + 3.13, lint + pytest)
- Pruebas unitarias para QueueManager (20 tests) y utilidades (17 tests)
- Pruebas de integración para Flask (18 tests)
- `conftest.py` con fixtures `app` y `qm`

### Cambiado
- `requirements.txt` incluye `ruff`

## [0.4.0] — 2026-07-29

### Añadido
- Rate limiting por sesión en `/api/upload` (sliding window, configurable)
- Límite de items en cola por sesión (`MAX_QUEUE_ITEMS`, default 20)
- Validación de tipo MIME server-side en endpoint de upload
- Validación de tamaño client-side (50 MB – 500 MB) con toast warning
- Reintento automático de upload (hasta 3 intentos)
- Esqueletos shimmer para cola y grilla de videos
- Notificaciones toast (success, error, warning, info)
- Páginas de error personalizadas (404, 500)

### Cambiado
- `app.py`: registrados errorhandlers 404 y 500
- `static/app.js`: skeleton screens, uploadWithRetry, showToast, handleFiles con validación
- `static/style.css`: estilos skeleton, toast, error-page
- `templates/index.html`: contenedor de toasts

## [0.3.0] — 2026-07-29

### Añadido
- Health check en `GET /health` (200 si DB responde, 503 si no)
- Timeout configurable para items en procesamiento (`ITEM_TIMEOUT`, default 600 s)
- Reintento automático de items fallidos (hasta `MAX_RETRIES`, default 3)
- Apagado graceful: reencola items en processing al recibir SIGTERM
- Migraciones Alembic para `sessions`, `video`, `queue_items`

### Cambiado
- `services/queue.py`: lógica de retry (`_fail_or_retry`), stale recovery, shutdown
- `app.py`: `_init_db()` intenta Alembic primero, fallback a `db.create_all()`

## [0.2.0] — 2026-07-29

### Añadido
- Script de backup (`scripts/backup.sh`) con configuración por env vars
- Logging estructurado en JSON (JsonFormatter, activo con `LOG_FORMAT=json`)
- Validación de configuración al inicio (`_validate_config()`)
- `docker-compose.yml` con servicios app, postgres, clamav
- `start.sh` con espera a PostgreSQL via socket check en Python

### Cambiado
- `app.py`: integrado JsonFormatter, `_validate_config()`, reestructurado logging

## [0.1.0] — 2026-07-29

### Añadido
- Aplicación Flask con rutas para sesiones, upload, cola, videos
- Modelos SQLAlchemy: `Session`, `Video`, `QueueItem`
- QueueManager con scheduler, ThreadPoolExecutor (max 1 worker), persistencia en DB
- Análisis de video con ffprobe (codecs, resolución, FPS, contenedor, metadatos)
- Escaneo ClamAV con detección de memoria baja
- SSE para logs en tiempo real (por item) y actualización de cola (global)
- CleanupDaemon para limpieza de sesiones expiradas
- Frontend Bootstrap 5 con drag & drop, terminal de logs, grilla de videos
- Sesiones anónimas con código de 8 caracteres, compartibles por enlace
- Migración de datos legacy (sesión LEGACY01)
- Dockerfile para Render con ClamAV y ffmpeg
- `render.yaml` para despliegue en Render
