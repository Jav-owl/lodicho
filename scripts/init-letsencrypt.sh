#!/usr/bin/env bash
# Bootstrap de Let's Encrypt para lodicho.zamarrito.com. Correr UNA sola
# vez, desde la raiz del repo en el VPS, con los servicios ya levantados
# (make up) y CERTBOT_EMAIL seteado en .env.
#
# nginx/conf.d/lodicho.conf ya asume que existe un certificado real en
# /etc/letsencrypt/live/lodicho.zamarrito.com/ — pero nginx necesita ese
# archivo para poder arrancar el bloque 443, y Let's Encrypt necesita a
# nginx corriendo para validar el dominio (reto HTTP-01). Este script
# rompe ese circulo: crea un certificado dummy, arranca nginx con eso,
# lo cambia por el real, y recarga.
set -euo pipefail
cd "$(dirname "$0")/.."

DOMAIN="lodicho.zamarrito.com"

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi
: "${CERTBOT_EMAIL:?Seteá CERTBOT_EMAIL en .env antes de correr esto}"

if [ -d "certbot/conf/live/$DOMAIN" ]; then
  echo "Ya existe un certificado para $DOMAIN en certbot/conf/live/$DOMAIN. Nada que hacer."
  echo "(Si querés forzar un nuevo pedido, borrá ese directorio primero.)"
  exit 0
fi

echo "### 1/4 — Certificado dummy, para que nginx pueda arrancar el bloque SSL ..."
mkdir -p "certbot/conf/live/$DOMAIN"
docker compose run --rm --entrypoint sh certbot -c "
  openssl req -x509 -nodes -newkey rsa:2048 -days 1 \
    -keyout '/etc/letsencrypt/live/$DOMAIN/privkey.pem' \
    -out '/etc/letsencrypt/live/$DOMAIN/fullchain.pem' \
    -subj '/CN=$DOMAIN'
"

echo "### 2/4 — Arrancando nginx con el certificado dummy ..."
docker compose up -d nginx

echo "### 3/4 — Borrando el dummy y pidiendo el certificado real a Let's Encrypt ..."
docker compose run --rm --entrypoint sh certbot -c "
  rm -rf '/etc/letsencrypt/live/$DOMAIN' \
         '/etc/letsencrypt/archive/$DOMAIN' \
         '/etc/letsencrypt/renewal/$DOMAIN.conf'
"
docker compose run --rm --entrypoint certbot certbot certonly \
  --webroot -w /var/www/certbot \
  -d "$DOMAIN" \
  --email "$CERTBOT_EMAIL" --agree-tos --no-eff-email --non-interactive

echo "### 4/4 — Recargando nginx con el certificado real ..."
docker compose exec nginx nginx -s reload

echo
echo "Listo: https://$DOMAIN debería servir con un certificado real."
echo "El contenedor 'certbot' queda corriendo en segundo plano renovando"
echo "automáticamente, pero nginx no relee el certificado renovado solo:"
echo "hace falta 'docker compose exec nginx nginx -s reload' cada tanto"
echo "(un cron semanal alcanza — el certificado dura 90 días)."
