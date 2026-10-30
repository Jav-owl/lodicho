"""Valida un borrador (frontmatter + cuerpo) antes de escribirlo al
corpus y commitear, para el flujo del panel de admin.

Es una version de las mismas reglas que corren en
lodicho-corpus/scripts/validar_frontmatter.py (CI del repo del corpus),
adaptada para operar sobre contenido en memoria en vez de archivos en
disco — el admin puede estar editando algo que todavia no se escribio a
ningun lado. Se duplica a proposito: CLAUDE.md mantiene lodicho y
lodicho-corpus sin dependencias cruzadas entre repos.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.services.ingest import (
    ARTICULO_RE,
    LIMITE_TOKENS_POR_CHUNK,
    NIVEL_TODOS,
    NIVELES_GOBIERNO,
    _articulos,
    _chunkear,
    _subdividir_articulo,
)

TIPOS_VALIDOS = {"marco_legal", "plan_trabajo", "contexto"}

CAMPOS_COMUNES = [
    "doc_id",
    "tipo",
    "fuente_url",
    "pdf_sha256",
    "convertido_con",
    "revisado_por",
    "revisado_en",
    "vigente",
]
CAMPOS_PLAN_TRABAJO = ["candidatura_id", "dignidad", "organizacion", "lista_numero", "periodo", "jurisdiccion_dpa"]
CAMPOS_MARCO_LEGAL = ["nivel_gobierno"]
NIVELES_VALIDOS = [*NIVELES_GOBIERNO, NIVEL_TODOS]
CAMPOS_CONTEXTO = ["jurisdiccion_dpa"]

RATIO_MD_PDF_MINIMO = 0.85
MAX_REPETICIONES_LINEA = 5
CHARS_POR_TOKEN_APROX = 4

DOC_ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
PDF_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
FECHA_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass
class ResultadoValidacion:
    errores: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errores


def validar(meta: dict, body: str, texto_pdf: str | None = None) -> ResultadoValidacion:
    r = ResultadoValidacion()
    _validar_frontmatter_completo(meta, r)

    tipo = meta.get("tipo")
    if tipo not in TIPOS_VALIDOS:
        return r  # sin tipo valido no tiene sentido correr el resto

    _validar_secuencia_articulos(body, tipo, r)
    _validar_encabezados_repetidos(body, r)
    _validar_tildes(body, r)
    _validar_umbral_tokens(body, tipo, r)
    if texto_pdf is not None:
        _validar_ratio_pdf(body, texto_pdf, r)

    return r


def _validar_frontmatter_completo(meta: dict, r: ResultadoValidacion) -> None:
    tipo = meta.get("tipo")
    campos = list(CAMPOS_COMUNES)
    if tipo == "plan_trabajo":
        campos += CAMPOS_PLAN_TRABAJO
    elif tipo == "marco_legal":
        campos += CAMPOS_MARCO_LEGAL
    elif tipo == "contexto":
        campos += CAMPOS_CONTEXTO

    for campo in campos:
        if campo not in meta or meta[campo] in (None, ""):
            r.errores.append(f"Falta el campo obligatorio '{campo}'.")

    if tipo is not None and tipo not in TIPOS_VALIDOS:
        r.errores.append(f"'tipo: {tipo}' no es valido. Debe ser uno de: {', '.join(sorted(TIPOS_VALIDOS))}.")

    nivel = meta.get("nivel_gobierno")
    if tipo == "marco_legal" and nivel not in (None, "") and nivel not in NIVELES_VALIDOS:
        r.errores.append(
            f"'nivel_gobierno: {nivel}' no es valido: debe ser uno de {', '.join(NIVELES_VALIDOS)} "
            "(si no coincide exacto, ninguna consulta recupera este documento)."
        )

    doc_id = meta.get("doc_id")
    if doc_id is not None:
        if not isinstance(doc_id, str) or not DOC_ID_RE.match(doc_id):
            r.errores.append(
                f"'doc_id: {doc_id}' debe ser minusculas/numeros separados por guiones (ej. plan-bolivar-simiatug-junta-18-2027)."
            )

    for campo in ("jurisdiccion_dpa", "lista_numero", "periodo", "pdf_sha256"):
        if campo in meta and meta[campo] is not None and not isinstance(meta[campo], str):
            r.errores.append(f"'{campo}' debe ser texto, no {type(meta[campo]).__name__}: {meta[campo]!r}.")

    if "candidatura_id" in meta and meta["candidatura_id"] is not None and not isinstance(meta["candidatura_id"], int):
        r.errores.append(f"'candidatura_id' debe ser un numero entero, no {type(meta['candidatura_id']).__name__}.")

    if "vigente" in meta and not isinstance(meta.get("vigente"), bool):
        r.errores.append("'vigente' debe ser true/false (booleano).")

    pdf_sha256 = meta.get("pdf_sha256")
    if isinstance(pdf_sha256, str) and not PDF_SHA256_RE.match(pdf_sha256):
        r.errores.append("'pdf_sha256' no parece un sha256 valido (64 caracteres hexadecimales en minuscula).")

    revisado_en = meta.get("revisado_en")
    if revisado_en is not None and not FECHA_RE.match(str(revisado_en)):
        r.errores.append("'revisado_en' debe tener formato YYYY-MM-DD.")

    if not meta.get("revisado_por"):
        r.errores.append("Falta 'revisado_por': ningun documento entra al corpus sin revision humana.")


def _validar_secuencia_articulos(body: str, tipo: str, r: ResultadoValidacion) -> None:
    if tipo != "marco_legal":
        return
    numeros = [int(n) for n in ARTICULO_RE.findall(body)]
    if not numeros:
        r.warnings.append("No se encontraron encabezados 'Art. N' — revisa que el chunker por articulo pueda procesarlo.")
        return
    for anterior, actual in zip(numeros, numeros[1:]):
        if actual != anterior + 1:
            r.errores.append(f"Salto en la secuencia de articulos: Art. {anterior} seguido de Art. {actual}.")


def _validar_encabezados_repetidos(body: str, r: ResultadoValidacion) -> None:
    conteo: dict[str, int] = {}
    for linea in body.splitlines():
        limpia = linea.strip()
        if not limpia or len(limpia) > 200:
            continue
        conteo[limpia] = conteo.get(limpia, 0) + 1
    for linea, veces in conteo.items():
        if veces > MAX_REPETICIONES_LINEA:
            r.errores.append(
                f"La linea {linea!r} se repite {veces} veces — probable encabezado/pie de pagina pegado al convertir el PDF."
            )


def _validar_tildes(body: str, r: ResultadoValidacion) -> None:
    letras = [c for c in body if c.isalpha()]
    if len(letras) < 1500:
        return
    acentuadas = sum(1 for c in letras if c.lower() in "áéíóúñ")
    proporcion = acentuadas / len(letras)
    if proporcion == 0:
        r.errores.append("No hay ninguna tilde ni 'ñ' en un documento largo en español — probable corrupcion de encoding.")
    elif proporcion < 0.003:
        r.warnings.append(f"Proporcion de tildes/'ñ' inusualmente baja ({proporcion:.4%}) — revisa el encoding a mano.")


def _validar_umbral_tokens(body: str, tipo: str, r: ResultadoValidacion) -> None:
    # El chunker ya subdivide los articulos largos; aca solo queda error si
    # algo sigue sobre el limite despues de eso (p. ej. una oracion sin puntos).
    for chunk in _chunkear(body, tipo):
        tokens_aprox = len(chunk) / CHARS_POR_TOKEN_APROX
        if tokens_aprox > LIMITE_TOKENS_POR_CHUNK:
            inicio = chunk.strip().splitlines()[0][:60] if chunk.strip() else "(vacio)"
            r.errores.append(f"Un chunk (~{int(tokens_aprox)} tokens, empieza con {inicio!r}...) supera el umbral de {LIMITE_TOKENS_POR_CHUNK}.")

    if tipo == "marco_legal":
        for articulo in _articulos(body):
            partes = _subdividir_articulo(articulo)
            if len(partes) > 1:
                inicio = articulo.strip().splitlines()[0][:60]
                r.warnings.append(
                    f"El articulo que empieza con {inicio!r} (~{int(len(articulo) / CHARS_POR_TOKEN_APROX)} tokens) "
                    f"se subdivide en {len(partes)} chunks al ingestar."
                )


def _validar_ratio_pdf(body: str, texto_pdf: str, r: ResultadoValidacion) -> None:
    if not texto_pdf.strip():
        r.warnings.append("El PDF no devolvio texto extraible (¿es un escaneado sin OCR?).")
        return
    ratio = len(body.strip()) / len(texto_pdf.strip())
    if ratio <= RATIO_MD_PDF_MINIMO:
        r.errores.append(
            f"Ratio caracteres MD/PDF = {ratio:.2f}, por debajo del minimo {RATIO_MD_PDF_MINIMO} "
            "(la conversion pudo haber perdido contenido — revisa el markdown contra el PDF)."
        )
