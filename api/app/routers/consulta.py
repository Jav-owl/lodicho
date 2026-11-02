"""`POST /api/v1/consulta` (SSE) y `GET /api/v1/estado`.

Entrega 1: solo el camino de evidencia, solo input de texto. Ver el plan de
la entrega para el alcance completo y lo que queda deliberadamente afuera
(voz/URL, veredicto/ARQ, indicadores, cache semantico).

El endpoint es `async def` (arma y retorna el `StreamingResponse`, no
bloquea), pero `_generar_stream` es un generador SINCRONO normal: todo lo
que llama (Gemini via `generacion.py`, Qdrant via `qdrant_client.py`,
SQLAlchemy `Session`) es bloqueante, y Starlette envuelve un generador
sincrono con `iterate_in_threadpool` automaticamente — mismo principio que
`asyncio.to_thread` para Docling en `admin.py`, aplicado por el framework.
"""
from __future__ import annotations

import logging
from typing import Generator, Literal

from fastapi import APIRouter, Depends, Form
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.db.models import Candidatura, Consulta, Declaracion
from app.db.models.enums import TipoDeclaracion, TipoInput
from app.db.session import get_session
from app.schemas.consulta import (
    CandidaturaEventData,
    CandidaturaInfo,
    CandidaturaOpcion,
    CategoriaIntencion,
    CATEGORIAS_RECHAZADAS,
    DeclaracionInfo,
    ErrorEventData,
    EstadoResponse,
    EvidenciaEventData,
    RechazoEventData,
)
from app.services.evidencia import recuperar_evidencia
from app.services.generacion import GeminiGenerationError
from app.services.intencion import MENSAJES_RECHAZO, clasificar_intencion
from app.services.resolucion_candidatura import (
    NivelGobiernoDesconocidoError,
    buscar_candidaturas_por_nombre,
    dignidad_desde_fallback,
    es_fallback,
    extraer_nombre_candidato,
    nivel_gobierno_para_dignidad,
)
from app.services.sse import sse_event
from app.routers.transcripcion import ruta_audio

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["consulta"])

MENSAJE_SILENCIO_ELECTORAL = (
    "Silencio electoral activo: solo se permite consultar informes ya publicados."
)
MENSAJE_ENTRADA_NO_SOPORTADA = "Esta version solo admite entrada de texto o dictado."
MENSAJE_AUDIO_NO_ENCONTRADO = "No se encontro el audio de la consulta. Graba o sube el audio de nuevo."
MENSAJE_TEXTO_LARGO = (
    "La consulta es demasiado larga ({n} caracteres). El maximo es {max}: resume la afirmacion."
)
MENSAJE_TEXTO_VACIO = "El texto de la consulta no puede estar vacio."
MENSAJE_FALLO_CLASIFICACION = "No se pudo procesar la consulta. Intenta de nuevo."
MENSAJE_COMPARACION_NO_DISPONIBLE = (
    "El contraste lado a lado entre candidatos no esta disponible todavia."
)
MENSAJE_DIGNIDAD_NO_RECONOCIDA = "Dignidad no reconocida."
MENSAJE_CANDIDATURA_NO_ENCONTRADA = "No se encontro la candidatura seleccionada."
MENSAJE_FALLO_RETRIEVAL = "No se pudo recuperar evidencia. Intenta de nuevo."
MENSAJE_FALLO_PERSISTENCIA = "No se pudo registrar la consulta. Intenta de nuevo."
MENSAJE_ERROR_INESPERADO = "Ocurrio un error inesperado. Intenta de nuevo."


@router.get("/estado", response_model=EstadoResponse)
def obtener_estado() -> EstadoResponse:
    return EstadoResponse(modo_silencio_electoral=settings.modo_silencio_electoral)


@router.post("/consulta")
async def consulta(
    tipo_input: Literal["texto", "voz", "url"] = Form(...),
    texto: str | None = Form(None),
    url_fuente: str | None = Form(None),
    audio_id: str | None = Form(None),
    candidatura_id: str | None = Form(None),
    session: Session = Depends(get_session),
) -> StreamingResponse:
    return StreamingResponse(
        _generar_stream(tipo_input, texto, candidatura_id, session, audio_id),
        media_type="text/event-stream",
    )


def _persistir_consulta(
    session: Session, texto: str, intencion: str, audio_path: str | None = None
) -> Declaracion:
    fila_consulta = Consulta(
        tipo_input=TipoInput.voz if audio_path else TipoInput.texto,
        audio_path=audio_path,
        texto=texto,
        intencion_detectada=intencion,
        desde_cache=False,
    )
    session.add(fila_consulta)
    session.flush()
    declaracion = Declaracion(
        consulta_id=fila_consulta.id,
        texto=texto,
        tipo=TipoDeclaracion.dictado_usuario,
        atribuible=True,
        analisis_id=None,
    )
    session.add(declaracion)
    session.commit()
    return declaracion


def _a_candidatura_info(candidatura: Candidatura, nombre_candidato: str) -> CandidaturaInfo:
    return CandidaturaInfo(
        id=candidatura.id,
        nombre=nombre_candidato,
        dignidad=candidatura.dignidad,
        organizacion_politica=candidatura.organizacion_politica,
        jurisdiccion_dpa=candidatura.jurisdiccion_dpa,
        periodo=candidatura.periodo,
        estado_plan=candidatura.estado_plan.value,
    )


