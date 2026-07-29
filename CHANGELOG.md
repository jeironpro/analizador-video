# Changelog

Todas las cambios notables en este proyecto se documentarán en este archivo.

El formato está basado en [Keep a Changelog](https://keepachangelog.com/),
y el proyecto adhiere a [Semantic Versioning](https://semver.org/).

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
