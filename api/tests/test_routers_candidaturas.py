"""Tests de integracion de `GET /api/v1/candidaturas` y
`GET /api/v1/candidaturas/{id}`: la vista publica que el ciudadano usa
para explorar candidatos sin depender de que el clasificador adivine un
nombre desde texto libre.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import Analisis, Candidato, Candidatura
from app.db.models.enums import EstadoAnalisis, EstadoPlanCandidatura, Veredicto
from app.db.session import get_session
from app.main import app

TABLAS = [Candidatura.__table__, Candidato.__table__, Analisis.__table__]


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


def _crear_candidatura(session: Session, **overrides):
    valores = dict(
        organizacion_politica="Partido X",
        lista_numero="1",
        dignidad="alcalde",
        jurisdiccion_dpa="0201",
        periodo="2027-2031",
        estado_plan=EstadoPlanCandidatura.registrado,
    )
    valores.update(overrides)
    candidatura = Candidatura(**valores)
    session.add(candidatura)
    session.flush()
    session.add(Candidato(nombre="Ana Torres", candidatura_id=candidatura.id, posicion_lista=1))
    session.commit()
    return candidatura


def test_listar_candidaturas_vacio(client):
    respuesta = client.get("/api/v1/candidaturas")
    assert respuesta.status_code == 200
    assert respuesta.json() == []


def test_listar_candidaturas_incluye_candidatos(client, db_session):
    _crear_candidatura(db_session)

    respuesta = client.get("/api/v1/candidaturas")
    cuerpo = respuesta.json()

    assert len(cuerpo) == 1
    assert cuerpo[0]["candidatos"] == [{"id": 1, "nombre": "Ana Torres", "posicion_lista": 1}]
    assert cuerpo[0]["estado_plan"] == "registrado"


def test_obtener_candidatura_inexistente_404(client):
    respuesta = client.get("/api/v1/candidaturas/999")
    assert respuesta.status_code == 404


def test_obtener_candidatura_solo_incluye_informes_publicados(client, db_session, monkeypatch):
    # El endpoint trae el plan con scroll a Qdrant; aca no se prueba eso, y
    # sin este stub el test exige un Qdrant real corriendo en localhost.
    monkeypatch.setattr("app.routers.candidaturas.listar_plan_trabajo", lambda *a, **k: [])
    candidatura = _crear_candidatura(db_session)

    session_ = db_session
    session_.add(
        Analisis(
            candidatura_id=candidatura.id,
            afirmacion="Construira un hospital",
            veredicto=Veredicto.viable_y_en_plan,
            payload_json={},
            modelo_usado="gemini-2.5-pro",
            estado=EstadoAnalisis.publicado,
            factibilidad_score=Decimal("72.50"),
            publicado_en=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
    )
    session_.add(
        Analisis(
            candidatura_id=candidatura.id,
            afirmacion="Pavimentara toda la parroquia",
            veredicto=Veredicto.incomprobable,
            payload_json={},
            modelo_usado="gemini-2.5-pro",
            estado=EstadoAnalisis.borrador,
        )
    )
    session_.commit()

    respuesta = client.get(f"/api/v1/candidaturas/{candidatura.id}")
    cuerpo = respuesta.json()

    assert respuesta.status_code == 200
    assert len(cuerpo["informes_publicados"]) == 1
    assert cuerpo["informes_publicados"][0]["afirmacion"] == "Construira un hospital"
    assert cuerpo["informes_publicados"][0]["factibilidad_score"] == 72.5
