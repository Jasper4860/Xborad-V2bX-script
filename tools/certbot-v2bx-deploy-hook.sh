#!/bin/sh
# Certbot deploy hook for native V2bX instances. Runs after successful renewal.
# Only acts on a lineage currently referenced in the V2bX config.
set -eu
lineage="${RENEWED_LINEAGE:-}"
case "$lineage" in
  /etc/letsencrypt/live/v2bx-*) ;;
  *) exit 0 ;;
esac
[ -f /etc/V2bX/config.json ] || exit 0
if /usr/bin/python3 - "$lineage" <<'PY'
import json
import sys
from pathlib import Path
lineage = sys.argv[1] + "/"
try:
    data = json.loads(Path("/etc/V2bX/config.json").read_text(encoding="utf-8"))
except (OSError, ValueError):
    sys.exit(1)
for n in data.get("Nodes", []):
    if not isinstance(n, dict):
        continue
    config = n.get("CertConfig") or {}
    if config.get("CertMode") == "file" and str(config.get("CertFile", "")).startswith(lineage):
        sys.exit(0)
sys.exit(1)
PY
then
  if command -v systemctl >/dev/null 2>&1; then
    systemctl try-restart V2bX.service
  fi
fi
