"""Panel de admin: subir un PDF, convertirlo a Markdown (Docling),
revisar/editar, commitear a lodicho-corpus, e ingestar a Qdrant — todo
desde el front, sin que el revisor toque una terminal ni git.

Auth con una sola clave compartida (services/admin_auth.py) — no hay
tabla de usuarios todavia (ver README/decisiones del panel de admin).

El estado de los borradores vive en memoria de proceso: se pierde si el
contenedor reinicia, y no funciona corriendo mas de un worker de
uvicorn. Aceptable para el volumen de este piloto; si hace falta mas
adelante, pasa a una tabla en Postgres.
"""
from __future__ import annotations

import asyncio
import hashlib
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.db.models import Candidato, Candidatura, Documento
from app.db.models.enums import EstadoPlanCandidatura
from app.db.session import get_session
from app.services import admin_auth, corpus_git, corpus_validation, pdf_conversion
from app.services.ingest import (
    ALIAS_POR_TIPO,
    DIR_POR_TIPO,
    IngestaError,
    ingestar_archivo,
    parsear_frontmatter,
)
from app.services.qdrant_client import delete_by_doc_id

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


# ---------- auth ----------


class LoginRequest(BaseModel):
    password: str


class LoginResponse(BaseModel):
    token: str


@router.post("/login", response_model=LoginResponse)
def login(datos: LoginRequest) -> LoginResponse:
    if not admin_auth.verificar_password(datos.password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Contraseña incorrecta")
    return LoginResponse(token=admin_auth.crear_sesion())


def requiere_admin(authorization: str | None = Header(default=None)) -> None:
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization[len("Bearer ") :]
    if not admin_auth.verificar_sesion(token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sesión inválida o expirada")


# ---------- borradores (en memoria de proceso) ----------


@dataclass
class Borrador:
    markdown: str
    meta: dict[str, Any]
    pdf_temp_path: Path | None = None
    pdf_sha256: str | None = None
    texto_pdf: str | None = ""
    origen: str = "docling"
    # Solo se llenan al editar un documento YA ingestado (ver /documentos/{doc_id}/editar).
    # Si tipo o doc_id cambian respecto a estos valores, confirmar_borrador sabe que
    # tiene que borrar el archivo y los puntos de Qdrant viejos, no solo escribir los nuevos.
    doc_id_original: str | None = None
    tipo_original: str | None = None


_borradores: dict[str, Borrador] = {}
_TMP_DIR = Path(tempfile.gettempdir()) / "lodicho-borradores"
_TMP_DIR.mkdir(parents=True, exist_ok=True)


def _obtener_borrador(borrador_id: str) -> Borrador:
    borrador = _borradores.get(borrador_id)
    if borrador is None:
        raise HTTPException(status_code=404, detail="Borrador no encontrado (¿expiró o ya se confirmó?)")
    return borrador


def _extraer_texto_plano(pdf_path: Path) -> str:
    try:
        from pypdf import PdfReader

        return "".join(pagina.extract_text() or "" for pagina in PdfReader(str(pdf_path)).pages)
    except Exception:
        return ""


def _meta_con_campos_automaticos(b: Borrador) -> dict[str, Any]:
    """Campos que nunca se confian al cliente: son la garantia de que
    pdf_sha256 y la fecha de revision reflejan lo que paso de verdad, no
    lo que alguien haya podido mandar en el request. Un solo lugar para
    calcularlos, para que /validar (vista previa) y /confirmar (real)
    nunca puedan desincronizarse sobre que cuenta como valido."""
    return {
        **b.meta,
        "pdf_sha256": b.pdf_sha256,
        "convertido_con": b.origen,
        "revisado_en": date.today().isoformat(),
    }


class ConvertirResponse(BaseModel):
    borrador_id: str
    markdown: str
    pdf_sha256: str | None
    tipo: str


class IniciarConversionResponse(BaseModel):
    job_id: str


class EstadoConversionResponse(BaseModel):
    estado: str  # "procesando" | "listo" | "error"
    resultado: ConvertirResponse | None = None
    detalle: str | None = None


# Docling puede tardar varios minutos en un PDF real, y Cloudflare corta
# cualquier peticion que no responda en ~100s (524). Por eso la conversion
# no se espera dentro del request: POST /convertir devuelve un job_id al
# instante y el front consulta GET /convertir/{job_id} hasta que termine.
# Mismo criterio que los borradores: estado en memoria de proceso.
_jobs_conversion: dict[str, dict[str, Any]] = {}
_tareas_conversion: set[asyncio.Task] = set()  # referencia fuerte: asyncio no retiene tareas sueltas
# Una conversion a la vez: Docling satura varios nucleos y el VPS es compartido.
_cupo_conversion = asyncio.Semaphore(1)


def _respuesta_de_borrador(borrador_id: str) -> ConvertirResponse:
    b = _obtener_borrador(borrador_id)
    return ConvertirResponse(
        borrador_id=borrador_id, markdown=b.markdown, pdf_sha256=b.pdf_sha256, tipo=b.meta["tipo"]
    )


async def _ejecutar_conversion(job_id: str, tipo: str, pdf_path: Path, pdf_sha256: str) -> None:
    try:
        async with _cupo_conversion:
            # asyncio.to_thread saca a Docling del event loop: sincrono dentro
            # de una ruta async bloquearia el unico loop del proceso (ni
            # siquiera /health responderia mientras tanto).
            markdown = await asyncio.to_thread(pdf_conversion.convertir_pdf_a_markdown, pdf_path)
        texto_pdf = await asyncio.to_thread(_extraer_texto_plano, pdf_path)
    except Exception as exc:  # noqa: BLE001 — cualquier fallo se reporta al front por el job
        pdf_path.unlink(missing_ok=True)
        _jobs_conversion[job_id] = {"estado": "error", "detalle": f"No se pudo convertir el PDF: {exc}"}
        return

    borrador_id = str(uuid.uuid4())
    _borradores[borrador_id] = Borrador(
        markdown=markdown,
        meta={"tipo": tipo, "vigente": True},
        pdf_temp_path=pdf_path,
        pdf_sha256=pdf_sha256,
        texto_pdf=texto_pdf,
    )
    _jobs_conversion[job_id] = {"estado": "listo", "borrador_id": borrador_id}


@router.post(
    "/documentos/convertir",
    response_model=IniciarConversionResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(requiere_admin)],
)
async def convertir(tipo: str = Form(...), pdf: UploadFile = File(...)) -> IniciarConversionResponse:
    if tipo not in DIR_POR_TIPO:
        raise HTTPException(status_code=422, detail=f"tipo debe ser uno de {list(DIR_POR_TIPO)}")

    job_id = str(uuid.uuid4())
    pdf_path = _TMP_DIR / f"{job_id}.pdf"
    with pdf_path.open("wb") as destino:
        shutil.copyfileobj(pdf.file, destino)

    pdf_sha256 = pdf_conversion.calcular_sha256(pdf_path)

    _jobs_conversion[job_id] = {"estado": "procesando"}
    tarea = asyncio.create_task(_ejecutar_conversion(job_id, tipo, pdf_path, pdf_sha256))
    _tareas_conversion.add(tarea)
    tarea.add_done_callback(_tareas_conversion.discard)

    return IniciarConversionResponse(job_id=job_id)


@router.get(
    "/documentos/convertir/{job_id}",
    response_model=EstadoConversionResponse,
    dependencies=[Depends(requiere_admin)],
)
async def estado_conversion(job_id: str) -> EstadoConversionResponse:
    job = _jobs_conversion.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Conversión no encontrada (¿el servidor se reinició?)")
    if job["estado"] == "listo":
        return EstadoConversionResponse(estado="listo", resultado=_respuesta_de_borrador(job["borrador_id"]))
    if job["estado"] == "error":
        return EstadoConversionResponse(estado="error", detalle=job["detalle"])
    return EstadoConversionResponse(estado="procesando")


@router.post(
    "/documentos/importar-markdown", response_model=ConvertirResponse, dependencies=[Depends(requiere_admin)]
)
async def importar_markdown(tipo: str = Form(...), markdown_file: UploadFile = File(...)) -> ConvertirResponse:
    if tipo not in DIR_POR_TIPO:
        raise HTTPException(status_code=422, detail=f"tipo debe ser uno de {list(DIR_POR_TIPO)}")

    contenido = await markdown_file.read()
    try:
        markdown = contenido.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=422, detail="El archivo no es UTF-8 válido.") from exc

    borrador_id = str(uuid.uuid4())
    # No hay PDF de origen: usamos el sha256 del propio markdown como
    # checksum de integridad, para no dejar vacio 'pdf_sha256' (campo
    # obligatorio en la validacion del corpus para cualquier tipo).
    checksum = hashlib.sha256(contenido).hexdigest()

    _borradores[borrador_id] = Borrador(
        markdown=markdown,
        meta={"tipo": tipo, "vigente": True},
        pdf_temp_path=None,
        pdf_sha256=checksum,
        texto_pdf=None,  # sin PDF: se salta la validacion de ratio markdown/pdf
        origen="manual",
    )

    return ConvertirResponse(borrador_id=borrador_id, markdown=markdown, pdf_sha256=checksum, tipo=tipo)

