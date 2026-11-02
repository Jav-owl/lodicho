"""`POST /api/v1/transcribir`: audio -> texto, para que el usuario lo revise y
lo envie a `/consulta` como texto. Guarda el audio original (evidencia ante
impugnacion) y devuelve su `audio_id` para enlazarlo a la consulta."""
from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.config.settings import settings
from app.schemas.consulta import TranscripcionResponse
from app.services import transcripcion as svc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["transcripcion"])

EXTENSIONES = {"audio/webm": ".webm", "audio/ogg": ".ogg", "audio/mp4": ".m4a",
               "audio/x-m4a": ".m4a", "audio/mpeg": ".mp3", "audio/wav": ".wav",
               "audio/x-wav": ".wav", "audio/aac": ".aac"}


def ruta_audio(audio_id: str) -> Path | None:
    """Archivo guardado para un `audio_id` (uuid hex), o None si no existe."""
    if len(audio_id) != 32 or any(c not in "0123456789abcdef" for c in audio_id):
        return None
    return next(Path(settings.audio_path).glob(f"{audio_id}.*"), None)


def _procesar(contenido: bytes, extension: str) -> TranscripcionResponse:
    carpeta = Path(settings.audio_path)
    carpeta.mkdir(parents=True, exist_ok=True)
    audio_id = uuid.uuid4().hex
    ruta = carpeta / f"{audio_id}{extension}"
    ruta.write_bytes(contenido)
    try:
        duracion = svc.medir_duracion_s(ruta)
        svc.validar_duracion(duracion)
        texto = svc.transcribir(ruta)
    except Exception:
        ruta.unlink(missing_ok=True)  # no se archiva lo que no se proceso
        raise
    costo = svc.costo_estimado_usd(duracion)
    logger.info("transcripcion %s: %.1f s, costo estimado US$%.5f", audio_id, duracion, costo)
    return TranscripcionResponse(
        audio_id=audio_id, texto=texto, duracion_s=round(duracion, 1), costo_estimado_usd=costo
    )


@router.post("/transcribir", response_model=TranscripcionResponse)
async def transcribir_audio(audio: UploadFile = File(...)) -> TranscripcionResponse:
    if settings.modo_silencio_electoral:
        raise HTTPException(403, "Silencio electoral activo: la generacion esta desactivada.")
    contenido = await audio.read(settings.audio_max_bytes + 1)
    if not contenido:
        raise HTTPException(400, "El archivo de audio esta vacio.")
    if len(contenido) > settings.audio_max_bytes:
        raise HTTPException(413, f"El audio supera el limite de {settings.audio_max_bytes // 1024 // 1024} MB.")
    tipo = (audio.content_type or "").split(";")[0].strip().lower()
    try:
        return await asyncio.to_thread(_procesar, contenido, EXTENSIONES.get(tipo, ".webm"))
    except svc.AudioInvalidoError as exc:
        raise HTTPException(422, str(exc)) from exc
    except svc.TranscripcionError:
        logger.exception("fallo al transcribir")
        raise HTTPException(503, "No se pudo transcribir el audio. Escribe la afirmacion o intenta de nuevo.")
