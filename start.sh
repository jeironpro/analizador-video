#!/bin/sh
mkdir -p /var/run/clamav
clamd --config-file /etc/clamav/clamd.conf >/dev/null 2>&1 &
i=0
while [ $i -lt 15 ]; do
  clamdscan --ping --config-file /etc/clamav/clamd.conf 2>/dev/null && break
  i=$((i+1))
  sleep 1
done
exec gunicorn app:app --bind 0.0.0.0:$PORT --timeout 300 --workers 1 --worker-class sync
