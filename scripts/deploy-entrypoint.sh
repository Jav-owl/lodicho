#!/usr/bin/env bash
# Comando forzado de la llave SSH de GitHub Actions (ver docs/deploy.md).
# Se instala COPIADO en /usr/local/bin/lodicho-deploy, propiedad de root y no
# editable por el usuario de deploy: asi un repo comprometido no puede
# cambiar que ejecuta la llave. Solo acepta un SHA de 40 hex.
set -euo pipefail

REPO_DIR="${LODICHO_DIR:-$HOME/docker/lodicho}"
SHA="${SSH_ORIGINAL_COMMAND:-}"

if [[ ! "$SHA" =~ ^[0-9a-f]{40}$ ]]; then
  echo "Argumento invalido: se espera un SHA de 40 caracteres hex." >&2
  exit 2
fi

cd "$REPO_DIR"
git fetch --prune origin main
# Solo despliega commits que estan en main.
if ! git merge-base --is-ancestor "$SHA" origin/main; then
  echo "El commit $SHA no pertenece a origin/main." >&2
  exit 3
fi
git checkout --force --detach "$SHA"
exec bash scripts/deploy.sh
