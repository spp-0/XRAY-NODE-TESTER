#!/bin/bash
set -euo pipefail

DATA_DIR="${XRAY_WEB_DATA_DIR:-/data/xray1}"
XRAY_BIN="${XRAY_BIN:-${DATA_DIR}/xray}"
DOWNLOAD_URL="${XRAY_DOWNLOAD_URL:-}"

mkdir -p "$DATA_DIR"

if [ ! -x "$XRAY_BIN" ]; then
  echo "[entrypoint] xray not found, downloading..."

  if [ -z "$DOWNLOAD_URL" ]; then
    ARCH="$(uname -m)"
    case "$ARCH" in
      x86_64|amd64) ARCH_TAG="64" ;;
      aarch64|arm64) ARCH_TAG="arm64-v8a" ;;
      armv7l|armv7) ARCH_TAG="arm32-v7a" ;;
      *) ARCH_TAG="64" ;;
    esac

    API_JSON=$(python - <<'PY'
import json, urllib.request
url = "https://api.github.com/repos/XTLS/Xray-core/releases/latest"
req = urllib.request.Request(url, headers={"User-Agent":"xray-node-tester"})
with urllib.request.urlopen(req, timeout=15) as r:
    print(r.read().decode("utf-8", errors="ignore"))
PY
)

    DOWNLOAD_URL=$(python - <<PY
import json, sys
arch_tag = "$ARCH_TAG"
obj = json.loads(sys.stdin.read())
assets = obj.get("assets", [])
want = f"Xray-linux-{arch_tag}.zip"
for a in assets:
    if a.get("name") == want:
        print(a.get("browser_download_url"))
        break
PY
<<<"$API_JSON")
  fi

  if [ -z "$DOWNLOAD_URL" ]; then
    echo "[entrypoint] failed to resolve download url" >&2
    exit 1
  fi

  TMP_ZIP="/tmp/xray.zip"
  curl -L -o "$TMP_ZIP" "$DOWNLOAD_URL"
  mkdir -p /tmp/xray
  unzip -o "$TMP_ZIP" -d /tmp/xray >/dev/null

  if [ -f /tmp/xray/xray ]; then
    mv /tmp/xray/xray "$XRAY_BIN"
    chmod +x "$XRAY_BIN"
  else
    echo "[entrypoint] xray binary not found in archive" >&2
    exit 1
  fi

  if [ -f /tmp/xray/geoip.dat ]; then
    mv /tmp/xray/geoip.dat "$DATA_DIR/geoip.dat"
  fi
  if [ -f /tmp/xray/geosite.dat ]; then
    mv /tmp/xray/geosite.dat "$DATA_DIR/geosite.dat"
  fi

  rm -rf /tmp/xray "$TMP_ZIP"
  echo "[entrypoint] xray download complete"
fi

exec "$@"
