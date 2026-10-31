"""Embeddings densos (Gemini `gemini-embedding-001` u OpenAI, 768 dim).

El proveedor se elige con `EMBEDDING_PROVIDER` (`gemini` por defecto, u `openai`).
El resto de la app solo llama a `embed_documents` / `embed_query`; nunca importa
un SDK de proveedor. **Cambiar de proveedor obliga a reindexar todo el corpus**:
vectores de modelos distintos no son comparables entre si, aunque tengan la misma
dimension, y la busqueda devolveria basura sin lanzar ningun error. Cada punto
guarda `embedding_modelo` (ver `modelo_activo`) para poder detectar la mezcla.

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

import httpx
from google import genai
from google.genai import errors
from google.genai import types

from app.config.settings import settings

EMBEDDING_MODEL = "gemini-embedding-001"
EMBEDDING_DIM = 768

# La API de Gemini rechaza mas de 100 textos por llamada a embed_content; un
# documento largo (p. ej. el COOTAD) genera cientos de chunks.
MAX_TEXTOS_POR_LLAMADA = 100
# El tier gratuito limita los tokens de embeddings por minuto (30000). Una sola
# llamada mayor que esa cuota jamas pasa, por mas que se espere: los lotes se
# arman por presupuesto de tokens (estimado, sin tokenizer local; se asume un
# token cada 3 caracteres para quedar del lado seguro) ademas del tope de textos.
MAX_TOKENS_POR_LLAMADA = 15000
CHARS_POR_TOKEN_ESTIMADO = 3
# Ante un 429 se espera el retryDelay que indica la API y se reintenta el lote.
MAX_REINTENTOS_429 = 10

# OpenAI: sin task type (modelo simetrico). `dimensions` recorta el vector a 768 para
# calzar con las colecciones de Qdrant. Limite real: 2048 textos y 300k tokens por
# llamada; se usa un margen amplio, no hay cuota por minuto que cuidar.
PROVEEDORES = ("gemini", "openai")
OPENAI_URL = "https://api.openai.com/v1/embeddings"
OPENAI_MAX_TEXTOS_POR_LLAMADA = 100
OPENAI_MAX_TOKENS_POR_LLAMADA = 100000
OPENAI_MAX_REINTENTOS = 5
OPENAI_TIMEOUT_S = 60

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


def proveedor_activo() -> str:
    proveedor = settings.embedding_provider.strip().lower()
    if proveedor not in PROVEEDORES:
        raise ValueError(
            f"EMBEDDING_PROVIDER={settings.embedding_provider!r} no es valido; usa uno de {PROVEEDORES}"
        )
    return proveedor


def modelo_activo() -> str:
    """Identificador `proveedor:modelo:dim` que se guarda en el payload de cada punto."""
    modelo = settings.openai_embedding_model if proveedor_activo() == "openai" else EMBEDDING_MODEL
    return f"{proveedor_activo()}:{modelo}:{EMBEDDING_DIM}"


def _armar_lotes(
    texts: list[str],
    max_textos: int = MAX_TEXTOS_POR_LLAMADA,
    max_tokens: int = MAX_TOKENS_POR_LLAMADA,
) -> list[list[str]]:
    limite_chars = max_tokens * CHARS_POR_TOKEN_ESTIMADO
    lotes: list[list[str]] = []
    actual: list[str] = []
    chars_actual = 0
    for texto in texts:
        if actual and (len(actual) >= max_textos or chars_actual + len(texto) > limite_chars):
            lotes.append(actual)
            actual, chars_actual = [], 0
        actual.append(texto)
        chars_actual += len(texto)
    if actual:
        lotes.append(actual)
    return lotes


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


def _embed_openai_lote(lote: list[str]) -> list[list[float]]:
    if not settings.openai_api_key:
        raise RuntimeError("falta OPENAI_API_KEY (EMBEDDING_PROVIDER=openai)")
    for intento in range(OPENAI_MAX_REINTENTOS + 1):
        respuesta = httpx.post(
            OPENAI_URL,
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            json={
                "model": settings.openai_embedding_model,
                "input": lote,
                "dimensions": EMBEDDING_DIM,
                "encoding_format": "float",
            },
            timeout=OPENAI_TIMEOUT_S,
        )
        if respuesta.status_code == 200:
            datos = sorted(respuesta.json()["data"], key=lambda d: d["index"])
            return [d["embedding"] for d in datos]
        # Sin credito no sirve reintentar: se corta con un mensaje claro.
        if "insufficient_quota" in respuesta.text:
            raise RuntimeError(f"OpenAI sin credito disponible (insufficient_quota): {respuesta.text}")
        reintentable = respuesta.status_code == 429 or respuesta.status_code >= 500
        if not reintentable or intento == OPENAI_MAX_REINTENTOS:
            raise RuntimeError(f"OpenAI embeddings fallo ({respuesta.status_code}): {respuesta.text}")
        espera = respuesta.headers.get("retry-after")
        time.sleep(float(espera) if espera and espera.replace(".", "", 1).isdigit() else 2**intento)
    raise AssertionError("inalcanzable")


def _embed_openai(texts: list[str]) -> list[list[float]]:
    vectores: list[list[float]] = []
    for lote in _armar_lotes(texts, OPENAI_MAX_TEXTOS_POR_LLAMADA, OPENAI_MAX_TOKENS_POR_LLAMADA):
        vectores.extend(_l2_normalize(v) for v in _embed_openai_lote(lote))
    return vectores


def _embed_gemini(texts: list[str], task_type: str) -> list[list[float]]:
    vectores: list[list[float]] = []
    for lote in _armar_lotes(texts):
        response = _embed_lote(lote, task_type)
        vectores.extend(_l2_normalize(embedding.values) for embedding in response.embeddings)
    return vectores


def _embed(texts: list[str], task_type: str) -> list[list[float]]:
    if not texts:
        return []
    if proveedor_activo() == "openai":
        return _embed_openai(texts)
    return _embed_gemini(texts, task_type)


def embed_documents(texts: list[str]) -> list[list[float]]:
    """Embeddings para indexar (upsert). Siempre `TASK_TYPE_DOCUMENT`."""
    return _embed(texts, TASK_TYPE_DOCUMENT)


def embed_query(text: str) -> list[float]:
    """Embedding de una consulta. Siempre `TASK_TYPE_QUERY`."""
    return _embed([text], TASK_TYPE_QUERY)[0]
