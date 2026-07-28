#!/bin/sh
mkdir -p /var/run/clamav
clamd --config-file /etc/clamav/clamd.conf &
until clamdscan --ping --config-file /etc/clamav/clamd.conf 2>/dev/null; do sleep 1; done
exec gunicorn app:app --bind 0.0.0.0:$PORT --timeout 300 --workers 1 --worker-class sync
