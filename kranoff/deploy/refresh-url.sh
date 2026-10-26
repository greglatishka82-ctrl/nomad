#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
URL=$(docker logs kranoff-tunnel 2>&1 | grep -Eo 'https://[a-zA-Z0-9.-]+\.trycloudflare\.com' | tail -1 || true)
if [ -z "$URL" ]; then
  echo "Не нашёл URL туннеля. Проверьте: docker logs kranoff-tunnel" >&2
  exit 1
fi
sed -i "s|^PUBLIC_BASE_URL=.*|PUBLIC_BASE_URL=$URL|" .env
docker compose up -d app >/dev/null
echo "$URL"
