"""La conversion PDF -> Markdown es asincrona: Cloudflare corta a los ~100s
y Docling tarda mas. POST /convertir devuelve un job_id y el estado se
consulta aparte."""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers import admin

PDF_FALSO = b"%PDF-1.4 contenido de prueba"


@pytest.fixture()
def client(monkeypatch):
    app.dependency_overrides[admin.requiere_admin] = lambda: None
    monkeypatch.setattr(admin.pdf_conversion, "convertir_pdf_a_markdown", lambda ruta: "# Titulo\n\nTexto")
    monkeypatch.setattr(admin, "_extraer_texto_plano", lambda ruta: "Titulo Texto")
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(admin.requiere_admin, None)


def _esperar_estado(client, job_id, finales=("listo", "error"), intentos=100):
    for _ in range(intentos):
        cuerpo = client.get(f"/api/v1/admin/documentos/convertir/{job_id}").json()
        if cuerpo["estado"] in finales:
            return cuerpo
        time.sleep(0.05)
    raise AssertionError("la conversion no termino")


def _subir(client, tipo="marco_legal"):
    return client.post(
        "/api/v1/admin/documentos/convertir",
        data={"tipo": tipo},
        files={"pdf": ("doc.pdf", PDF_FALSO, "application/pdf")},
    )


def test_convertir_devuelve_job_y_luego_el_resultado(client):
    respuesta = _subir(client)
    assert respuesta.status_code == 202
    job_id = respuesta.json()["job_id"]

    estado = _esperar_estado(client, job_id)

    assert estado["estado"] == "listo"
    assert estado["resultado"]["markdown"] == "# Titulo\n\nTexto"
    assert estado["resultado"]["tipo"] == "marco_legal"
    assert estado["resultado"]["pdf_sha256"]


def test_convertir_error_se_reporta_por_el_job(client, monkeypatch):
    def _falla(ruta):
        raise RuntimeError("pdf ilegible")

    monkeypatch.setattr(admin.pdf_conversion, "convertir_pdf_a_markdown", _falla)

    job_id = _subir(client).json()["job_id"]
    estado = _esperar_estado(client, job_id)

    assert estado["estado"] == "error"
    assert "pdf ilegible" in estado["detalle"]


def test_convertir_tipo_invalido_422(client):
    assert _subir(client, tipo="otro").status_code == 422


def test_estado_de_job_inexistente_404(client):
    assert client.get("/api/v1/admin/documentos/convertir/no-existe").status_code == 404
