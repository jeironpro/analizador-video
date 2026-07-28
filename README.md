# Analizador de Video

Aplicación web para analizar, validar y almacenar archivos de video. Escanea con ClamAV, verifica codecs y metadatos mediante ffprobe, y persiste los resultados en PostgreSQL.

## Stack

| Componente | Tecnología |
|------------|------------|
| Backend | Flask + Gevent |
| Base de datos | PostgreSQL (Supabase) / SQLite (local) |
| Contenedor | Docker (Render) |
| Análisis de video | ffprobe (ffmpeg) |
| Antivirus | ClamAV (clamscan) |
| Frontend | HTML + CSS + JS vanilla (SSE en tiempo real) |

## Funcionalidades

- Subida múltiple de videos (50 MB – 500 MB) con arrastrar y soltar
- Cola de procesamiento persistente (sobrevive reinicios)
- Hasta 2 procesos en paralelo vía `ThreadPoolExecutor`
- Validación de tamaño, tipo MIME, codecs de video/audio, resolución, FPS, contenedor
- Escaneo antivirus con ClamAV (salta automáticamente si hay < 200 MB de RAM libre)
- Detección de metadatos sospechosos
- Streaming de logs en tiempo real por SSE por cada item
- Actualización de la cola en vivo vía SSE global (sin polling)
- Descarga / eliminación de videos almacenados

## Arquitectura

```
                    ┌─────────────┐
                    │  Navegador  │
                    └──────┬──────┘
                           │ SSE / REST
                    ┌──────┴──────┐
                    │   Gevent    │
                    │  (1 worker) │
                    └──────┬──────┘
                           │
          ┌────────────────┼────────────────┐
          │                │                │
   ┌──────┴──────┐  ┌──────┴─────┐  ┌───────┴─────┐
   │ PostgreSQL  │  │   Disco    │  │    Queue    │
   │ (Supabase)  │  │   Render   │  │  (hasta 2)  │
   └─────────────┘  └────────────┘  └─────────────┘
```

## Despliegue en Render

1. Crea un servicio **Web Service** con `runtime: docker` (`render.yaml` incluido)
2. Conecta una base de datos PostgreSQL (p.ej. Supabase)
3. Configura las variables de entorno:
   - `DATABASE_URL` — conexión a PostgreSQL
   - `SUPABASE_URL` / `SUPABASE_KEY` — (opcional, reservadas)
   - `SECRET_KEY` — autogenerada por Render
4. El `Dockerfile` instala `clamav`, `ffmpeg` y descarga `main.cvd`
5. Se monta un disco persistente de 5 GB en `/data` (uploads + temp)

### Variables de entorno

| Variable | Obligatorio | Descripción |
|----------|-------------|-------------|
| `DATABASE_URL` | Sí | PostgreSQL connection string |
| `SUPABASE_URL` | No | Reservada |
| `SUPABASE_KEY` | No | Reservada |
| `SECRET_KEY` | No | Se genera automáticamente si no se provee |
| `RENDER` | No | Se define automáticamente en Render |

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

### Tests

```bash
pytest
```

## Licencia

MIT — ver [LICENSE](LICENSE).
