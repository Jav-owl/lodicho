#!/usr/bin/env bash
# Deploy en el VPS. Lo invoca scripts/deploy-entrypoint.sh (desde el Action)
# con el commit ya checkouteado; tambien se puede correr a mano:
#   cd ~/docker/lodicho && bash scripts/deploy.sh
set -euo pipefail
cd "$(dirname "$0")/.."

[ -f .env ] || { echo "Falta .env (ver docs/deploy.md)." >&2; exit 1; }
# Si el archivo no existe Docker monta una carpeta vacia en su lugar y el
# error posterior es confuso (ver CLAUDE.md, gotchas).
[ -f secrets/corpus_deploy_key ] || { echo "Falta secrets/corpus_deploy_key (archivo, no carpeta)." >&2; exit 1; }
[ -d ../lodicho-corpus ] || { echo "Falta ../lodicho-corpus (clonarlo como hermano)." >&2; exit 1; }

# El VPS es compartido: nginx/certbot del host llevan 80/443, el stack solo
# publica en 127.0.0.1 (docker-compose.prod.yml).
export COMPOSE_FILE="docker-compose.yml:docker-compose.prod.yml"

echo "### Commit: $(git rev-parse --short HEAD)"

echo "### Build + up"
docker compose up -d --build --remove-orphans

echo "### Migraciones"
docker compose exec -T api alembic upgrade head

# nginx cachea la IP del contenedor api al arrancar; si api se recreo,
# hay 502 hasta reiniciarlo.
echo "### Reiniciando nginx"
docker compose restart nginx

echo "### Health check"
for i in $(seq 1 20); do
  if docker compose exec -T api python -c \
      "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health', timeout=3).status==200 else 1)" \
      2>/dev/null; then
    echo "API OK"
    docker image prune -f >/dev/null
    exit 0
  fi
  sleep 3
done

echo "La API no respondio /health a tiempo." >&2
docker compose ps >&2
exit 1
