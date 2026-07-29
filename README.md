# VidScan

Aplicación web para escanear, validar y almacenar archivos de video. Utiliza ClamAV para antivirus y ffprobe para análisis de codecs y metadatos, con persistencia en PostgreSQL.

## Stack

| Componente       | Tecnología                                   |
|------------------|----------------------------------------------|
| Backend          | Flask + Gevent                               |
| Base de datos    | PostgreSQL / SQLite (local)                  |
| Contenedor       | Docker (Render) + docker-compose (local)     |
| Análisis         | ffprobe (ffmpeg)                             |
| Antivirus        | ClamAV (clamscan)                            |
| Frontend         | HTML + CSS + JS vanilla (SSE en tiempo real) |
| Calidad          | Ruff, type hints, pytest                     |

## Funcionalidades

- Subida múltiple de videos (50 MB – 500 MB) con arrastrar y soltar
- Validación client-side de tamaño antes de subir
- Reintento automático de upload (hasta 3 intentos)
- Cola de procesamiento persistente en PostgreSQL (sobrevive reinicios)
- Procesamiento secuencial (1 item a la vez)
- Validación de tamaño, tipo MIME (python-magic), codecs de video/audio, resolución, FPS, contenedor
- Escaneo antivirus con ClamAV (salta automáticamente si hay < 200 MB de RAM libre)
- Reintento automático de items fallidos (configurable, default 3)
- Recuperación automática de items "stuck" en processing (timeout configurable)
- Detección de metadatos sospechosos
- Streaming de logs en tiempo real por SSE por cada item
- Actualización de la cola en vivo vía SSE global (sin polling)
- Limitación de tasa en upload (sliding window, default 20 req / 60s)
- Límite de items por sesión (default 20)
- Esqueletos shimmer (skeleton screens) mientras carga la cola y los videos
- Notificaciones toast (success, error, warning, info)
- Páginas de error personalizadas (404, 500)
- Descarga / eliminación de videos almacenados
- Sesiones anónimas compartibles por enlace (expiran tras inactividad)
- Limpieza automática de sesiones expiradas (CleanupDaemon)
- Apagado graceful (SIGTERM): finaliza item activo, reencola pendientes
- Logging estructurado en JSON (activado en Render o con `LOG_FORMAT=json`)

## Arquitectura

```
                     ┌─────────────┐
                     │  Navegador  │
                     └──────┬──────┘
                            │ SSE / REST
                     ┌──────┴──────┐
                     │   Flask +   │
                     │  Gevent (1) │─── CleanupDaemon (cada 1h)
                     └──────┬──────┘
                            │
           ┌────────────────┼────────────────┬─────────────────┐
           │                │                │                 │
    ┌──────┴──────┐  ┌──────┴──────┐  ┌──────┴──────┐  ┌──────┴──────┐
    │ PostgreSQL  │  │    Disco    │  │   ClamAV    │  │   ffprobe   │
    │ (queue +    │  │  /data/     │  │  (clamscan) │  │  (ffmpeg)   │
    │  sessions + │  │  uploads/   │  │             │  │             │
    │  videos)    │  │  temp/      │  │             │  │             │
    └─────────────┘  └────────────┘  └─────────────┘  └─────────────┘
```

## Despliegue en Render

1. Crea un servicio **Web Service** con `runtime: docker` (`render.yaml` incluido)
2. Conecta una base de datos PostgreSQL externa (p. ej. Supabase, Neon, Aiven)
3. Configura las variables de entorno:
   - `DATABASE_URL` — conexión a PostgreSQL (obligatorio)
   - `SECRET_KEY` — autogenerada por Render si no se provee
4. El `Dockerfile` instala `clamav`, `ffmpeg` y descarga `main.cvd`
5. Se monta un disco persistente de 5 GB en `/data` (uploads + temp)

### Variables de entorno

| Variable             | Obligatorio | Default       | Descripción                                  |
|----------------------|-------------|---------------|----------------------------------------------|
| `DATABASE_URL`       | Sí          | `sqlite:///videos.db` | Conexión a PostgreSQL              |
| `SECRET_KEY`         | No          | Auto-generado | Clave secreta de Flask                       |
| `UPLOAD_DIR`         | No          | `/data`       | Directorio base para uploads y temp          |
| `SESSION_DAYS`       | No          | `7`           | Días antes de expirar sesión inactiva        |
| `ITEM_TIMEOUT`       | No          | `600`         | Segundos antes de recuperar item "stuck"     |
| `MAX_RETRIES`        | No          | `3`           | Reintentos máximos por item fallido          |
| `MAX_QUEUE_ITEMS`    | No          | `20`          | Máximo de items en cola por sesión           |
| `RATE_LIMIT_UPLOAD`  | No          | `20`          | Máximo de uploads por ventana                |
| `RATE_LIMIT_WINDOW`  | No          | `60`          | Ventana de rate limiting (segundos)          |
| `LOG_FORMAT`         | No          | `text`        | `json` para logging estructurado             |
| `RENDER`             | No          | —             | Se define automáticamente en Render          |

## Desarrollo local

```bash
git clone <repo>
cd analizador-video
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python app.py
# Abrir http://localhost:5001
```

Por defecto usa SQLite (`videos.db`). Para usar PostgreSQL, exporta `DATABASE_URL`.

### Con Docker

```bash
docker compose up --build
# Abrir http://localhost:8080
```

### Tests

```bash
pytest
```

### Calidad de código

```bash
ruff check .
```

### Pre-commit

Los hooks se ejecutan automáticamente antes de cada commit:

```bash
pre-commit install
```

Para ejecutarlos manualmente sobre todos los archivos:

```bash
pre-commit run --all-files
```

## Scripts

| Script                          | Descripción                                        |
|---------------------------------|----------------------------------------------------|
| `scripts/backup.sh`             | Backup de DB (`pg_dump`) + archivos (`tar`)        |
| `start.sh`                      | Entrypoint: espera PostgreSQL y arranca gunicorn   |
| `docker-compose.yml`            | Entorno local con PostgreSQL + ClamAV + app        |

## Licencia

MIT — ver [LICENSE](LICENSE).