class EditarDocumentoResponse(BaseModel):
    borrador_id: str
    markdown: str
    tipo: str
    meta: dict[str, Any]


@router.post(
    "/documentos/{doc_id}/editar", response_model=EditarDocumentoResponse, dependencies=[Depends(requiere_admin)]
)
def editar_documento(doc_id: str) -> EditarDocumentoResponse:
    """Abre un documento YA commiteado al corpus como borrador editable —
    mismo formulario que un documento nuevo, pre-llenado. Existe porque el
    tipo (marco_legal / plan_trabajo / contexto) se elige una sola vez al
    subir y, si alguien se equivoca ahi, hasta ahora no habia forma de
    corregirlo sin tocar git a mano."""
    corpus_path = Path(settings.corpus_path)
    archivo = None
    tipo_actual = None
    for tipo, carpeta in DIR_POR_TIPO.items():
        candidato = corpus_path / carpeta / f"{doc_id}.md"
        if candidato.exists():
            archivo, tipo_actual = candidato, tipo
            break

    if archivo is None:
        raise HTTPException(status_code=404, detail=f"No se encontró {doc_id}.md en el corpus")

    try:
        meta, body = parsear_frontmatter(archivo.read_text(encoding="utf-8"))
    except IngestaError as exc:
        raise HTTPException(status_code=422, detail=f"No se pudo leer el documento existente: {exc}") from exc

    borrador_id = str(uuid.uuid4())
    _borradores[borrador_id] = Borrador(
        markdown=body.strip(),
        meta=meta,
        pdf_temp_path=None,  # el PDF ya vive en el corpus; editar texto/tipo no lo toca
        pdf_sha256=meta.get("pdf_sha256"),
        texto_pdf=None,  # ya paso la validacion de ratio md/pdf cuando se ingesto la primera vez
        origen=meta.get("convertido_con", "editado"),
        doc_id_original=doc_id,
        tipo_original=tipo_actual,
    )

    return EditarDocumentoResponse(borrador_id=borrador_id, markdown=body.strip(), tipo=tipo_actual, meta=meta)


