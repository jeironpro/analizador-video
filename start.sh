#!/bin/sh
set -e

# Wait for PostgreSQL if DATABASE_URL points to postgres
python3 -c "
import re, socket, time, os
url = os.environ.get('DATABASE_URL', '')
m = re.search(r'@([^:/]+)(?::(\d+))?', url)
if m:
    host, port = m.group(1), int(m.group(2) or 5432)
    print(f'Waiting for PostgreSQL at {host}:{port}...')
    for i in range(60):
        try:
            s = socket.socket()
            s.settimeout(2)
            s.connect((host, port))
            s.close()
            print('PostgreSQL is ready')
            break
        except Exception:
            time.sleep(1)
    else:
        print('PostgreSQL not available after 60s')
"

# Wait for Redis if REDIS_URL is set
python3 -c "
import re, socket, time, os
url = os.environ.get('REDIS_URL', '')
if url:
    host, port = 'localhost', 6379
    m = re.search(r'redis://([^:/]+)(?::(\d+))?', url)
    if m:
        host, port = m.group(1), int(m.group(2) or 6379)
    print(f'Waiting for Redis at {host}:{port}...')
    for i in range(60):
        try:
            s = socket.socket()
            s.settimeout(2)
            s.connect((host, port))
            s.close()
            print('Redis is ready')
            break
        except Exception:
            time.sleep(1)
    else:
        print('Redis not available after 60s')
"
if [ "$REDIS_URL" != "" ]; then
    echo "Redis configurado"
fi

python3 -m alembic upgrade head
echo "Migraciones aplicadas correctamente"

exec gunicorn app:app --bind 0.0.0.0:"${PORT}" --timeout 300 --worker-class gthread \
    --workers "${WEB_WORKERS:-2}" --threads "${WEB_THREADS:-4}"
