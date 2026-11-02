"""Carga masiva de candidaturas por CSV desde el panel de admin.

Dos pasos sin estado en el servidor: el front sube el mismo archivo primero con
`aplicar=false` (vista previa, no escribe) y luego con `aplicar=true`. Se reprocesa
en cada llamada, asi que lo aplicado es siempre lo que se valido sobre la base actual.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.routers.admin import requiere_admin
from app.services import importacion_candidaturas as imp

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


class ErrorFila(BaseModel):
    fila: int
    mensaje: str


class ConteoCandidaturas(BaseModel):
    nuevas: int
    actualizadas: int
    sin_cambios: int


class ConteoCandidatos(BaseModel):
    nuevos: int
    actualizados: int
    sin_cambios: int
    no_incluidos: int


class ImportacionResponse(BaseModel):
    aplicado: bool
    filas_leidas: int
    candidaturas: ConteoCandidaturas
    candidatos: ConteoCandidatos
    cambios_estado: list[dict]
    avisos: list[str]
    total_errores: int
    errores: list[ErrorFila]


@router.post(
    "/candidaturas/importar", response_model=ImportacionResponse, dependencies=[Depends(requiere_admin)]
)
def importar_candidaturas(
    archivo: UploadFile = File(...),
    aplicar: bool = Form(False),
    session: Session = Depends(get_session),
) -> ImportacionResponse:
    contenido = archivo.file.read(imp.MAX_BYTES + 1)
    if not contenido:
        raise HTTPException(status_code=400, detail="El archivo esta vacio.")
    r = imp.procesar(session, contenido, aplicar=aplicar)
    return ImportacionResponse(
        aplicado=r.aplicado,
        filas_leidas=r.filas_leidas,
        candidaturas=ConteoCandidaturas(
            nuevas=r.candidaturas_nuevas, actualizadas=r.candidaturas_actualizadas,
            sin_cambios=r.candidaturas_sin_cambios),
        candidatos=ConteoCandidatos(
            nuevos=r.candidatos_nuevos, actualizados=r.candidatos_actualizados,
            sin_cambios=r.candidatos_sin_cambios, no_incluidos=r.candidatos_no_incluidos),
        cambios_estado=r.cambios_estado,
        avisos=r.avisos,
        total_errores=len(r.errores),
        errores=[ErrorFila(fila=n, mensaje=m) for n, m in sorted(r.errores)[: imp.MAX_ERRORES_DEVUELTOS]],
    )