class BorradorResponse(BaseModel):
    markdown: str
    meta: dict[str, Any]


@router.get(
    "/documentos/borradores/{borrador_id}", response_model=BorradorResponse, dependencies=[Depends(requiere_admin)]
)
def obtener_borrador(borrador_id: str) -> BorradorResponse:
    b = _obtener_borrador(borrador_id)
    return BorradorResponse(markdown=b.markdown, meta=b.meta)


class ActualizarBorradorRequest(BaseModel):
    markdown: str
    meta: dict[str, Any]


@router.put(
    "/documentos/borradores/{borrador_id}", response_model=BorradorResponse, dependencies=[Depends(requiere_admin)]
)
def actualizar_borrador(borrador_id: str, datos: ActualizarBorradorRequest) -> BorradorResponse:
    b = _obtener_borrador(borrador_id)
    b.markdown = datos.markdown
    b.meta = {**b.meta, **datos.meta}
    return BorradorResponse(markdown=b.markdown, meta=b.meta)


@router.delete("/documentos/borradores/{borrador_id}", dependencies=[Depends(requiere_admin)])
def descartar_borrador(borrador_id: str) -> dict[str, bool]:
    b = _borradores.pop(borrador_id, None)
    if b and b.pdf_temp_path:
        b.pdf_temp_path.unlink(missing_ok=True)
    return {"ok": True}


