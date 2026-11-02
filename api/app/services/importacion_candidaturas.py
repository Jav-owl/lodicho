"""Carga masiva de candidaturas desde un CSV (una fila por candidato).

Es una carga de LISTA, no de corpus: no toca Qdrant ni `documentos`.

Idempotente: una candidatura se identifica por (dignidad, jurisdiccion_dpa,
lista_numero, periodo) y un candidato por su nombre normalizado dentro de ella.
Volver a cargar el archivo (o uno actualizado) no duplica nada: actualiza
`estado_candidatura` (inscrito -> oficializado), `estado_plan`, organizacion y
posicion. Los candidatos que ya existen y no vienen en el archivo se conservan
(no se borra nada). Todo o nada: con un solo error no se escribe nada.

`procesar(..., aplicar=False)` es la vista previa: calcula el mismo reporte sin
escribir.
"""
from __future__ import annotations

import csv
import io
import re
import unicodedata
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.models import Candidato, Candidatura
from app.db.models.enums import EstadoCandidatura, EstadoPlanCandidatura
from app.services.resolucion_candidatura import DIGNIDAD_A_NIVEL_GOBIERNO

MAX_BYTES = 5 * 1024 * 1024
MAX_FILAS = 50_000
MAX_ERRORES_DEVUELTOS = 200

COLUMNAS = (
    "organizacion_politica", "lista_numero", "dignidad", "jurisdiccion_dpa", "periodo",
    "estado_candidatura", "estado_plan", "nombre", "posicion_lista",
)
# Columnas que puede omitir el archivo (el del CNE no las trae):
# - estado_plan: si falta, una candidatura NUEVA se crea como `registrado` y se avisa; una
#   existente conserva el suyo. `registrado` es el error menos danino: si no hay plan, la
#   recuperacion devuelve vacio (sin_plan_recuperado) en vez de afirmar "no registro plan".
# - posicion_lista: si falta, se asigna por orden de aparicion dentro de la lista.
OPCIONALES = ("estado_plan", "posicion_lista")

# Encabezados del archivo oficial -> nombre canonico (despues de pasar por `_slug`).
ALIAS_COLUMNAS = {
    "candidato_a": "nombre", "candidato": "nombre", "candidata": "nombre",
    "numero_de_lista": "lista_numero", "n_de_lista": "lista_numero", "lista": "lista_numero",
    "organizacion": "organizacion_politica",
    "jurisdiccion": "jurisdiccion_dpa", "dpa": "jurisdiccion_dpa",
    "estado_cne": "estado_candidatura", "estado": "estado_candidatura",
}
ALIAS_DIGNIDAD = {
    "prefectura": "prefecto", "alcaldia": "alcalde", "concejalia": "concejal",
    "concejalia_urbana": "concejal_urbano", "concejalia_rural": "concejal_rural",
    "vocalia_junta_parroquial": "vocal_junta_parroquial",
}
ALIAS_ESTADO = {
    "oficializado": "oficializado", "oficializada": "oficializado",
    "inscrito": "inscrito", "inscrita": "inscrito",
    "aun_sin_oficializar": "inscrito", "sin_oficializar": "inscrito", "pendiente": "inscrito",
}
DPA_EN_PARENTESIS_RE = re.compile(r"\((\d{1,6})\)\s*$")
PERIODO_RE = re.compile(r"^(\d{4})-(\d{4})$")
ORDEN_ESTADO = {EstadoCandidatura.inscrito: 0, EstadoCandidatura.oficializado: 1}


@dataclass
class Reporte:
    filas_leidas: int = 0
    candidaturas_nuevas: int = 0
    candidaturas_actualizadas: int = 0
    candidaturas_sin_cambios: int = 0
    candidatos_nuevos: int = 0
    candidatos_actualizados: int = 0
    candidatos_sin_cambios: int = 0
    candidatos_no_incluidos: int = 0
    cambios_estado: list[dict] = field(default_factory=list)
    errores: list[tuple[int, str]] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    aplicado: bool = False


