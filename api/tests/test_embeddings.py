"""Los vectores densos deben quedar normalizados L2 antes de salir del
modulo de embeddings: a 768 dim Gemini no los normaliza, y un vector sin
normalizar distorsiona los scores coseno en Qdrant sin lanzar ningun error
(ver CLAUDE.md, seccion "Gemini embeddings"). No se llama a la API real:
se falsea `_get_client` para poder correr el test sin red ni API key.
"""
from __future__ import annotations

import math

import pytest

from app.services import embeddings


def _norm(vector: list[float]) -> float:
    return math.sqrt(sum(v * v for v in vector))


class _FakeEmbedding:
    def __init__(self, values: list[float]) -> None:
        self.values = values


class _FakeResponse:
    def __init__(self, values_list: list[list[float]]) -> None:
        self.embeddings = [_FakeEmbedding(v) for v in values_list]


class _FakeModels:
    def __init__(self, values_list: list[list[float]], calls: list[dict]) -> None:
        self._values_list = values_list
        self._calls = calls

    def embed_content(self, *, model, contents, config):
        self._calls.append(
            {"model": model, "contents": contents, "task_type": config.task_type}
        )
        return _FakeResponse(self._values_list)


class _FakeClient:
    def __init__(self, values_list: list[list[float]], calls: list[dict]) -> None:
        self.models = _FakeModels(values_list, calls)


def _unnormalized_vector(dim: int = embeddings.EMBEDDING_DIM, scale: float = 3.7) -> list[float]:
    # Un vector deliberadamente NO unitario: si el modulo no normalizara,
    # este test lo detectaria.
    return [scale] * dim


def test_l2_normalize_produces_unit_vector():
    normalized = embeddings._l2_normalize([3.0, 4.0])  # norma original 5.0

    assert _norm(normalized) == pytest.approx(1.0, abs=1e-9)
    assert normalized == pytest.approx([0.6, 0.8])


def test_l2_normalize_handles_zero_vector_without_dividing_by_zero():
    assert embeddings._l2_normalize([0.0, 0.0, 0.0]) == [0.0, 0.0, 0.0]


def test_embed_documents_returns_l2_normalized_vectors(monkeypatch):
    calls: list[dict] = []
    fake_client = _FakeClient([_unnormalized_vector()], calls)
    monkeypatch.setattr(embeddings, "_get_client", lambda: fake_client)

    result = embeddings.embed_documents(["el plan de trabajo dice..."])

    assert len(result) == 1
    assert _norm(result[0]) == pytest.approx(1.0, abs=1e-9)
    # task_type asimetrico: indexar siempre usa RETRIEVAL_DOCUMENT.
    assert calls[0]["task_type"] == embeddings.TASK_TYPE_DOCUMENT


def test_embed_query_returns_l2_normalized_vector(monkeypatch):
    calls: list[dict] = []
    fake_client = _FakeClient([_unnormalized_vector(scale=1.3)], calls)
    monkeypatch.setattr(embeddings, "_get_client", lambda: fake_client)

    result = embeddings.embed_query("una afirmacion del candidato")

    assert _norm(result) == pytest.approx(1.0, abs=1e-9)
    # task_type asimetrico: consultar siempre usa RETRIEVAL_QUERY.
    assert calls[0]["task_type"] == embeddings.TASK_TYPE_QUERY


def test_embed_documents_empty_input_skips_api_call(monkeypatch):
    def _fail_if_called():
        raise AssertionError("no deberia llamar a la API con una lista vacia")

    monkeypatch.setattr(embeddings, "_get_client", _fail_if_called)

    assert embeddings.embed_documents([]) == []


# --- Proveedor OpenAI -------------------------------------------------------


class _FakeHttpResponse:
    def __init__(self, status_code: int, data: list[list[float]] | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._data = data or []
        self.text = text
        self.headers: dict[str, str] = {}

    def json(self) -> dict:
        # Fuera de orden a proposito: el modulo debe reordenar por `index`.
        return {"data": [{"index": i, "embedding": v} for i, v in reversed(list(enumerate(self._data)))]}


def _usar_openai(monkeypatch) -> None:
    monkeypatch.setattr(embeddings.settings, "embedding_provider", "openai")
    monkeypatch.setattr(embeddings.settings, "openai_api_key", "sk-falsa")


def test_openai_devuelve_vectores_normalizados_y_en_orden(monkeypatch):
    _usar_openai(monkeypatch)
    llamadas: list[dict] = []

    def _post(url, *, headers, json, timeout):
        llamadas.append(json)
        return _FakeHttpResponse(200, [[3.0, 4.0], [0.0, 2.0]])

    monkeypatch.setattr(embeddings.httpx, "post", _post)

    result = embeddings.embed_documents(["a", "b"])

    assert result[0] == pytest.approx([0.6, 0.8])
    assert result[1] == pytest.approx([0.0, 1.0])
    assert llamadas[0]["dimensions"] == embeddings.EMBEDDING_DIM
    assert llamadas[0]["input"] == ["a", "b"]


def test_openai_sin_credito_falla_sin_reintentar(monkeypatch):
    _usar_openai(monkeypatch)
    intentos: list[int] = []

    def _post(*args, **kwargs):
        intentos.append(1)
        return _FakeHttpResponse(429, text='{"error": {"code": "insufficient_quota"}}')

    monkeypatch.setattr(embeddings.httpx, "post", _post)

    with pytest.raises(RuntimeError, match="insufficient_quota"):
        embeddings.embed_query("hola")
    assert len(intentos) == 1


def test_openai_reintenta_un_429_transitorio(monkeypatch):
    _usar_openai(monkeypatch)
    monkeypatch.setattr(embeddings.time, "sleep", lambda s: None)
    respuestas = [_FakeHttpResponse(429, text="rate limit"), _FakeHttpResponse(200, [[1.0, 0.0]])]
    monkeypatch.setattr(embeddings.httpx, "post", lambda *a, **k: respuestas.pop(0))

    assert embeddings.embed_query("hola") == [1.0, 0.0]


def test_proveedor_invalido_se_rechaza(monkeypatch):
    monkeypatch.setattr(embeddings.settings, "embedding_provider", "otro")

    with pytest.raises(ValueError, match="EMBEDDING_PROVIDER"):
        embeddings.proveedor_activo()


def test_modelo_activo_identifica_proveedor_y_dimension(monkeypatch):
    monkeypatch.setattr(embeddings.settings, "embedding_provider", "gemini")
    assert embeddings.modelo_activo() == "gemini:gemini-embedding-001:768"

    monkeypatch.setattr(embeddings.settings, "embedding_provider", "openai")
    assert embeddings.modelo_activo() == "openai:text-embedding-3-small:768"
