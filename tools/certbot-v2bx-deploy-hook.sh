#!/bin/sh
# Certbot runs this only after a certificate was successfully renewed.
set -eu
lineage="${RENEWED_LINEAGE:-}"
case "$lineage" in
    /etc/letsencrypt/live/v2bx-*) ;;
    *) exit 0 ;;
esac
manager=/usr/local/V2bX/acme_cert_manager.py
if [ ! -f "$manager" ]; then
    echo 'V2bX renewal deploy: certificate manager missing, service not touched.' >&2
    exit 1
fi
exec /usr/bin/python3 "$manager" deploy --lineage "$lineage"
