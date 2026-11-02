"""Carga masiva de candidaturas por CSV: validacion, idempotencia, actualizacion
de `estado_candidatura` (inscrito -> oficializado) y todo-o-nada."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import Candidato, Candidatura
from app.db.models.enums import EstadoCandidatura, EstadoPlanCandidatura
from app.db.session import get_session
from app.main import app
from app.routers.admin import requiere_admin
from app.services import importacion_candidaturas as imp

ENC = "organizacion_politica,lista_numero,dignidad,jurisdiccion_dpa,periodo,estado_candidatura,estado_plan,nombre,posicion_lista\n"


def fila(org="Partido X", lista="1", dig="alcalde", dpa="0201", per="2027-2031",
         est="inscrito", plan="registrado", nombre="Ana Torres", pos="1"):
    return f"{org},{lista},{dig},{dpa},{per},{est},{plan},{nombre},{pos}\n"


@pytest.fixture()
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine, tables=[Candidatura.__table__, Candidato.__table__])
    s = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    yield s
    s.close()


def cuenta(session, modelo):
    return session.scalar(select(func.count()).select_from(modelo))


def test_carga_nueva_crea_candidaturas_y_candidatos(session):
    csv = ENC + fila() + fila(nombre="Luis Mora", pos="2") + fila(lista="2", org="Partido Y", nombre="Eva Paz")
    r = imp.procesar(session, csv.encode(), aplicar=True)

    assert not r.errores and r.aplicado
    assert (r.candidaturas_nuevas, r.candidatos_nuevos) == (2, 3)
    assert cuenta(session, Candidatura) == 2 and cuenta(session, Candidato) == 3


def test_vista_previa_no_escribe(session):
    r = imp.procesar(session, (ENC + fila()).encode(), aplicar=False)

    assert r.candidaturas_nuevas == 1 and not r.aplicado
    assert cuenta(session, Candidatura) == 0


def test_recargar_el_mismo_archivo_es_idempotente(session):
    csv = (ENC + fila() + fila(nombre="Luis Mora", pos="2")).encode()
    imp.procesar(session, csv, aplicar=True)
    r = imp.procesar(session, csv, aplicar=True)

    assert (r.candidaturas_nuevas, r.candidaturas_sin_cambios, r.candidatos_sin_cambios) == (0, 1, 2)
    assert cuenta(session, Candidatura) == 1 and cuenta(session, Candidato) == 2


def test_nueva_carga_actualiza_inscrito_a_oficializado(session):
    imp.procesar(session, (ENC + fila(est="inscrito")).encode(), aplicar=True)
    r = imp.procesar(session, (ENC + fila(est="oficializado")).encode(), aplicar=True)

    assert r.candidaturas_actualizadas == 1
    assert r.cambios_estado[0]["de"] == "inscrito" and r.cambios_estado[0]["a"] == "oficializado"
    assert session.scalars(select(Candidatura)).one().estado_candidatura == EstadoCandidatura.oficializado
    assert not r.avisos


def test_retroceder_de_oficializado_avisa(session):
    imp.procesar(session, (ENC + fila(est="oficializado")).encode(), aplicar=True)
    r = imp.procesar(session, (ENC + fila(est="inscrito")).encode(), aplicar=False)

    assert any("retrocede" in a for a in r.avisos)


def test_candidato_existente_que_no_viene_se_conserva(session):
    imp.procesar(session, (ENC + fila() + fila(nombre="Luis Mora", pos="2")).encode(), aplicar=True)
    r = imp.procesar(session, (ENC + fila()).encode(), aplicar=True)

    assert r.candidatos_no_incluidos == 1
    assert cuenta(session, Candidato) == 2


def test_nombre_con_tildes_y_mayusculas_es_el_mismo_candidato(session):
    imp.procesar(session, (ENC + fila(nombre="José Pérez")).encode(), aplicar=True)
    r = imp.procesar(session, (ENC + fila(nombre="JOSE PEREZ", pos="3")).encode(), aplicar=True)

    assert (r.candidatos_nuevos, r.candidatos_actualizados) == (0, 1)
    assert cuenta(session, Candidato) == 1


def test_un_error_impide_escribir_todo(session):
    csv = ENC + fila() + fila(lista="2", dig="reina", nombre="Eva Paz")
    r = imp.procesar(session, csv.encode(), aplicar=True)

    assert r.errores and not r.aplicado
    assert cuenta(session, Candidatura) == 0
    assert r.errores[0][0] == 3  # numero de linea del CSV


@pytest.mark.parametrize("campos,esperado", [
    ({"per": "2031-2027"}, "periodo"),
    ({"est": "oficial"}, "estado_candidatura"),
    ({"plan": "tal vez"}, "estado_plan"),
    ({"pos": "0"}, "posicion_lista"),
    ({"dpa": "02a1"}, "jurisdiccion_dpa"),
    ({"nombre": ""}, "vacios"),
    ({"org": "EJEMPLO - BORRAR"}, "ejemplo"),
])
def test_validaciones_por_campo(session, campos, esperado):
    r = imp.procesar(session, (ENC + fila(**campos)).encode(), aplicar=False)

    assert r.errores and esperado in r.errores[0][1].lower()


def test_dpa_sin_cero_inicial_se_completa_con_aviso(session):
    r = imp.procesar(session, (ENC + fila(dpa="201")).encode(), aplicar=True)

    assert not r.errores
    assert session.scalars(select(Candidatura)).one().jurisdiccion_dpa == "0201"
    assert any("cero inicial" in a for a in r.avisos)


def test_filas_de_una_misma_lista_con_estado_distinto_es_error(session):
    csv = ENC + fila(est="inscrito") + fila(nombre="Luis Mora", pos="2", est="oficializado")
    r = imp.procesar(session, csv.encode(), aplicar=False)

    assert any("difiere" in m for _, m in r.errores)


def test_candidato_o_posicion_repetidos_son_error(session):
    r = imp.procesar(session, (ENC + fila() + fila()).encode(), aplicar=False)

    mensajes = " ".join(m for _, m in r.errores)
    assert "repetido" in mensajes and "repetida" in mensajes


def test_faltan_columnas(session):
    r = imp.procesar(session, b"nombre,lista_numero\nAna,1\n", aplicar=False)

    assert r.errores and "Faltan columnas" in r.errores[0][1]


def test_punto_y_coma_y_cp1252_de_excel(session):
    texto = ENC.replace(",", ";") + fila(nombre="Nuñez Peña").replace(",", ";")
    r = imp.procesar(session, texto.encode("cp1252"), aplicar=True)

    assert not r.errores
    assert session.scalars(select(Candidato)).one().nombre == "Nuñez Peña"


def test_actualiza_estado_plan_y_organizacion(session):
    imp.procesar(session, (ENC + fila()).encode(), aplicar=True)
    imp.procesar(session, (ENC + fila(org="Partido Nuevo", plan="sin_plan_registrado")).encode(), aplicar=True)

    c = session.scalars(select(Candidatura)).one()
    assert c.organizacion_politica == "Partido Nuevo"
    assert c.estado_plan == EstadoPlanCandidatura.sin_plan_registrado


# --- endpoint ---

@pytest.fixture()
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[requiere_admin] = lambda: None
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _post(client, csv, aplicar):
    return client.post("/api/v1/admin/candidaturas/importar",
                       files={"archivo": ("c.csv", csv.encode(), "text/csv")}, data={"aplicar": str(aplicar).lower()})


def test_endpoint_previsualiza_y_luego_aplica(client, session):
    previa = _post(client, ENC + fila(), False).json()
    assert previa["candidaturas"]["nuevas"] == 1 and not previa["aplicado"]
    assert cuenta(session, Candidatura) == 0

    final = _post(client, ENC + fila(), True).json()
    assert final["aplicado"] and cuenta(session, Candidatura) == 1


def test_endpoint_devuelve_errores_con_fila(client):
    cuerpo = _post(client, ENC + fila(dig="reina"), True).json()

    assert cuerpo["total_errores"] == 1 and cuerpo["errores"][0]["fila"] == 2 and not cuerpo["aplicado"]


def test_endpoint_archivo_vacio_da_400(client):
    assert client.post("/api/v1/admin/candidaturas/importar",
                       files={"archivo": ("c.csv", b"", "text/csv")}).status_code == 400


# --- formato del archivo oficial (docs/candidaturas_seccionales_*.csv) ---

CNE = ("Candidato/a,Organización política,Número de lista,Dignidad,Jurisdicción DPA,Período,Estado CNE\n"
       "César Pérez Roca,Unidad Popular / Pachakutik,2-18,Prefectura,Guayas (09),2027–2031,Oficializado\n"
       "Pabel Muñoz,Alianza Ciudadana (UP / PSE / Todos),2-17-70,Alcaldía,Quito (1701),2027–2031,Aún sin oficializar\n")


def test_formato_cne_se_acepta_tal_cual(session):
    r = imp.procesar(session, CNE.encode("utf-8-sig"), aplicar=True)

    assert not r.errores and r.candidaturas_nuevas == 2
    por_dpa = {c.jurisdiccion_dpa: c for c in session.scalars(select(Candidatura))}
    prefectura, alcaldia = por_dpa["09"], por_dpa["1701"]
    assert (prefectura.dignidad, prefectura.lista_numero, prefectura.periodo) == ("prefecto", "2-18", "2027-2031")
    assert alcaldia.dignidad == "alcalde"
    assert prefectura.estado_candidatura == EstadoCandidatura.oficializado
    assert alcaldia.estado_candidatura == EstadoCandidatura.inscrito  # "Aún sin oficializar"


def test_sin_estado_plan_crea_registrado_con_aviso_y_no_pisa_existentes(session):
    imp.procesar(session, (ENC + fila(plan="sin_plan_registrado", dig="prefecto", dpa="09", lista="2-18",
                                       nombre="César Pérez Roca")).encode(), aplicar=True)
    r = imp.procesar(session, CNE.encode(), aplicar=True)

    assert any("estado_plan" in a for a in r.avisos)
    por_dpa = {c.jurisdiccion_dpa: c for c in session.scalars(select(Candidatura))}
    assert por_dpa["09"].estado_plan == EstadoPlanCandidatura.sin_plan_registrado  # conservado
    assert por_dpa["1701"].estado_plan == EstadoPlanCandidatura.registrado          # nuevo


def test_sin_posicion_se_asigna_por_orden(session):
    csv = CNE + "Otro Nombre,Unidad Popular / Pachakutik,2-18,Prefectura,Guayas (09),2027–2031,Oficializado\n"
    imp.procesar(session, csv.encode(), aplicar=True)

    prefectura = session.scalars(select(Candidatura).where(Candidatura.jurisdiccion_dpa == "09")).one()
    assert sorted((c.posicion_lista, c.nombre) for c in prefectura.candidatos) == [(1, "César Pérez Roca"), (2, "Otro Nombre")]
