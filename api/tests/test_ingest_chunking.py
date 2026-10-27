"""Tests del chunker por articulo de `marco_legal` cuando un articulo supera
el limite de tokens (p. ej. una reforma que reproduce un articulo entero con
su tabla de multas): se subdivide sin recortar texto, en vez de rechazarse.
"""
from __future__ import annotations

from app.services.corpus_validation import (
    ResultadoValidacion,
    _validar_frontmatter_completo,
    _validar_umbral_tokens,
)
from app.services.ingest import (
    CHARS_POR_TOKEN_APROX,
    LIMITE_TOKENS_POR_CHUNK,
    _chunkear,
)

LIMITE_CHARS = LIMITE_TOKENS_POR_CHUNK * CHARS_POR_TOKEN_APROX


def _articulo_largo(cuerpo: str) -> str:
    return f"Artículo 1.- Sustitúyase el artículo 473.\n\n{cuerpo}\n\nArtículo 2.- Vigencia.\n"


def test_articulo_corto_queda_en_un_solo_chunk():
    chunks = _chunkear("Artículo 1.- Corto.\n\nArtículo 2.- Tambien corto.\n", "marco_legal")

    assert len(chunks) == 2


def test_articulo_largo_se_subdivide_sin_perder_texto():
    parrafos = [f"Parrafo {i}. " + "texto " * 200 for i in range(12)]
    body = _articulo_largo("\n\n".join(parrafos))

    chunks = _chunkear(body, "marco_legal")

    assert len(chunks) > 2  # articulo 1 en varias partes + articulo 2
    assert all(len(c) <= LIMITE_CHARS for c in chunks)
    unido = "\n".join(chunks)
    for parrafo in parrafos:
        assert parrafo in unido


def test_partes_siguientes_llevan_linea_de_contexto():
    parrafos = ["texto " * 200 for _ in range(12)]
    chunks = _chunkear(_articulo_largo("\n\n".join(parrafos)), "marco_legal")

    assert not chunks[0].startswith("[Continuación")
    assert chunks[1].startswith("[Continuación de: Artículo 1.- Sustitúyase")
    assert chunks[-1].startswith("Artículo 2.-")


def test_tabla_larga_se_corta_por_filas_enteras():
    filas = [f"| Infracción {i} | ${i * 10} | Multa del {i} % del SBU |" for i in range(400)]
    body = _articulo_largo("\n".join(filas))

    chunks = _chunkear(body, "marco_legal")

    assert all(len(c) <= LIMITE_CHARS for c in chunks)
    unido = "\n".join(chunks)
    for fila in filas:
        assert fila in unido


def test_validador_avisa_pero_no_bloquea_un_articulo_largo():
    parrafos = [f"Parrafo {i}. " + "texto " * 200 for i in range(12)]
    r = ResultadoValidacion()

    _validar_umbral_tokens(_articulo_largo("\n\n".join(parrafos)), "marco_legal", r)

    assert r.errores == []
    assert any("se subdivide" in w for w in r.warnings)


def test_validador_rechaza_si_algo_no_se_puede_subdividir():
    # Una sola "oracion" sin puntuacion ni saltos de linea: no hay donde cortar.
    r = ResultadoValidacion()

    _validar_umbral_tokens(_articulo_largo("palabra" * (LIMITE_CHARS // 7 + 100)), "marco_legal", r)

    assert any("supera el umbral" in e for e in r.errores)


def _meta_marco_legal(**cambios):
    meta = {
        "doc_id": "ordenanza-metropolitana-001-quito",
        "tipo": "marco_legal",
        "fuente_url": "https://example.org/ordenanza.pdf",
        "pdf_sha256": "a" * 64,
        "convertido_con": "docling",
        "revisado_por": "revisor",
        "revisado_en": "2026-10-27",
        "vigente": True,
        "nivel_gobierno": "cantonal",
    }
    meta.update(cambios)
    return meta


def test_nivel_gobierno_valido_no_da_error():
    r = ResultadoValidacion()

    _validar_frontmatter_completo(_meta_marco_legal(), r)

    assert r.errores == []


def test_nivel_gobierno_que_no_coincide_con_el_filtro_da_error():
    r = ResultadoValidacion()

    _validar_frontmatter_completo(_meta_marco_legal(nivel_gobierno="Quito"), r)

    assert any("nivel_gobierno: Quito" in e and "cantonal" in e for e in r.errores)
