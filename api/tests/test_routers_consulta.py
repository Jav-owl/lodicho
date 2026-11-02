"""Tests de integracion de `POST /api/v1/consulta`: verifican la secuencia
EXACTA de eventos SSE para cada escenario del pipeline de evidencia, con
Gemini/Qdrant mockeados (parcheados donde se usan, en `app.routers.consulta`,
no donde se definen) y Postgres reemplazado por SQLite en memoria.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config.settings import settings
from app.db.base import Base
from app.db.models import Candidato, Candidatura, Consulta, Declaracion
from app.db.models.enums import EstadoPlanCandidatura
from app.db.session import get_session
from app.main import app
from app.schemas.consulta import CategoriaIntencion, EvidenciaItem
from app.services.generacion import GeminiGenerationError
from app.services.resolucion_candidatura import CandidaturaCandidata

TABLAS = [Candidatura.__table__, Candidato.__table__, Consulta.__table__, Declaracion.__table__]


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine, tables=TABLAS)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(db_session: Session):
    def _override_get_session():
        yield db_session

    app.dependency_overrides[get_session] = _override_get_session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_session, None)


def _parse_eventos(texto_sse: str) -> list[tuple[str, dict]]:
    eventos = []
    for bloque in texto_sse.strip().split("\n\n"):
        if not bloque.strip():
            continue
        evento = "message"
        data_lineas = []
        for linea in bloque.split("\n"):
            if linea.startswith("event:"):
                evento = linea[len("event:") :].strip()
            elif linea.startswith("data:"):
                data_lineas.append(linea[len("data:") :].strip())
        eventos.append((evento, json.loads("\n".join(data_lineas))))
    return eventos


def _crear_candidatura(session: Session, *, dignidad="alcalde", estado_plan=EstadoPlanCandidatura.registrado):
    candidatura = Candidatura(
        organizacion_politica="Partido X",
        lista_numero="1",
        dignidad=dignidad,
        jurisdiccion_dpa="0201",
        periodo="2027-2031",
        estado_plan=estado_plan,
    )
    session.add(candidatura)
    session.flush()
    session.add(Candidato(nombre="Ana Torres", candidatura_id=candidatura.id, posicion_lista=1))
    session.commit()
    return candidatura


def _post_consulta(client: TestClient, **kwargs):
    data = {"tipo_input": "texto"}
    data.update(kwargs)
    return client.post("/api/v1/consulta", data=data)


def test_silencio_electoral_activo_produce_solo_rechazo(client, monkeypatch):
    monkeypatch.setattr(settings, "modo_silencio_electoral", True)

    respuesta = _post_consulta(client, texto="una propuesta cualquiera")
    eventos = _parse_eventos(respuesta.text)

    assert [e[0] for e in eventos] == ["rechazo"]


def test_tipo_input_voz_sin_audio_id_produce_solo_error(client):
    respuesta = _post_consulta(client, tipo_input="voz", texto="algo dictado")
    eventos = _parse_eventos(respuesta.text)

    assert [e[0] for e in eventos] == ["error"]
    assert "audio" in eventos[0][1]["detalle"].lower()


def test_tipo_input_url_produce_solo_error(client):
    respuesta = _post_consulta(client, tipo_input="url", url_fuente="https://x.com/n")
    eventos = _parse_eventos(respuesta.text)

    assert [e[0] for e in eventos] == ["error"]


def test_texto_demasiado_largo_se_rechaza_antes_de_llamar_a_modelos(client, monkeypatch):
    monkeypatch.setattr(settings, "max_chars_consulta", 50)

    def _no_debe_llamarse(*a, **k):
        raise AssertionError("no debe clasificar un texto sobre el limite")

    monkeypatch.setattr("app.routers.consulta.clasificar_intencion", _no_debe_llamarse)
    respuesta = _post_consulta(client, texto="a" * 51)
    eventos = _parse_eventos(respuesta.text)

    assert [e[0] for e in eventos] == ["error"]
    assert "50" in eventos[0][1]["detalle"]


def test_voz_con_audio_id_guarda_audio_path(client, db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "audio_path", str(tmp_path))
    audio_id = "a" * 32
    (tmp_path / f"{audio_id}.webm").write_bytes(b"x")
    monkeypatch.setattr(
        "app.routers.consulta.clasificar_intencion",
        lambda texto: CategoriaIntencion.recomendacion_voto,
    )

    respuesta = _post_consulta(client, tipo_input="voz", texto="vote por Ana", audio_id=audio_id)
    eventos = _parse_eventos(respuesta.text)

    assert [e[0] for e in eventos] == ["rechazo"]
    fila = db_session.scalars(select(Consulta)).one()
    assert fila.tipo_input.value == "voz"
    assert fila.audio_path.endswith(f"{audio_id}.webm")


def test_texto_vacio_produce_solo_error(client):
    respuesta = _post_consulta(client, texto="   ")
    eventos = _parse_eventos(respuesta.text)

    assert [e[0] for e in eventos] == ["error"]


def test_fallo_de_clasificacion_produce_solo_error(client, monkeypatch):
    def _falla(texto):
        raise GeminiGenerationError("timeout")

    monkeypatch.setattr("app.routers.consulta.clasificar_intencion", _falla)

    respuesta = _post_consulta(client, texto="una propuesta")
    eventos = _parse_eventos(respuesta.text)

    assert [e[0] for e in eventos] == ["error"]


def test_categoria_rechazada_emite_rechazo_y_persiste_consulta(client, db_session, monkeypatch):
    monkeypatch.setattr(
        "app.routers.consulta.clasificar_intencion",
        lambda texto: CategoriaIntencion.recomendacion_voto,
    )

    respuesta = _post_consulta(client, texto="por quien debo votar")
    eventos = _parse_eventos(respuesta.text)

    assert [e[0] for e in eventos] == ["rechazo"]
    fila = db_session.execute(select(Consulta)).scalar_one()
    assert fila.intencion_detectada == CategoriaIntencion.recomendacion_voto.value


def test_comparacion_factual_produce_solo_error(client, monkeypatch):
    monkeypatch.setattr(
        "app.routers.consulta.clasificar_intencion",
        lambda texto: CategoriaIntencion.comparacion_factual,
    )

    respuesta = _post_consulta(client, texto="compara a los dos candidatos")
    eventos = _parse_eventos(respuesta.text)

    assert [e[0] for e in eventos] == ["error"]


def _mock_clasificacion_valida(monkeypatch):
    monkeypatch.setattr(
        "app.routers.consulta.clasificar_intencion",
        lambda texto: CategoriaIntencion.contrastacion_declaracion,
    )


def test_sin_candidatura_id_cero_matches_emite_candidatura_con_opciones_vacias(client, monkeypatch):
    _mock_clasificacion_valida(monkeypatch)
    monkeypatch.setattr("app.routers.consulta.extraer_nombre_candidato", lambda texto: "Nadie Conocido")
    monkeypatch.setattr("app.routers.consulta.buscar_candidaturas_por_nombre", lambda session, nombre: [])

    respuesta = _post_consulta(client, texto="dijo que iba a hacer un puente")
    eventos = _parse_eventos(respuesta.text)

    assert eventos == [("candidatura", {"opciones": [], "candidatura": None})]


def test_sin_candidatura_id_multiples_matches_emite_opciones(client, monkeypatch):
    _mock_clasificacion_valida(monkeypatch)
    monkeypatch.setattr("app.routers.consulta.extraer_nombre_candidato", lambda texto: "Perez")
    opciones = [
        CandidaturaCandidata(id=1, nombre="Juan Perez", dignidad="alcalde", organizacion="Partido X"),
        CandidaturaCandidata(id=2, nombre="Ana Perez", dignidad="prefecto", organizacion="Partido Y"),
    ]
    monkeypatch.setattr("app.routers.consulta.buscar_candidaturas_por_nombre", lambda session, nombre: opciones)

    respuesta = _post_consulta(client, texto="Perez dijo que...")
    eventos = _parse_eventos(respuesta.text)

    assert len(eventos) == 1
    assert eventos[0][0] == "candidatura"
    assert len(eventos[0][1]["opciones"]) == 2


def test_sin_candidatura_id_un_match_va_directo_a_evidencia_y_persiste_antes(client, db_session, monkeypatch):
    _mock_clasificacion_valida(monkeypatch)
    candidatura = _crear_candidatura(db_session)
    monkeypatch.setattr("app.routers.consulta.extraer_nombre_candidato", lambda texto: "Ana Torres")
    monkeypatch.setattr(
        "app.routers.consulta.buscar_candidaturas_por_nombre",
        lambda session, nombre: [
            CandidaturaCandidata(id=candidatura.id, nombre="Ana Torres", dignidad="alcalde", organizacion="Partido X")
        ],
    )
    monkeypatch.setattr("app.routers.consulta.recuperar_evidencia", lambda *a, **k: [])

    respuesta = _post_consulta(client, texto="Ana Torres propuso un puente")
    eventos = _parse_eventos(respuesta.text)

    assert [e[0] for e in eventos] == ["evidencia"]
    assert eventos[0][1]["candidatura"]["id"] == candidatura.id
    assert db_session.execute(select(Consulta)).scalar_one_or_none() is not None
    assert db_session.execute(select(Declaracion)).scalar_one_or_none() is not None


def test_fallback_valido_devuelve_solo_marco_legal_y_candidatura_null(client, monkeypatch):
    _mock_clasificacion_valida(monkeypatch)

    def _fake_recuperar(texto, *, candidatura, nivel_gobierno, **kwargs):
        assert candidatura is None
        assert nivel_gobierno == "cantonal"
        return [EvidenciaItem(paso="marco_legal", texto="art 55", score=0.9, doc_id="cootad", git_sha="abc", point_id="p1")]

    monkeypatch.setattr("app.routers.consulta.recuperar_evidencia", _fake_recuperar)

    respuesta = _post_consulta(client, texto="una propuesta", candidatura_id="fallback_alcalde")
    eventos = _parse_eventos(respuesta.text)

    assert [e[0] for e in eventos] == ["evidencia"]
    assert eventos[0][1]["candidatura"] is None
    assert all(e["paso"] == "marco_legal" for e in eventos[0][1]["evidencias"])


def test_fallback_con_dignidad_invalida_produce_error(client, monkeypatch):
    _mock_clasificacion_valida(monkeypatch)

    respuesta = _post_consulta(client, texto="una propuesta", candidatura_id="fallback_gobernador")
    eventos = _parse_eventos(respuesta.text)

    assert [e[0] for e in eventos] == ["error"]


def test_candidatura_id_numerico_inexistente_produce_error(client, monkeypatch):
    _mock_clasificacion_valida(monkeypatch)

    respuesta = _post_consulta(client, texto="una propuesta", candidatura_id="9999")
    eventos = _parse_eventos(respuesta.text)

    assert [e[0] for e in eventos] == ["error"]


def test_candidatura_sin_plan_registrado_solo_evidencia_marco_legal(client, db_session, monkeypatch):
    _mock_clasificacion_valida(monkeypatch)
    candidatura = _crear_candidatura(db_session, estado_plan=EstadoPlanCandidatura.sin_plan_registrado)

    def _fake_recuperar(texto, *, candidatura, nivel_gobierno, **kwargs):
        return [EvidenciaItem(paso="marco_legal", texto="art 1", score=0.5, doc_id="cootad", git_sha="abc", point_id="p2")]

    monkeypatch.setattr("app.routers.consulta.recuperar_evidencia", _fake_recuperar)

    respuesta = _post_consulta(client, texto="una propuesta", candidatura_id=str(candidatura.id))
    eventos = _parse_eventos(respuesta.text)

    assert [e[0] for e in eventos] == ["evidencia"]


def test_fallo_de_commit_produce_error_y_no_evidencia(client, db_session, monkeypatch):
    _mock_clasificacion_valida(monkeypatch)
    candidatura = _crear_candidatura(db_session)
    monkeypatch.setattr("app.routers.consulta.recuperar_evidencia", lambda *a, **k: [])

    def _commit_falla():
        raise RuntimeError("conexion perdida")

    monkeypatch.setattr(db_session, "commit", _commit_falla)

    respuesta = _post_consulta(client, texto="una propuesta", candidatura_id=str(candidatura.id))
    eventos = _parse_eventos(respuesta.text)

    assert [e[0] for e in eventos] == ["error"]


def test_excepcion_no_prevista_produce_un_solo_evento_error_sin_500(client, monkeypatch):
    def _explota(texto):
        raise ValueError("algo totalmente inesperado")

    monkeypatch.setattr("app.routers.consulta.clasificar_intencion", _explota)

    respuesta = _post_consulta(client, texto="una propuesta")
    eventos = _parse_eventos(respuesta.text)

    assert respuesta.status_code == 200
    assert [e[0] for e in eventos] == ["error"]