def _generar_stream(
    tipo_input: str,
    texto: str | None,
    candidatura_id_raw: str | None,
    session: Session,
    audio_id: str | None = None,
) -> Generator[str, None, None]:
    try:
        if settings.modo_silencio_electoral:
            yield sse_event("rechazo", RechazoEventData(motivo=MENSAJE_SILENCIO_ELECTORAL))
            return

        if tipo_input == "url":
            yield sse_event("error", ErrorEventData(detalle=MENSAJE_ENTRADA_NO_SOPORTADA))
            return
        audio_path: str | None = None
        if tipo_input == "voz":
            archivo = ruta_audio(audio_id or "")
            if archivo is None:
                yield sse_event("error", ErrorEventData(detalle=MENSAJE_AUDIO_NO_ENCONTRADO))
                return
            audio_path = str(archivo)
        if not texto or not texto.strip():
            yield sse_event("error", ErrorEventData(detalle=MENSAJE_TEXTO_VACIO))
            return
        texto = texto.strip()
        # Antes de cualquier llamada a un modelo: el texto largo (escrito, dictado o
        # transcrito) cuesta tokens en clasificacion, extraccion y retrieval.
        if len(texto) > settings.max_chars_consulta:
            yield sse_event(
                "error",
                ErrorEventData(
                    detalle=MENSAJE_TEXTO_LARGO.format(n=len(texto), max=settings.max_chars_consulta)
                ),
            )
            return

        try:
            categoria = clasificar_intencion(texto)
        except GeminiGenerationError:
            logger.exception("fallo al clasificar intencion")
            yield sse_event("error", ErrorEventData(detalle=MENSAJE_FALLO_CLASIFICACION))
            return

        if categoria in CATEGORIAS_RECHAZADAS:
            _persistir_consulta(session, texto, categoria.value, audio_path)
            yield sse_event("rechazo", RechazoEventData(motivo=MENSAJES_RECHAZO[categoria]))
            return

        if categoria == CategoriaIntencion.comparacion_factual:
            yield sse_event("error", ErrorEventData(detalle=MENSAJE_COMPARACION_NO_DISPONIBLE))
            return

        candidatura: Candidatura | None = None
        nombre_candidato_resuelto: str | None = None
        nivel_gobierno: str | None = None

        if candidatura_id_raw and es_fallback(candidatura_id_raw):
            dignidad = dignidad_desde_fallback(candidatura_id_raw)
            try:
                nivel_gobierno = nivel_gobierno_para_dignidad(dignidad)
            except NivelGobiernoDesconocidoError:
                yield sse_event("error", ErrorEventData(detalle=MENSAJE_DIGNIDAD_NO_RECONOCIDA))
                return

        elif candidatura_id_raw:
            try:
                candidatura = session.get(Candidatura, int(candidatura_id_raw))
            except ValueError:
                candidatura = None
            if candidatura is None:
                yield sse_event(
                    "error", ErrorEventData(detalle=MENSAJE_CANDIDATURA_NO_ENCONTRADA)
                )
                return
            nombre_candidato_resuelto = (
                candidatura.candidatos[0].nombre if candidatura.candidatos else candidatura.dignidad
            )
            try:
                nivel_gobierno = nivel_gobierno_para_dignidad(candidatura.dignidad)
            except NivelGobiernoDesconocidoError:
                logger.error(
                    "dignidad sin nivel_gobierno mapeado: %s (candidatura %s)",
                    candidatura.dignidad,
                    candidatura.id,
                )
                nivel_gobierno = None

        else:
            try:
                nombre = extraer_nombre_candidato(texto)
            except GeminiGenerationError:
                logger.exception("fallo al extraer nombre de candidato")
                nombre = None

            opciones = buscar_candidaturas_por_nombre(session, nombre) if nombre else []

            if len(opciones) != 1:
                yield sse_event(
                    "candidatura",
                    CandidaturaEventData(
                        opciones=[
                            CandidaturaOpcion(
                                id=o.id, nombre=o.nombre, dignidad=o.dignidad, organizacion=o.organizacion
                            )
                            for o in opciones
                        ]
                    ),
                )
                return

            candidatura = session.get(Candidatura, opciones[0].id)
            nombre_candidato_resuelto = opciones[0].nombre
            try:
                nivel_gobierno = nivel_gobierno_para_dignidad(candidatura.dignidad)
            except NivelGobiernoDesconocidoError:
                logger.error(
                    "dignidad sin nivel_gobierno mapeado: %s (candidatura %s)",
                    candidatura.dignidad,
                    candidatura.id,
                )
                nivel_gobierno = None

        try:
            evidencias = recuperar_evidencia(
                texto, candidatura=candidatura, nivel_gobierno=nivel_gobierno
            )
        except Exception:
            logger.exception("fallo de retrieval en /consulta")
            yield sse_event("error", ErrorEventData(detalle=MENSAJE_FALLO_RETRIEVAL))
            return

        try:
            declaracion = _persistir_consulta(session, texto, categoria.value, audio_path)
        except Exception:
            session.rollback()
            logger.exception("fallo al persistir Consulta/Declaracion")
            yield sse_event("error", ErrorEventData(detalle=MENSAJE_FALLO_PERSISTENCIA))
            return

        yield sse_event(
            "evidencia",
            EvidenciaEventData(
                candidatura=(
                    _a_candidatura_info(candidatura, nombre_candidato_resuelto or candidatura.dignidad)
                    if candidatura
                    else None
                ),
                declaracion=DeclaracionInfo(id=declaracion.id, texto=texto),
                evidencias=evidencias,
            ),
        )

    except Exception:
        logger.exception("fallo no manejado en /consulta")
        yield sse_event("error", ErrorEventData(detalle=MENSAJE_ERROR_INESPERADO))
