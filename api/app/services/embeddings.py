"""Embeddings densos (Gemini `gemini-embedding-001`, 768 dim).

Dos errores silenciosos documentados en CLAUDE.md que este modulo existe
para evitar:

1. Task type asimetrico: `RETRIEVAL_DOCUMENT` al indexar, `RETRIEVAL_QUERY`
   al consultar. Viven aqui como constantes; ningun otro modulo debe
   hardcodear el string.
2. Normalizacion L2: solo la variante de 3072 dim de este modelo viene
   pre-normalizada. A 768 dim hay que normalizar a mano antes del upsert
   o los scores coseno salen distorsionados sin lanzar ningun error.

No usar `gemini-embedding-2`: es multimodal y no soporta `task_type`.
"""
from __future__ import annotations

import math
import re
import time

from google import genai
from google.genai import errors
from google.genai import types

from app.config.settings import settings

EMBEDDING_MODEL = "gemini-embedding-001"
EMBEDDING_DIM = 768

# La API de Gemini rechaza mas de 100 textos por llamada a embed_content; un
# documento largo (p. ej. el COOTAD) genera cientos de chunks.
MAX_TEXTOS_POR_LLAMADA = 100
# El tier gratuito limita los tokens de embeddings por minuto (429); se espera
# el retryDelay que indica la API y se reintenta el mismo lote.
MAX_REINTENTOS_429 = 10

TASK_TYPE_DOCUMENT ="RETRIEVAL_DOCUMENT"
TASK_TYPE_QUERY = "RETRIEVAL_QUERY"

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client


def _l2_normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0.0:
        return vector
    return [v / norm for v in vector]


def _embed_lote(lote: list[str], task_type: str):
    for intento in range(MAX_REINTENTOS_429 + 1):
        try:
            return _get_client().models.embed_content(
                model=EMBEDDING_MODEL,
                contents=lote,
                config=types.EmbedContentConfig(
                    task_type=task_type,
                    output_dimensionality=EMBEDDING_DIM,
                ),
            )
        except errors.ClientError as exc:
            if exc.code != 429 or intento == MAX_REINTENTOS_429:
                raise
            espera = re.search(r"retry in ([\d.]+)s", str(exc))
            time.sleep(float(espera.group(1)) + 1 if espera else 15)


def _embed(texts: list[str], task_type: str) -> list[list[float]]:
    if not texts:
        return []

    vectores: list[list[float]] = []
    for i in range(0, len(texts), MAX_TEXTOS_POR_LLAMADA):
        response = _embed_lote(texts[i : i + MAX_TEXTOS_POR_LLAMADA], task_type)
        vectores.extend(_l2_normalize(embedding.values) for embedding in response.embeddings)
    return vectores


def embed_documents(texts: list[str]) -> list[list[float]]:
    """Embeddings para indexar (upsert). Siempre `TASK_TYPE_DOCUMENT`."""
    return _embed(texts, TASK_TYPE_DOCUMENT)


def embed_query(text: str) -> list[float]:
    """Embedding de una consulta. Siempre `TASK_TYPE_QUERY`."""
    return _embed([text], TASK_TYPE_QUERY)[0]