class ValidarResponse(BaseModel):
    ok: bool
    errores: list[str]
    warnings: list[str]


@router.post(
    "/documentos/borradores/{borrador_id}/validar",
    response_model=ValidarResponse,
    dependencies=[Depends(requiere_admin)],
)
def validar_borrador(borrador_id: str) -> ValidarResponse:
    b = _obtener_borrador(borrador_id)
    resultado = corpus_validation.validar(_meta_con_campos_automaticos(b), b.markdown, b.texto_pdf)
    return ValidarResponse(ok=resultado.ok, errores=resultado.errores, warnings=resultado.warnings)


class ConfirmarResponse(BaseModel):
    doc_id: str
    git_sha: str
    ruta_md: str


@router.post(
    "/documentos/borradores/{borrador_id}/confirmar",
    response_model=ConfirmarResponse,
    dependencies=[Depends(requiere_admin)],
)
def confirmar_borrador(borrador_id: str, session: Session = Depends(get_session)) -> ConfirmarResponse:
    b = _obtener_borrador(borrador_id)
    meta = _meta_con_campos_automaticos(b)

    resultado = corpus_validation.validar(meta, b.markdown, b.texto_pdf)
    if not resultado.ok:
        raise HTTPException(status_code=422, detail={"errores": resultado.errores, "warnings": resultado.warnings})

    doc_id = meta["doc_id"]
    tipo = meta["tipo"]
    corpus_path = Path(settings.corpus_path)
    carpeta = DIR_POR_TIPO[tipo]

    ruta_md = corpus_path / carpeta / f"{doc_id}.md"
    ruta_md.parent.mkdir(parents=True, exist_ok=True)

    frontmatter = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False)
    ruta_md.write_text(f"---\n{frontmatter}---\n\n{b.markdown.strip()}\n", encoding="utf-8")

    rutas_relativas = [str(ruta_md.relative_to(corpus_path))]

    if b.pdf_temp_path and b.pdf_temp_path.exists():
        ruta_pdf = corpus_path / "pdfs" / carpeta / f"{doc_id}.pdf"
        ruta_pdf.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(b.pdf_temp_path, ruta_pdf)
        rutas_relativas.append(str(ruta_pdf.relative_to(corpus_path)))

    # Editando un documento existente y cambio tipo y/o doc_id (p.ej. estaba
    # mal cargado como marco_legal y en realidad es plan_trabajo): el
    # archivo viejo, sus puntos en la coleccion vieja de Qdrant, y la fila
    # vieja en Postgres quedarian huerfanos si no se limpian aca mismo.
    es_movimiento = bool(b.doc_id_original) and (b.doc_id_original != doc_id or b.tipo_original != tipo)
    if es_movimiento:
        ruta_md_anterior = corpus_path / DIR_POR_TIPO[b.tipo_original] / f"{b.doc_id_original}.md"
        if ruta_md_anterior.exists() and ruta_md_anterior != ruta_md:
            ruta_md_anterior.unlink()
            rutas_relativas.append(str(ruta_md_anterior.relative_to(corpus_path)))

        ruta_pdf_anterior = corpus_path / "pdfs" / DIR_POR_TIPO[b.tipo_original] / f"{b.doc_id_original}.pdf"
        if ruta_pdf_anterior.exists():
            ruta_pdf_anterior.unlink()
            rutas_relativas.append(str(ruta_pdf_anterior.relative_to(corpus_path)))

    mensaje_commit = (
        f"Corrige {b.doc_id_original} -> {doc_id} (panel de admin)"
        if es_movimiento
        else f"Agrega {doc_id} (panel de admin)"
    )

    try:
        git_sha = corpus_git.commitear_y_pushear(corpus_path, rutas_relativas, mensaje_commit)
    except corpus_git.GitCorpusError as exc:
        # No 502: Cloudflare reemplaza el cuerpo de un 502 por su propia pagina
        # HTML y el panel nunca vería el detalle del error de git.
        raise HTTPException(status_code=424, detail=str(exc)) from exc

    if es_movimiento:
        delete_by_doc_id(ALIAS_POR_TIPO[b.tipo_original], b.doc_id_original)
        if b.doc_id_original != doc_id:
            documento_anterior = session.execute(
                select(Documento).where(Documento.doc_id == b.doc_id_original)
            ).scalar_one_or_none()
            if documento_anterior is not None:
                session.delete(documento_anterior)
                session.commit()

    if b.pdf_temp_path:
        b.pdf_temp_path.unlink(missing_ok=True)
    _borradores.pop(borrador_id, None)

    return ConfirmarResponse(doc_id=doc_id, git_sha=git_sha, ruta_md=rutas_relativas[0])