@dataclass
class _Fila:
    n: int
    organizacion: str
    lista: str
    dignidad: str
    dpa: str
    periodo: str
    estado: EstadoCandidatura
    plan: EstadoPlanCandidatura | None
    nombre: str
    posicion: int

    @property
    def clave(self) -> tuple[str, str, str, str]:
        return (self.dignidad, self.dpa, self.lista, self.periodo)


def _sin_tildes(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")


def normalizar_nombre(nombre: str) -> str:
    return " ".join(_sin_tildes(nombre).lower().split())


def _slug(texto: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _sin_tildes(texto).lower()).strip("_")


def _decodificar(contenido: bytes) -> str:
    try:
        return contenido.decode("utf-8-sig")
    except UnicodeDecodeError:
        return contenido.decode("cp1252")  # Excel en Windows


def _leer_filas(contenido: bytes) -> tuple[list[dict[str, str]], list[str], list[str]]:
    """(filas con su numero de linea en `_n`, columnas desconocidas, opcionales ausentes)."""
    texto = _decodificar(contenido)
    primera = texto.split("\n", 1)[0]
    delimitador = max((",", ";", "\t"), key=primera.count)  # Excel en espanol usa ';'
    lector = csv.reader(io.StringIO(texto), delimiter=delimitador)
    try:
        encabezado = [ALIAS_COLUMNAS.get(_slug(h), _slug(h)) for h in next(lector)]
    except StopIteration:
        raise ValueError("El archivo esta vacio.")
    faltan = [c for c in COLUMNAS if c not in encabezado and c not in OPCIONALES]
    if faltan:
        raise ValueError(f"Faltan columnas: {', '.join(faltan)}.")
    desconocidas = [h for h in encabezado if h and h not in COLUMNAS]
    filas = []
    for n, valores in enumerate(lector, start=2):
        if not any(v.strip() for v in valores):
            continue
        fila = {c: "" for c in COLUMNAS}
        fila.update({h: (valores[i].strip() if i < len(valores) else "") for i, h in enumerate(encabezado) if h})
        fila["_n"] = n
        filas.append(fila)
    return filas, desconocidas, [c for c in OPCIONALES if c not in encabezado]


def _normalizar_dpa(valor: str) -> str | None:
    """Excel borra el cero inicial de '0207' -> '207': los codigos DPA tienen 2, 4 o 6
    digitos, asi que un largo impar solo puede ser un cero perdido."""
    if not valor.isdigit() or not 1 <= len(valor) <= 6:
        return None
    return valor.zfill(len(valor) + len(valor) % 2)


def _validar_fila(
    f: dict[str, str], reporte: Reporte, dpa_completados: set[str], ausentes: list[str]
) -> _Fila | None:
    n = f["_n"]
    problemas: list[str] = []

    vacias = [c for c in COLUMNAS if not f[c] and c not in ausentes]
    if vacias:
        reporte.errores.append((n, f"Campos vacios: {', '.join(vacias)}."))
        return None
    if f["organizacion_politica"].upper().startswith("EJEMPLO"):
        reporte.errores.append((n, "Es una fila de ejemplo de la plantilla: borrala."))
        return None

    dignidad = _slug(f["dignidad"])
    dignidad = ALIAS_DIGNIDAD.get(dignidad, dignidad)
    if dignidad not in DIGNIDAD_A_NIVEL_GOBIERNO:
        problemas.append(f"dignidad '{f['dignidad']}' no reconocida (usa: {', '.join(DIGNIDAD_A_NIVEL_GOBIERNO)})")

    # El archivo oficial escribe "Guayas (09)": el codigo es lo que va entre parentesis.
    bruto_dpa = f["jurisdiccion_dpa"]
    en_parentesis = DPA_EN_PARENTESIS_RE.search(bruto_dpa)
    if en_parentesis:
        bruto_dpa = en_parentesis.group(1)
    dpa = _normalizar_dpa(bruto_dpa)
    if dpa is None:
        problemas.append("jurisdiccion_dpa debe ser un codigo numerico de 2, 4 o 6 digitos")
    elif dpa != bruto_dpa:
        dpa_completados.add(f"{bruto_dpa} -> {dpa}")

    periodo = re.sub(r"\s*[\u2010-\u2015]\s*", "-", f["periodo"])  # 2027–2031 -> 2027-2031
    m = PERIODO_RE.match(periodo)
    if not m or int(m.group(2)) <= int(m.group(1)):
        problemas.append("periodo debe ser como 2027-2031")

    try:
        estado = EstadoCandidatura(ALIAS_ESTADO[_slug(f["estado_candidatura"])])
    except KeyError:
        problemas.append("estado_candidatura debe ser inscrito u oficializado")
        estado = None
    plan = None  # None = no viene en el archivo
    if f["estado_plan"]:
        try:
            plan = EstadoPlanCandidatura(_slug(f["estado_plan"]))
        except ValueError:
            problemas.append("estado_plan debe ser registrado o sin_plan_registrado")
    posicion = 0  # 0 = se asigna despues, por orden de aparicion
    if f["posicion_lista"]:
        try:
            posicion = int(f["posicion_lista"])
            if posicion < 1:
                raise ValueError
        except ValueError:
            problemas.append("posicion_lista debe ser un entero positivo")
    if problemas:
        reporte.errores.append((n, "; ".join(problemas) + "."))
        return None
    lista = re.sub(r"\.0$", "", f["lista_numero"])  # Excel: 18 -> "18.0"
    return _Fila(n, f["organizacion_politica"], lista, dignidad, dpa, periodo, estado, plan,
                 f["nombre"], posicion)


def procesar(session: Session, contenido: bytes, *, aplicar: bool) -> Reporte:
    reporte = Reporte()
    if len(contenido) > MAX_BYTES:
        reporte.errores.append((0, f"El archivo supera {MAX_BYTES // 1024 // 1024} MB."))
        return reporte
    try:
        crudas, desconocidas, ausentes = _leer_filas(contenido)
    except ValueError as exc:
        reporte.errores.append((1, str(exc)))
        return reporte
    if len(crudas) > MAX_FILAS:
        reporte.errores.append((0, f"El archivo tiene mas de {MAX_FILAS} filas."))
        return reporte
    reporte.filas_leidas = len(crudas)
    if desconocidas:
        reporte.avisos.append(f"Columnas ignoradas: {', '.join(desconocidas)}.")

    dpa_completados: set[str] = set()
    filas = [x for x in (_validar_fila(f, reporte, dpa_completados, ausentes) for f in crudas) if x]
    if dpa_completados:
        reporte.avisos.append(
            "Se completo el cero inicial de jurisdiccion_dpa (Excel lo borra): "
            + ", ".join(sorted(dpa_completados)[:5]) + ("..." if len(dpa_completados) > 5 else "")
        )

    grupos: dict[tuple, list[_Fila]] = {}
    for fila in filas:
        grupos.setdefault(fila.clave, []).append(fila)

    if "posicion_lista" in ausentes:
        for miembros in grupos.values():
            for i, m in enumerate(miembros, start=1):
                m.posicion = i
    if "estado_plan" in ausentes:
        reporte.avisos.append(
            "El archivo no trae estado_plan: las candidaturas nuevas se crean como 'registrado' y las "
            "existentes conservan el suyo. Corrijalo despues si alguna no registro plan ante el CNE.")

    for clave, miembros in grupos.items():
        base = miembros[0]
        for otro in miembros[1:]:
            for campo, etiqueta in (("organizacion", "organizacion_politica"),
                                    ("estado", "estado_candidatura"), ("plan", "estado_plan")):
                if campo == "plan" and "estado_plan" in ausentes:
                    continue
                if getattr(otro, campo) != getattr(base, campo):
                    reporte.errores.append((otro.n, f"{etiqueta} difiere de la fila {base.n}, "
                                            "que es de la misma lista."))
        vistos_nombre: dict[str, int] = {}
        vistas_pos: dict[int, int] = {}
        for m in miembros:
            nn = normalizar_nombre(m.nombre)
            if nn in vistos_nombre:
                reporte.errores.append((m.n, f"Candidato repetido (ya esta en la fila {vistos_nombre[nn]})."))
            if m.posicion in vistas_pos:
                reporte.errores.append((m.n, f"posicion_lista repetida (ya esta en la fila {vistas_pos[m.posicion]})."))
            vistos_nombre.setdefault(nn, m.n)
            vistas_pos.setdefault(m.posicion, m.n)

    existentes: dict[tuple, list[Candidatura]] = {}
    for c in session.execute(select(Candidatura).options(selectinload(Candidatura.candidatos))).scalars():
        existentes.setdefault((_slug(c.dignidad), c.jurisdiccion_dpa, c.lista_numero, c.periodo), []).append(c)

    for clave, miembros in grupos.items():
        if len(existentes.get(clave, [])) > 1:
            reporte.errores.append((miembros[0].n, "Hay mas de una candidatura con esa dignidad/jurisdiccion/lista/periodo "
                                    "en la base: resolverlo a mano antes de cargar."))

    if reporte.errores:
        return reporte  # todo o nada

    for clave, miembros in grupos.items():
        base = miembros[0]
        previas = existentes.get(clave)
        if not previas:
            reporte.candidaturas_nuevas += 1
            reporte.candidatos_nuevos += len(miembros)
            if aplicar:
                cand = Candidatura(
                    organizacion_politica=base.organizacion, lista_numero=base.lista, dignidad=base.dignidad,
                    jurisdiccion_dpa=base.dpa, periodo=base.periodo,
                    estado_plan=base.plan or EstadoPlanCandidatura.registrado,
                    estado_candidatura=base.estado,
                )
                cand.candidatos = [Candidato(nombre=m.nombre, posicion_lista=m.posicion) for m in miembros]
                session.add(cand)
            continue

        cand = previas[0]
        cambio = False
        if cand.estado_candidatura != base.estado:
            reporte.cambios_estado.append({
                "dignidad": base.dignidad, "jurisdiccion_dpa": base.dpa, "lista_numero": base.lista,
                "organizacion": base.organizacion,
                "de": cand.estado_candidatura.value, "a": base.estado.value,
            })
            if ORDEN_ESTADO[base.estado] < ORDEN_ESTADO[cand.estado_candidatura]:
                reporte.avisos.append(
                    f"Lista {base.lista} ({base.dignidad}, {base.dpa}) retrocede de "
                    f"{cand.estado_candidatura.value} a {base.estado.value}: confirma que es correcto.")
            cambio = True
            if aplicar:
                cand.estado_candidatura = base.estado
        if base.plan is not None and cand.estado_plan != base.plan:
            cambio = True
            if aplicar:
                cand.estado_plan = base.plan
        if cand.organizacion_politica != base.organizacion:
            cambio = True
            if aplicar:
                cand.organizacion_politica = base.organizacion
        if cambio:
            reporte.candidaturas_actualizadas += 1
        else:
            reporte.candidaturas_sin_cambios += 1

        por_nombre = {normalizar_nombre(c.nombre): c for c in cand.candidatos}
        for m in miembros:
            ya = por_nombre.pop(normalizar_nombre(m.nombre), None)
            if ya is None:
                reporte.candidatos_nuevos += 1
                if aplicar:
                    cand.candidatos.append(Candidato(nombre=m.nombre, posicion_lista=m.posicion))
            elif ya.posicion_lista != m.posicion:
                reporte.candidatos_actualizados += 1
                if aplicar:
                    ya.posicion_lista = m.posicion
            else:
                reporte.candidatos_sin_cambios += 1
        reporte.candidatos_no_incluidos += len(por_nombre)

    if aplicar:
        try:
            session.commit()
        except Exception:
            session.rollback()
            raise
        reporte.aplicado = True
    return reporte
