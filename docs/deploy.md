# Despliegue (GitHub Actions → VPS)

Push a `main` → corren los tests → el Action entra por SSH al VPS con una llave
**restringida** → el VPS hace `git fetch`, checkout del SHA exacto,
`docker compose up -d --build`, migraciones, reinicio del nginx del stack y health check.

- Repo: fork `Jav-owl/lodicho` (el original, `intiinside/lodicho`, queda como `upstream`)
- Dominio: `lodicho.zamarrito.com`, Cloudflare proxied, SSL/TLS **Full (strict)**
- VPS: el de `docs/vps-audit.md` (Ubuntu 24.04, 47 GiB RAM, nginx + certbot en el host)
- Ruta: `/home/javofox/docker/lodicho` y, como hermano, `/home/javofox/docker/lodicho-corpus`
- Usuario de deploy: `javofox` (el mismo de la administración; ya está en el grupo `docker`). La llave del Action es una línea aparte de su `authorized_keys`, restringida a un solo comando

## Topología en el VPS compartido

```
Cloudflare ──443──▶ nginx del HOST (certbot --nginx, real_ip global)
                        │ proxy_pass 127.0.0.1:18090
                        ▼
              contenedor nginx del stack (solo HTTP, rate limiting, sirve web/)
                        │
                        ▼
              api · worker · postgres · redis · qdrant   (red interna de Docker)
```

- `docker-compose.prod.yml` (cargado con `COMPOSE_FILE` en `.env` y por `deploy.sh`):
  apaga `certbot` y publica nginx **solo en `127.0.0.1:18090`**. Docker publica
  puertos saltándose UFW (la auditoría ya lo sufrió con otro servicio): nunca `0.0.0.0`.
- `nginx/conf.d.prod/lodicho.conf`: igual que el de standalone pero sin TLS, y recupera
  la IP real del cliente desde `X-Real-IP` para que los rate limits funcionen.
- `docs/host/nginx-lodicho.zamarrito.com.conf`: vhost del nginx del host.
- `scripts/init-letsencrypt.sh` **no se usa** aquí: el certificado lo emite
  `certbot --nginx` del host, igual que el de `lib`.
- Antes de elegir el puerto: `sudo ss -lntp | grep 18090` (si está ocupado, define
  `LODICHO_HTTP_PORT` en `.env` y ajusta el vhost).

## Modelo de secretos

| Secreto | Dónde vive | Para qué |
|---|---|---|
| `.env` (Gemini, Postgres, admin…) | **Solo en el VPS**, `chmod 600` | La app. Nunca en GitHub ni en el repo |
| `secrets/corpus_deploy_key` | **Solo en el VPS**, `chmod 600` | La app pushea a `lodicho-corpus` |
| Llaves de lectura de ambos repos | **Solo en el VPS** (`~/.ssh`) | El VPS hace `git fetch` |
| `DEPLOY_SSH_KEY` y compañía | GitHub Environment `production` | El Action entra al VPS |
| `~/.ssh/lodicho-vps` | Solo tu Mac | Tu acceso administrativo al VPS |

El Action no conoce ni transporta el `.env`; cada llave es ed25519, dedicada a una
función; la llave del Action solo ejecuta `lodicho-deploy <sha>`.

## 0. Conexión SSH desde tu Mac (estado al 2026-10-23)

- GitHub: `ssh -T github-javowl` autentica como **Jav-owl** ✔. El `origin` del clon ya
  apunta a `git@github-javowl:Jav-owl/lodicho.git`.
- VPS: el alias existente `ssdn` (usuario `javofox`) llega al host pero **ninguna de
  las llaves cargadas está autorizada** (`Permission denied (publickey,password)`).
- Se generó una llave nueva `~/.ssh/lodicho-vps` y el alias `lodicho-vps` en
  `~/.ssh/config` (respaldo: `config.bak-lodicho`). Falta autorizarla una vez en el VPS:

```bash
ssh-copy-id -i ~/.ssh/lodicho-vps.pub lodicho-vps     # pide la clave del usuario (o usa la consola del proveedor y pega la .pub en ~/.ssh/authorized_keys)
ssh lodicho-vps 'hostname; id'
```

Huella del host que se vio al conectar (`SHA256:mcsL0at6NBzFRe6Mr48MRipojx5Sz3ahGGERL8XXeuU`):
compárala con `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` **desde la consola del
proveedor** antes de aceptarla. Es el pendiente "huella SSH por canal confiable" de la
auditoría. Recomendado: poner passphrase a la llave (`ssh-keygen -p -f ~/.ssh/lodicho-vps`;
`AddKeysToAgent` ya la cachea).

## 1. Bootstrap del VPS (una vez, con tu usuario administrador)

```bash
id javofox | grep -q docker && echo "javofox ya está en el grupo docker"   # docker = root sobre el host; por eso la llave del Action va restringida
sudo ss -lntp | grep 18090 || echo "18090 libre"
```

UFW no necesita cambios: 80/443 ya admiten Cloudflare y 22 está abierto para el Action.
Recomendado: `fail2ban` para sshd.

## 2. Llaves y repos (como `javofox`)