class IngestarResponse(BaseModel):
    doc_id: str
    tipo: str
    git_sha: str
    n_chunks: int


@router.post(
    "/documentos/{doc_id}/ingestar", response_model=IngestarResponse, dependencies=[Depends(requiere_admin)]
)
def ingestar_documento(doc_id: str, session: Session = Depends(get_session)) -> IngestarResponse:
    corpus_path = Path(settings.corpus_path)
    archivo = None
    for carpeta in DIR_POR_TIPO.values():
        candidato = corpus_path / carpeta / f"{doc_id}.md"
        if candidato.exists():
            archivo = candidato
            break

    if archivo is None:
        raise HTTPException(status_code=404, detail=f"No se encontró {doc_id}.md en el corpus")

    try:
        resumen = ingestar_archivo(archivo, corpus_path, session)
    except IngestaError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return IngestarResponse(
        doc_id=resumen.doc_id, tipo=resumen.tipo, git_sha=resumen.git_sha, n_chunks=resumen.n_chunks
    )


class DocumentoResumen(BaseModel):
    doc_id: str
    tipo: str
    candidatura_id: int | None
    n_chunks: int
    indexado_en: str | None
    estado: str
    git_sha: str
    ruta_repo: str


class ListaDocumentosResponse(BaseModel):
    documentos: list[DocumentoResumen]


@router.get("/documentos", response_model=ListaDocumentosResponse, dependencies=[Depends(requiere_admin)])
def listar_documentos(session: Session = Depends(get_session)) -> ListaDocumentosResponse:
    filas = session.execute(select(Documento).order_by(Documento.id.desc())).scalars().all()
    return ListaDocumentosResponse(
        documentos=[
            DocumentoResumen(
                doc_id=d.doc_id,
                tipo=d.tipo.value,
                candidatura_id=d.candidatura_id,
                n_chunks=d.n_chunks,
                indexado_en=d.indexado_en.isoformat() if d.indexado_en else None,
                estado=d.estado.value,
                git_sha=d.git_sha,
                ruta_repo=d.ruta_repo,
            )
            for d in filas
        ]
    )


class CandidatoResumen(BaseModel):
    id: int
    nombre: str
    posicion_lista: int
    candidatura_id: int


def _resumen_candidato(c: Candidato) -> CandidatoResumen:
    return CandidatoResumen(
        id=c.id, nombre=c.nombre, posicion_lista=c.posicion_lista, candidatura_id=c.candidatura_id
    )


