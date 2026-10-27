# Lo Dicho

Plataforma de contrastación electoral para el piloto de la provincia de Bolívar,
Ecuador. El ciudadano consulta una declaración de un candidato (voz, texto o URL) y el
sistema responde con evidencia de un corpus curado: qué dice su plan de trabajo
registrado y qué establece el COOTAD sobre las competencias de ese nivel de gobierno.

**La referencia completa del proyecto — reglas críticas, modelo de datos, arquitectura,
estado actual, y las gotchas operativas que ya nos mordieron una vez — vive en
[`CLAUDE.md`](./CLAUDE.md). Este README es solo el punto de arranque; para entender
*por qué* algo está hecho así, siempre `CLAUDE.md`.**

## Dos repos, no uno

| Repo | Qué tiene |
|---|---|
| `lodicho` (este) | API, worker, web, infra |
| [`lodicho-corpus`](https://github.com/Jav-owl/lodicho-corpus) | `.md` con frontmatter + PDFs fuente del corpus |

Se clonan como carpetas **hermanas** (mismo directorio padre) — `docker-compose.yml`
monta `lodicho-corpus` como volumen dentro del contenedor `api`.

## Arrancar en local

```bash
cp .env.example .env   # completar valores, ver tabla abajo
make up                # docker compose up -d --build
make migrate            # aplica el esquema de Postgres
make init-qdrant        # crea las 4 colecciones + sus alias
```

Frontend en `http://localhost` (nginx). Para probar el micrófono de la PWA en dev,
serví `web/` aparte en `localhost` (`python3 -m http.server` adentro de `web/`) —
`getUserMedia` exige contexto seguro, y `localhost` cuenta como tal sin necesitar
HTTPS.

### Variables de entorno imprescindibles

| Variable | Para qué |
|---|---|
| `POSTGRES_*` / `DATABASE_URL` | Postgres |
| `REDIS_URL` | Redis (worker) |
| `QDRANT_URL` | Qdrant |
| `GEMINI_API_KEY` | Embeddings + generación (Google AI Studio, no OpenAI — las claves nuevas empiezan con `AQ.`, las antiguas con `AIzaSy`) |
| `ADMIN_PASSWORD`, `ADMIN_SESSION_SECRET` | Panel de admin (`#/admin`) |
| `CORPUS_GIT_REMOTE`, `CORPUS_GIT_SSH_KEY` | Commit/push automático a `lodicho-corpus` desde el panel |
| `DOMAIN`, `CERTBOT_EMAIL` | HTTPS (Let's Encrypt) |

Ver `.env.example` para la lista completa con comentarios.

## Desplegar en el VPS

Resumen — el detalle de cada paso está en `CLAUDE.md` (sección "Panel de admin" para
la deploy key, y la lista de gotchas operativas si algo no arranca):

```bash
# Desarrollo local. Producción (VPS compartido + GitHub Actions): ver docs/deploy.md
cd ~/docker
git clone https://github.com/Jav-owl/lodicho.git
git clone git@github.com:Jav-owl/lodicho-corpus.git   # repo privado, necesita acceso
cd lodicho
cp .env.example .env   # completar
make up && make migrate && make init-qdrant
make init-letsencrypt  # certificado real, una sola vez
```

Después, para el panel de admin: generar una deploy key SSH dedicada (nunca la
personal), agregarla en GitHub con permiso de escritura sobre `lodicho-corpus`, y
colocarla en `secrets/corpus_deploy_key` con `chmod 600` — instrucciones completas en
[`secrets/README.md`](./secrets/README.md).

**Si algo devuelve 502 justo después de reconstruir un contenedor:** `docker compose
restart nginx` — nginx cachea la IP interna del contenedor al arrancar y no la
actualiza sola cuando `api` se recrea. Es la causa de la mayoría de los 502 que vas a
ver acá.

## Estructura

```
lodicho/
  api/              FastAPI + ARQ worker
    app/
      routers/       endpoints HTTP (admin.py; consulta/ingesta todavia no existen)
      services/       logica de negocio (embeddings, qdrant, ingest, git, pdf, auth)
      db/models/      SQLAlchemy
    migrations/       Alembic
    scripts/          CLI: init_qdrant.py, ingest_corpus.py
  web/              PWA vanilla JS — sin build step
    js/views/         Inicio, Historial, Acerca de, Admin
    js/components/     composer, nav, tarjetas, etc.
  nginx/            config de nginx + rate limiting
  secrets/          deploy key SSH (nunca commiteada, ver README ahi)
  scripts/          ops del host (Let's Encrypt)
```

## Estado

Infraestructura y panel de admin listos; el corpus todavía está vacío y el pipeline de
consulta ciudadana (`/api/v1/consulta`) no existe todavía. Detalle completo, siempre
actualizado, en la sección "Estado actual" de `CLAUDE.md`.
