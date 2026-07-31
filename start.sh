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
    for i in range(30):
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
        print('PostgreSQL not available after 30s')
"

python3 -m alembic upgrade head
echo "Migraciones aplicadas correctamente"

exec gunicorn app:app --bind 0.0.0.0:"${PORT}" --timeout 300 --worker-class gthread --workers 1 --threads 20