class CandidaturaResumen(BaseModel):
    id: int
    organizacion_politica: str
    lista_numero: str
    dignidad: str
    jurisdiccion_dpa: str
    periodo: str
    estado_plan: str
    doc_id_plan: str | None
    candidatos: list[CandidatoResumen] = []


def _resumen_candidatura(c: Candidatura) -> CandidaturaResumen:
    return CandidaturaResumen(
        id=c.id,
        organizacion_politica=c.organizacion_politica,
        lista_numero=c.lista_numero,
        dignidad=c.dignidad,
        jurisdiccion_dpa=c.jurisdiccion_dpa,
        periodo=c.periodo,
        estado_plan=c.estado_plan.value,
        doc_id_plan=c.doc_id_plan,
        candidatos=[_resumen_candidato(cand) for cand in sorted(c.candidatos, key=lambda x: x.posicion_lista)],
    )


@router.get("/candidaturas", response_model=list[CandidaturaResumen], dependencies=[Depends(requiere_admin)])
def listar_candidaturas(session: Session = Depends(get_session)) -> list[CandidaturaResumen]:
    filas = session.execute(select(Candidatura).order_by(Candidatura.id.desc())).scalars().all()
    return [_resumen_candidatura(c) for c in filas]


class CandidaturaCrear(BaseModel):
    organizacion_politica: str
    lista_numero: str
    dignidad: str
    jurisdiccion_dpa: str
    periodo: str
    estado_plan: str
    doc_id_plan: str | None = None


@router.post("/candidaturas", response_model=CandidaturaResumen, dependencies=[Depends(requiere_admin)])
def crear_candidatura(datos: CandidaturaCrear, session: Session = Depends(get_session)) -> CandidaturaResumen:
    valores_validos = {e.value for e in EstadoPlanCandidatura}
    if datos.estado_plan not in valores_validos:
        raise HTTPException(status_code=422, detail=f"estado_plan debe ser uno de {sorted(valores_validos)}")

    campos_vacios = [
        campo
        for campo in ("organizacion_politica", "lista_numero", "dignidad", "jurisdiccion_dpa", "periodo")
        if not getattr(datos, campo).strip()
    ]
    if campos_vacios:
        raise HTTPException(status_code=422, detail=f"Faltan campos: {', '.join(campos_vacios)}")

    candidatura = Candidatura(
        organizacion_politica=datos.organizacion_politica.strip(),
        lista_numero=datos.lista_numero.strip(),
        dignidad=datos.dignidad.strip(),
        jurisdiccion_dpa=datos.jurisdiccion_dpa.strip(),
        periodo=datos.periodo.strip(),
        estado_plan=EstadoPlanCandidatura(datos.estado_plan),
        doc_id_plan=(datos.doc_id_plan or "").strip() or None,
    )
    session.add(candidatura)
    session.commit()
    session.refresh(candidatura)
    return _resumen_candidatura(candidatura)


class CandidatoCrear(BaseModel):
    nombre: str
    posicion_lista: int


@router.post(
    "/candidaturas/{candidatura_id}/candidatos",
    response_model=CandidatoResumen,
    dependencies=[Depends(requiere_admin)],
)
def crear_candidato(
    candidatura_id: int, datos: CandidatoCrear, session: Session = Depends(get_session)
) -> CandidatoResumen:
    candidatura = session.get(Candidatura, candidatura_id)
    if candidatura is None:
        raise HTTPException(status_code=404, detail="No existe esa candidatura")

    nombre = datos.nombre.strip()
    if not nombre:
        raise HTTPException(status_code=422, detail="Falta el nombre del candidato")
    if datos.posicion_lista < 1:
        raise HTTPException(status_code=422, detail="posicion_lista debe ser un numero positivo")

    candidato = Candidato(nombre=nombre, posicion_lista=datos.posicion_lista, candidatura_id=candidatura.id)
    session.add(candidato)
    session.commit()
    session.refresh(candidato)
    return _resumen_candidato(candidato)
