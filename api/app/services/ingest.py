"""Ingesta de un .md del corpus a Qdrant + Postgres.

Logica compartida por dos consumidores:
  - scripts/ingest_corpus.py (CLI manual, `make ingest`)
  - app/routers/admin.py (boton "Ingestar" del panel de carga)

Hace, en Python, lo que CLAUDE.md describe para `/api/v1/ingest`:

  1. Parsea el frontmatter del .md
  2. Chunkea segun `tipo` (marco_legal: por articulo; planes_trabajo: por
     seccion/eje; contexto: semantico ~500 tokens)
  3. Embeddings densos (Gemini, normalizados L2) + dispersos (BM25) por
     chunk, via los mismos services/ que usa la app en consulta
  4. Nunca solo upsert: borra los chunks previos de ese doc_id antes de
     insertar los nuevos
  5. Actualiza la fila en `documentos` (Postgres) con git_sha, n_chunks,
     indexado_en, estado

Rechaza ingestar un archivo con cambios sin commitear en lodicho-corpus:
el git_sha debe reflejar exactamente la version del contenido indexado,
o la trazabilidad legal de evidencias.git_sha queda rota.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.db.models import Candidatura, Documento
from app.db.models.enums import EstadoDocumento, TipoDocumento
from app.services import embeddings, sparse
from app.services.resolucion_candidatura import DIGNIDAD_A_NIVEL_GOBIERNO
from app.services.qdrant_client import (
    ALIAS_CONTEXTO,
    ALIAS_MARCO_LEGAL,
    ALIAS_PLANES_TRABAJO,
    delete_by_doc_id,
    upsert_point,
)

DEFAULT_CORPUS_PATH = Path(settings.corpus_path)

DIR_POR_TIPO = {"marco_legal": "marco_legal", "plan_trabajo": "planes_trabajo", "contexto": "contexto"}
ALIAS_POR_TIPO = {"marco_legal": ALIAS_MARCO_LEGAL, "plan_trabajo": ALIAS_PLANES_TRABAJO, "contexto": ALIAS_CONTEXTO}

# `marco_legal` se recupera filtrando por coincidencia exacta de nivel_gobierno
# (qdrant_client.search_marco_legal): cualquier otro valor se indexa pero
# ninguna consulta lo encuentra, sin avisar.
NIVELES_GOBIERNO = sorted(set(DIGNIDAD_A_NIVEL_GOBIERNO.values()))
# Una norma que rige a todos los niveles (p. ej. el COOTAD) se marca "todos":
# al ingestar se guarda la lista completa de niveles en el payload, y el
# filtro exacto de Qdrant coincide si CUALQUIER elemento de la lista es igual.
NIVEL_TODOS = "todos"

ARTICULO_RE = re.compile(
    r"^#{0,6}[ \t]*\**[ \t]*Art(?:ículo|iculo|\.)?[ \t]*(\d+)",
    re.MULTILINE | re.IGNORECASE,
)
# Solo nivel 1: un eje puede traer subtitulos (## Financiamiento, ## Plazo)
# que describen esa misma propuesta y tienen que quedar en el mismo chunk,
# no fragmentados aparte.
ENCABEZADO_EJE_RE = re.compile(r"^#\s+.+$", re.MULTILINE)

CHARS_POR_TOKEN_APROX = 4  # heuristica gruesa: no hay tokenizer de Gemini local
OBJETIVO_TOKENS_CONTEXTO = 500
# Un articulo mas largo que esto se subdivide al chunkear (ver
# `_subdividir_articulo`); el validador del corpus usa el mismo limite.
LIMITE_TOKENS_POR_CHUNK = 1500


class IngestaError(Exception):
    pass


@dataclass
class ResumenIngesta:
    doc_id: str
    tipo: str
    git_sha: str
    n_chunks: int


def ingestar_archivo(archivo: Path, corpus_path: Path, session: Session) -> ResumenIngesta:
    if not archivo.exists():
        raise IngestaError("el archivo no existe")

    texto = archivo.read_text(encoding="utf-8")
    meta, body = parsear_frontmatter(texto)

    tipo = meta.get("tipo")
    if tipo not in DIR_POR_TIPO:
        raise IngestaError(f"'tipo: {tipo}' invalido o ausente")

    doc_id = meta.get("doc_id")
    if not doc_id:
        raise IngestaError("falta 'doc_id' en el frontmatter")

    if tipo == "plan_trabajo":
        candidatura_id = meta.get("candidatura_id")
        if candidatura_id is None or session.get(Candidatura, candidatura_id) is None:
            raise IngestaError(
                f"candidatura_id={candidatura_id} no existe en la base de datos — registrala antes de ingestar su plan"
            )

    ruta_relativa = str(archivo.relative_to(corpus_path))
    git_sha = git_sha_del_archivo(corpus_path, ruta_relativa)

    chunks = [c.strip() for c in _chunkear(body, tipo) if c.strip()]
    if not chunks:
        raise IngestaError("no se pudo dividir el documento en chunks (¿esta vacio?)")

    vectores_densos = embeddings.embed_documents(chunks)
    vectores_dispersos = sparse.embed_documents(chunks)

    alias = ALIAS_POR_TIPO[tipo]
    # Nunca solo upsert: si el doc paso de 12 a 9 chunks, los 3 viejos
    # tienen que desaparecer, no quedar dando vueltas con contenido stale.
    delete_by_doc_id(alias, doc_id)

    for i, (texto_chunk, denso, disperso) in enumerate(zip(chunks, vectores_densos, vectores_dispersos)):
        payload = _payload(meta, tipo, doc_id, texto_chunk, git_sha)
        point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{doc_id}#{i}"))
        upsert_point(alias, point_id, denso, disperso, payload)

    try:
        _actualizar_documento(session, meta, tipo, ruta_relativa, archivo, git_sha, len(chunks))
    except Exception as exc:
        raise IngestaError(
            f"Qdrant ya quedo actualizado, pero fallo el registro en Postgres: {exc}. "
            "Los chunks SI estan indexados; revisa la fila en 'documentos' a mano."
        ) from exc

    return ResumenIngesta(doc_id=doc_id, tipo=tipo, git_sha=git_sha, n_chunks=len(chunks))


def parsear_frontmatter(texto: str) -> tuple[dict, str]:
    if not texto.startswith("---"):
        raise IngestaError("falta el frontmatter (debe empezar con '---')")
    partes = texto.split("---", 2)
    if len(partes) < 3:
        raise IngestaError("frontmatter mal formado: falta el '---' de cierre")
    try:
        meta = yaml.safe_load(partes[1]) or {}
    except yaml.YAMLError as exc:
        raise IngestaError(f"frontmatter no es YAML valido: {exc}") from exc
    if not isinstance(meta, dict):
        raise IngestaError("el frontmatter debe ser un mapeo clave: valor")
    return meta, partes[2].lstrip("\n")


def git_sha_del_archivo(corpus_path: Path, ruta_relativa: str) -> str:
    sucio = subprocess.run(
        ["git", "-C", str(corpus_path), "status", "--porcelain", "--", ruta_relativa],
        capture_output=True,
        text=True,
    )
    if sucio.stdout.strip():
        raise IngestaError(
            "tiene cambios sin commitear en lodicho-corpus — commitealo antes de ingestar "
            "(el git_sha tiene que reflejar exactamente el contenido que se esta indexando)"
        )

    log = subprocess.run(
        ["git", "-C", str(corpus_path), "log", "-1", "--format=%H", "--", ruta_relativa],
        capture_output=True,
        text=True,
    )
    sha = log.stdout.strip()
    if not sha:
        raise IngestaError("no tiene historial de git en lodicho-corpus — commitealo primero")
    return sha


def _chunkear(body: str, tipo: str) -> list[str]:
    if tipo == "marco_legal":
        return [
            parte
            for articulo in _articulos(body)
            for parte in _subdividir_articulo(articulo)
        ]
    if tipo == "plan_trabajo":
        indices = [m.start() for m in ENCABEZADO_EJE_RE.finditer(body)]
        return _dividir_por_indices(body, indices) if indices else [body]
    return _chunk_semantico(body)


def _articulos(body: str) -> list[str]:
    return _dividir_por_indices(body, [m.start() for m in ARTICULO_RE.finditer(body)])


def _subdividir_articulo(articulo: str) -> list[str]:
    """Un articulo que supera el limite (p. ej. una reforma que reproduce un
    articulo entero con su tabla de multas) se corta en partes por parrafo,
    luego por linea (filas de tabla) y, si hace falta, por oracion. El texto
    de cada parte es el original, sin recortar; las partes siguientes llevan
    una linea de contexto para que el chunk no quede huerfano del articulo."""
    limite_chars = LIMITE_TOKENS_POR_CHUNK * CHARS_POR_TOKEN_APROX
    if len(articulo) <= limite_chars:
        return [articulo]

    primera_linea = articulo.strip().splitlines()[0][:80]
    prefijo = f"[Continuación de: {primera_linea}]\n\n"
    partes = _agrupar(_unidades_de_articulo(articulo, limite_chars - len(prefijo)), limite_chars - len(prefijo))
    return partes[:1] + [prefijo + p for p in partes[1:]]


def _unidades_de_articulo(articulo: str, limite_chars: int) -> list[tuple[str, str]]:
    """Trozos indivisibles del articulo, cada uno con el separador que lo une
    al siguiente (asi una tabla cortada por filas sigue siendo una tabla)."""
    unidades: list[tuple[str, str]] = []
    for parrafo in re.split(r"\n\s*\n", articulo):
        if not parrafo.strip():
            continue
        if len(parrafo) <= limite_chars:
            unidades.append((parrafo, "\n\n"))
            continue
        lineas = parrafo.splitlines()
        for i, linea in enumerate(lineas):
            sep = "\n\n" if i == len(lineas) - 1 else "\n"
            if len(linea) <= limite_chars:
                unidades.append((linea, sep))
            else:
                unidades.extend((o, " ") for o in _dividir_parrafo_largo(linea, limite_chars))
    return unidades


def _agrupar(unidades: list[tuple[str, str]], limite_chars: int) -> list[str]:
    partes: list[str] = []
    actual, sep_previo = "", ""
    for texto, sep in unidades:
        if actual and len(actual) + len(sep_previo) + len(texto) > limite_chars:
            partes.append(actual)
            actual, sep_previo = "", ""
        actual = f"{actual}{sep_previo}{texto}" if actual else texto
        sep_previo = sep
    if actual:
        partes.append(actual)
    return partes


def _dividir_por_indices(body: str, indices: list[int]) -> list[str]:
    if not indices:
        return [body]
    limites = indices + [len(body)]
    return [body[limites[i]:limites[i + 1]] for i in range(len(indices))]


def _chunk_semantico(body: str, objetivo_tokens: int = OBJETIVO_TOKENS_CONTEXTO) -> list[str]:
    """Chunker por parrafos para `contexto`: acumula parrafos hasta cerca
    del objetivo de tokens en vez de cortar a un tamano fijo de caracteres,
    para no partir una idea a la mitad quedando adentro del limite."""
    limite_chars = objetivo_tokens * CHARS_POR_TOKEN_APROX
    parrafos = [p for p in re.split(r"\n\s*\n", body) if p.strip()]

    chunks: list[str] = []
    actual: list[str] = []
    tamano_actual = 0

    for parrafo in parrafos:
        if len(parrafo) > limite_chars:
            if actual:
                chunks.append("\n\n".join(actual))
                actual, tamano_actual = [], 0
            chunks.extend(_dividir_parrafo_largo(parrafo, limite_chars))
            continue

        if tamano_actual + len(parrafo) > limite_chars and actual:
            chunks.append("\n\n".join(actual))
            actual, tamano_actual = [], 0

        actual.append(parrafo)
        tamano_actual += len(parrafo)

    if actual:
        chunks.append("\n\n".join(actual))

    return chunks


def _dividir_parrafo_largo(parrafo: str, limite_chars: int) -> list[str]:
    oraciones = re.split(r"(?<=[.!?])\s+", parrafo)
    chunks, actual, tamano = [], [], 0
    for oracion in oraciones:
        if tamano + len(oracion) > limite_chars and actual:
            chunks.append(" ".join(actual))
            actual, tamano = [], 0
        actual.append(oracion)
        tamano += len(oracion)
    if actual:
        chunks.append(" ".join(actual))
    return chunks


def _payload(meta: dict, tipo: str, doc_id: str, texto: str, git_sha: str) -> dict:
    payload = {
        "doc_id": doc_id,
        "texto": texto,
        "git_sha": git_sha,
        "tipo": tipo,
        "fuente_url": meta.get("fuente_url"),
        "embedding_modelo": embeddings.modelo_activo(),
    }
    if tipo == "plan_trabajo":
        payload["candidatura_id"] = meta.get("candidatura_id")
        payload["jurisdiccion_dpa"] = meta.get("jurisdiccion_dpa")
    elif tipo == "marco_legal":
        nivel = meta.get("nivel_gobierno")
        payload["nivel_gobierno"] = list(NIVELES_GOBIERNO) if nivel == NIVEL_TODOS else nivel
        payload["vigente"] = bool(meta.get("vigente", True))
    elif tipo == "contexto":
        payload["jurisdiccion_dpa"] = meta.get("jurisdiccion_dpa")
    return payload


def _actualizar_documento(
    session: Session, meta: dict, tipo: str, ruta_relativa: str, archivo: Path, git_sha: str, n_chunks: int
) -> None:
    doc_id = meta["doc_id"]
    sha256 = hashlib.sha256(archivo.read_bytes()).hexdigest()

    documento = session.execute(select(Documento).where(Documento.doc_id == doc_id)).scalar_one_or_none()
    if documento is None:
        documento = Documento(doc_id=doc_id)
        session.add(documento)

    documento.tipo = TipoDocumento(tipo)
    documento.candidatura_id = meta.get("candidatura_id") if tipo == "plan_trabajo" else None
    documento.ruta_repo = ruta_relativa
    documento.sha256 = sha256
    documento.pdf_sha256 = meta.get("pdf_sha256")
    documento.git_sha = git_sha
    documento.n_chunks = n_chunks
    documento.indexado_en = datetime.now(timezone.utc)
    documento.estado = EstadoDocumento.activo

    session.commit()
