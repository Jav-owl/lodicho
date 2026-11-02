"""Endpoints publicos de solo lectura sobre candidaturas: la "vista de
candidatos" que el ciudadano usa para explorar y elegir con quien
contrastar, en vez de depender por completo de que el clasificador
adivine el nombre desde texto libre (ver `services/resolucion_candidatura.py`).

Sin auth -- son datos publicos (candidaturas registradas ante el CNE), a
diferencia de `routers/admin.py` que exige `requiere_admin`.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.models import Analisis, Candidatura
from app.db.models.enums import EstadoAnalisis
from app.db.session import get_session
from app.services.qdrant_client import listar_plan_trabajo

router = APIRouter(prefix="/api/v1", tags=["candidaturas"])


class CandidatoInfo(BaseModel):
    id: int
    nombre: str
    posicion_lista: int


class CandidaturaResumen(BaseModel):
    id: int
    organizacion_politica: str
    lista_numero: str
    dignidad: str
    jurisdiccion_dpa: str
    periodo: str
    estado_plan: str
    estado_candidatura: str
    candidatos: list[CandidatoInfo]


def _a_resumen(c: Candidatura) -> CandidaturaResumen:
    return CandidaturaResumen(
        id=c.id,
        organizacion_politica=c.organizacion_politica,
        lista_numero=c.lista_numero,
        dignidad=c.dignidad,
        jurisdiccion_dpa=c.jurisdiccion_dpa,
        periodo=c.periodo,
        estado_plan=c.estado_plan.value,
        estado_candidatura=c.estado_candidatura.value,
        candidatos=[
            CandidatoInfo(id=cand.id, nombre=cand.nombre, posicion_lista=cand.posicion_lista)
            for cand in sorted(c.candidatos, key=lambda x: x.posicion_lista)
        ],
    )


@router.get("/candidaturas", response_model=list[CandidaturaResumen])
def listar_candidaturas(session: Session = Depends(get_session)) -> list[CandidaturaResumen]:
    filas = (
        session.execute(
            select(Candidatura)
            .options(selectinload(Candidatura.candidatos))
            .order_by(Candidatura.jurisdiccion_dpa, Candidatura.dignidad)
        )
        .scalars()
        .all()
    )
    return [_a_resumen(c) for c in filas]


class InformePublicado(BaseModel):
    id: int
    afirmacion: str
    veredicto: str
    factibilidad_score: float | None
    publicado_en: str | None


class CandidaturaDetalle(CandidaturaResumen):
    informes_publicados: list[InformePublicado]
    propuestas: list[str]


@router.get("/candidaturas/{candidatura_id}", response_model=CandidaturaDetalle)
def obtener_candidatura(candidatura_id: int, session: Session = Depends(get_session)) -> CandidaturaDetalle:
    candidatura = session.get(Candidatura, candidatura_id)
    if candidatura is None:
        raise HTTPException(status_code=404, detail="Candidatura no encontrada")

    informes = (
        session.execute(
            select(Analisis)
            .where(
                Analisis.candidatura_id == candidatura_id,
                Analisis.estado == EstadoAnalisis.publicado,
            )
            .order_by(Analisis.publicado_en.desc())
        )
        .scalars()
        .all()
    )

    propuestas = [
        punto.payload["texto"]
        for punto in listar_plan_trabajo(candidatura_id)
        if punto.payload and punto.payload.get("texto")
    ]

    resumen = _a_resumen(candidatura)
    return CandidaturaDetalle(
        **resumen.model_dump(),
        propuestas=propuestas,
        informes_publicados=[
            InformePublicado(
                id=a.id,
                afirmacion=a.afirmacion,
                veredicto=a.veredicto.value,
                factibilidad_score=float(a.factibilidad_score) if a.factibilidad_score is not None else None,
                publicado_en=a.publicado_en.isoformat() if a.publicado_en else None,
            )
            for a in informes
        ],
    )
