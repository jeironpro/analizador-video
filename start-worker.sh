#!/bin/sh
set -e

# Wait for Redis
python3 -c "
import re, socket, time, os
url = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
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

python3 -m alembic upgrade head
echo "Migraciones aplicadas correctamente"

count="${RQ_WORKERS:-1}"
i=1
while [ "$i" -le "$count" ]; do
    echo "Lanzando worker RQ $i de $count"
    if [ "$i" -eq "$count" ]; then
        exec python3 worker.py
    else
        python3 worker.py &
    fi
    i=$((i + 1))
done
