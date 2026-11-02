"""Tests de `POST /api/v1/transcribir` y del calculo de costo, con ffmpeg y
OpenAI mockeados (no hay red ni binarios en CI)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config.settings import settings
from app.main import app
from app.services import transcripcion as svc


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "audio_path", str(tmp_path))
    monkeypatch.setattr(settings, "modo_silencio_electoral", False)
    with TestClient(app) as c:
        yield c


def _subir(client, contenido=b"audio", tipo="audio/webm"):
    return client.post("/api/v1/transcribir", files={"audio": ("a.webm", contenido, tipo)})


def test_transcribe_y_archiva_el_audio(client, tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "medir_duracion_s", lambda ruta: 30.0)
    monkeypatch.setattr(svc, "transcribir", lambda ruta: "Castillo propone eliminar la tasa")

    r = _subir(client)

    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["texto"] == "Castillo propone eliminar la tasa"
    assert cuerpo["duracion_s"] == 30.0
    assert cuerpo["costo_estimado_usd"] == pytest.approx(0.0015)
    assert len(list(tmp_path.glob(f"{cuerpo['audio_id']}.*"))) == 1


def test_audio_sobre_el_limite_de_duracion_se_rechaza_sin_llamar_a_openai(client, tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "medir_duracion_s", lambda ruta: 61.0)

    def _no_debe_llamarse(ruta):
        raise AssertionError("no debe transcribir audio demasiado largo")

    monkeypatch.setattr(svc, "transcribir", _no_debe_llamarse)

    r = _subir(client)

    assert r.status_code == 422
    assert "60" in r.json()["detail"]
    assert list(tmp_path.iterdir()) == []  # no se archiva


def test_audio_sobre_el_limite_de_tamano_da_413(client, monkeypatch):
    monkeypatch.setattr(settings, "audio_max_bytes", 10)
    assert _subir(client, contenido=b"x" * 11).status_code == 413


def test_audio_vacio_da_400(client):
    assert _subir(client, contenido=b"").status_code == 400


def test_fallo_del_proveedor_da_503_y_no_archiva(client, tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "medir_duracion_s", lambda ruta: 5.0)

    def _falla(ruta):
        raise svc.TranscripcionError("cuota")

    monkeypatch.setattr(svc, "transcribir", _falla)

    assert _subir(client).status_code == 503
    assert list(tmp_path.iterdir()) == []


def test_silencio_electoral_desactiva_la_transcripcion(client, monkeypatch):
    monkeypatch.setattr(settings, "modo_silencio_electoral", True)
    assert _subir(client).status_code == 403


def test_costo_estimado_es_lineal_en_los_minutos():
    assert svc.costo_estimado_usd(60) == pytest.approx(settings.openai_transcribe_usd_por_min)
    assert svc.costo_estimado_usd(0) == 0


def test_proveedor_invalido_se_rechaza(monkeypatch):
    monkeypatch.setattr(settings, "transcription_provider", "otro")
    with pytest.raises(ValueError):
        svc.proveedor_activo()


def test_costo_usa_el_precio_del_proveedor_activo(monkeypatch):
    monkeypatch.setattr(settings, "transcription_provider", "gemini")
    assert svc.costo_estimado_usd(60) == pytest.approx(settings.gemini_transcribe_usd_por_min)


def test_transcribir_despacha_segun_el_proveedor(tmp_path, monkeypatch):
    ruta = tmp_path / "a.webm"
    ruta.write_bytes(b"x")
    monkeypatch.setattr(svc, "_transcribir_openai", lambda r: "openai")
    monkeypatch.setattr(svc, "_transcribir_gemini", lambda r: "gemini")

    monkeypatch.setattr(settings, "transcription_provider", "openai")
    assert svc.transcribir(ruta) == "openai"
    monkeypatch.setattr(settings, "transcription_provider", "gemini")
    assert svc.transcribir(ruta) == "gemini"


def test_gemini_convierte_a_wav_y_devuelve_el_texto(tmp_path, monkeypatch):
    ruta = tmp_path / "a.webm"
    ruta.write_bytes(b"x")
    monkeypatch.setattr(settings, "gemini_api_key", "k")
    monkeypatch.setattr(svc, "_a_wav_16k", lambda r: b"RIFFwav")

    class _Modelos:
        def generate_content(self, model, contents, config):
            assert contents[0] == svc.PROMPT_GEMINI
            return type("R", (), {"text": "  hola mundo \n"})()

    monkeypatch.setattr(svc, "_get_client", lambda: type("C", (), {"models": _Modelos()})())

    assert svc._transcribir_gemini(ruta) == "hola mundo"


def test_gemini_sin_clave_falla_limpio(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "")
    with pytest.raises(svc.TranscripcionError):
        svc._transcribir_gemini(tmp_path / "a.webm")
