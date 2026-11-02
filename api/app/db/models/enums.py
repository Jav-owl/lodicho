import enum


class TipoDocumento(str, enum.Enum):
    marco_legal = "marco_legal"
    plan_trabajo = "plan_trabajo"
    contexto = "contexto"


class EstadoDocumento(str, enum.Enum):
    activo = "activo"
    eliminado = "eliminado"


class EstadoPlanCandidatura(str, enum.Enum):
    """Hecho persistente: si la candidatura registro plan ante el CNE.

    Deliberadamente separado de cualquier resultado de un analisis: no debe
    confundirse con sin_plan_recuperado (fallo tecnico de retrieval, no
    persistido) ni con no_consta (Veredicto.no_consta_en_plan, persistido
    por analisis). Ver CLAUDE.md, seccion "Tres ausencias distintas".
    """

    registrado = "registrado"
    sin_plan_registrado = "sin_plan_registrado"


class EstadoCandidatura(str, enum.Enum):
    """Etapa de la candidatura ante el CNE. Una carga nueva de la lista la va
    actualizando (inscrito -> oficializado). Distinto de `EstadoPlanCandidatura`
    (si registro plan) y de `EstadoAnalisis` (flujo editorial de un veredicto)."""

    inscrito = "inscrito"
    oficializado = "oficializado"


class TipoInput(str, enum.Enum):
    voz = "voz"
    texto = "texto"
    url = "url"


class TipoDeclaracion(str, enum.Enum):
    cita_directa = "cita_directa"
    parafrasis_periodistica = "parafrasis_periodistica"
    dictado_usuario = "dictado_usuario"


class Veredicto(str, enum.Enum):
    viable_y_en_plan = "viable_y_en_plan"
    fuera_de_competencia = "fuera_de_competencia"
    no_consta_en_plan = "no_consta_en_plan"
    informacion_enganosa = "informacion_enganosa"
    informacion_falsa = "informacion_falsa"
    incomprobable = "incomprobable"


class EstadoAnalisis(str, enum.Enum):
    borrador = "borrador"
    en_revision = "en_revision"
    publicado = "publicado"
    descartado = "descartado"


class PasoEvidencia(str, enum.Enum):
    planes_trabajo = "planes_trabajo"
    marco_legal = "marco_legal"
    indicadores = "indicadores"
    analisis_publicados = "analisis_publicados"
