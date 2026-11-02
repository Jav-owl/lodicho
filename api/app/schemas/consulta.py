"""Contratos Pydantic de `/api/v1/consulta` y `/api/v1/estado`.

El frontend (`web/js/api.js`, `web/js/views/home-view.js`) ya esta construido
contra la forma exacta de estos modelos — no son un diseno libre, son el
contrato que el cliente ya sabe interpretar. Ver el plan de la entrega para
el detalle event por event.
"""
from __future__ import annotations

import enum

from pydantic import BaseModel

from app.db.models.enums import PasoEvidencia


class CategoriaIntencion(str, enum.Enum):
    """Categoria que devuelve el clasificador de intencion (Gemini).

    El modelo SOLO puede devolver una de estas categorias via
    `response_schema` — el texto que ve el usuario cuando se rechaza una
    consulta sale siempre de `MENSAJES_RECHAZO` (constante Python), nunca
    generado por el modelo (CLAUDE.md, regla critica 5).
    """

    contrastacion_declaracion = "contrastacion_declaracion"
    comparacion_factual = "comparacion_factual"
    recomendacion_voto = "recomendacion_voto"
    comparacion_calidad = "comparacion_calidad"
    opinion_persona = "opinion_persona"


CATEGORIAS_RECHAZADAS: frozenset[CategoriaIntencion] = frozenset(
    {
        CategoriaIntencion.recomendacion_voto,
        CategoriaIntencion.comparacion_calidad,
        CategoriaIntencion.opinion_persona,
    }
)


class IntencionClasificada(BaseModel):
    """`response_schema` de la llamada a Gemini que clasifica la intencion."""

    categoria: CategoriaIntencion


class NombreExtraido(BaseModel):
    """`response_schema` de la llamada a Gemini que extrae el nombre del
    candidato mencionado en la declaracion."""

    nombre_candidato: str | None


class CandidaturaOpcion(BaseModel):
    id: int
    nombre: str
    dignidad: str
    organizacion: str


class CandidaturaEventData(BaseModel):
    opciones: list[CandidaturaOpcion] = []
    candidatura: None = None


class CandidaturaInfo(BaseModel):
    id: int
    nombre: str
    dignidad: str
    organizacion_politica: str
    jurisdiccion_dpa: str
    periodo: str
    estado_plan: str


class DeclaracionInfo(BaseModel):
    id: int
    texto: str


class EvidenciaItem(BaseModel):
    paso: PasoEvidencia
    texto: str
    score: float
    doc_id: str
    git_sha: str
    point_id: str
    fuente_url: str | None = None


class EvidenciaEventData(BaseModel):
    candidatura: CandidaturaInfo | None
    declaracion: DeclaracionInfo
    evidencias: list[EvidenciaItem]


class RechazoEventData(BaseModel):
    motivo: str


class ErrorEventData(BaseModel):
    detalle: str


class EstadoResponse(BaseModel):
    modo_silencio_electoral: bool


class TranscripcionResponse(BaseModel):
    audio_id: str
    texto: str
    duracion_s: float
    costo_estimado_usd: float