```bash
install -d -m 700 ~/.ssh && mkdir -p ~/docker && cd ~/docker

# Una deploy key de SOLO LECTURA por repo (GitHub no deja repetir una llave).
ssh-keygen -t ed25519 -C "vps-lodicho-read"  -f ~/.ssh/lodicho_read -N ""
ssh-keygen -t ed25519 -C "vps-corpus-read"   -f ~/.ssh/corpus_read  -N ""
cat ~/.ssh/lodicho_read.pub   # → GitHub: Jav-owl/lodicho → Settings → Deploy keys (sin write)
cat ~/.ssh/corpus_read.pub    # → GitHub: <cuenta>/lodicho-corpus → Deploy keys (sin write)

cat >> ~/.ssh/config <<'EOF'
Host gh-lodicho
  HostName github.com
  User git
  IdentityFile ~/.ssh/lodicho_read
  IdentitiesOnly yes
Host gh-corpus
  HostName github.com
  User git
  IdentityFile ~/.ssh/corpus_read
  IdentitiesOnly yes
EOF
chmod 600 ~/.ssh/config
ssh-keyscan -t ed25519 github.com >> ~/.ssh/known_hosts   # verificar contra https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints

git clone git@gh-lodicho:Jav-owl/lodicho.git
git clone git@gh-corpus:<CUENTA>/lodicho-corpus.git

# Llave de ESCRITURA del panel de admin (otra distinta): ver secrets/README.md.
# Debe quedar en ~/docker/lodicho/secrets/corpus_deploy_key, chmod 600.

cd lodicho
cp .env.example .env && chmod 600 .env && nano .env
```

Valores clave del `.env`:

```
COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml
DOMAIN=lodicho.zamarrito.com
ALLOWED_ORIGINS=https://lodicho.zamarrito.com
CORPUS_GIT_REMOTE=git@github.com:<CUENTA>/lodicho-corpus.git
# secretos fuertes: openssl rand -hex 32
```

## 3. Llave del Action con comando forzado

En el VPS, con sudo:

```bash
sudo install -m 755 -o root -g root /home/javofox/docker/lodicho/scripts/deploy-entrypoint.sh /usr/local/bin/lodicho-deploy
# Revisa el diff y vuelve a copiarlo cada vez que cambie ese script.
```

En tu Mac genera la llave del Action (la privada solo irá a GitHub):

```bash
ssh-keygen -t ed25519 -C "github-actions-lodicho" -f ./gha_lodicho -N ""
```

`/home/javofox/.ssh/authorized_keys` — **agrega** (`>>`/editor, nunca sobrescribas: ahí está tu llave `lodicho-vps`) una línea con el contenido de `gha_lodicho.pub`:

```
command="/usr/local/bin/lodicho-deploy",restrict ssh-ed25519 AAAA... github-actions-lodicho
```

`restrict` desactiva pty, forwarding y agent. Si la llave se filtra, solo sirve para
desplegar un SHA que ya esté en `main`. Permisos: `chmod 700 ~/.ssh; chmod 600 ~/.ssh/authorized_keys`.

`DEPLOY_KNOWN_HOSTS` (línea para el Action, desde la consola o ya autenticado):

```bash
echo "<ip-del-vps> $(cut -d' ' -f1,2 /etc/ssh/ssh_host_ed25519_key.pub)"
```

## 4. Secretos y variables en GitHub (`Jav-owl/lodicho`, manual)

Settings → Secrets and variables → Actions. Los valores se cargan a mano en la web;
nunca por el repo ni por `gh` con el valor en el historial de la shell.

Pestaña **Secrets** (cifrados, no se vuelven a mostrar):

| Secret | Valor |
|---|---|
| `DEPLOY_SSH_KEY` | contenido de `gha_lodicho` (privada, con el salto de línea final) |
| `DEPLOY_KNOWN_HOSTS` | `<ip-del-vps> ssh-ed25519 AAAA...` (paso 3) |

Pestaña **Variables** (no sensibles, visibles en los logs):

| Variable | Valor |
|---|---|
| `DEPLOY_HOST` | IP del VPS (**no** el dominio proxied: Cloudflare no pasa SSH) |
| `DEPLOY_USER` | `javofox` |
| `DEPLOY_PORT` | `22` (opcional) |

Pueden ir a nivel de repo o dentro del Environment `production` (el job lo declara, así
que lee ambos). Con Environment se puede exigir *Required reviewers* y limitar a `main`:
recomendado. Luego borra `gha_lodicho*` de tu Mac. Activa branch protection en `main` y 2FA.

## 5. Primer arranque (a mano, como `javofox`)

```bash
# nginx del host + certificado (con tu usuario administrador)
sudo cp /home/javofox/docker/lodicho/docs/host/nginx-lodicho.zamarrito.com.conf /etc/nginx/sites-available/lodicho.zamarrito.com
sudo ln -s /etc/nginx/sites-available/lodicho.zamarrito.com /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx     # los avisos "protocol options redefined" son preexistentes
sudo certbot --nginx -d lodicho.zamarrito.com    # el DNS ya debe estar proxied en Cloudflare

# stack
cd ~/docker/lodicho && make up && make migrate && make init-qdrant
curl -sI https://lodicho.zamarrito.com | head -3
```

Desde aquí, cada push a `main` despliega solo (o Actions → *Deploy* → *Run workflow*).

## Mantenimiento

- **Rotar la llave del Action:** nueva llave, reemplazar la línea de `authorized_keys`
  y el secret.
- **Rollback:** la llave del Action solo despliega; entra con `ssh lodicho-vps` y corre
  `cd ~/docker/lodicho && git checkout --detach <sha> && bash scripts/deploy.sh`.
- **Backups:** `postgres_data` y `qdrant_data` son volúmenes Docker; el deploy no los
  toca, pero no hay backup programado todavía.
- **Cuenta nueva:** `lodicho-corpus` también debe migrar a la cuenta nueva y actualizar
  `CORPUS_GIT_REMOTE`; `README.md`/`CLAUDE.md` aún citan `intiinside`.
