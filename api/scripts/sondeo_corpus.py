"""Sondeo de recuperacion sobre `marco_legal`: comprueba que CADA documento del
corpus sea alcanzable por la busqueda que usa `/consulta`, no solo el COOTAD.

Solo lectura (embeddings + consultas a Qdrant; costo despreciable). Uso, sin
necesidad de reconstruir la imagen:

    docker compose exec -T api python - < api/scripts/sondeo_corpus.py

Parte 1: inventario (puntos por doc_id, nivel_gobierno, modelo de embedding).
Parte 2: sondas sinteticas. Por cada documento toma varios fragmentos suyos,
  usa el comienzo del fragmento como consulta (esa consulta SOLO se responde
  bien con ese documento) y verifica que el fragmento aparezca en el top-N que
  recibe el usuario, con cada nivel de gobierno que el documento declara.
Parte 3: sondas manuales (EXPECTATIVAS) con consultas escritas a mano y el
  doc_id que debe aparecer; completarlas con articulos conocidos.
"""
from __future__ import annotations

import random
from collections import Counter, defaultdict

from qdrant_client import models

from app.services import embeddings, qdrant_client as qc, sparse

TOP = 6  # LIMIT_POR_PASO_DEFAULT de services/evidencia.py
MUESTRAS_POR_DOC = 5
NIVELES = ("provincial", "cantonal", "parroquial_rural")

# (consulta, nivel_gobierno, fragmento de doc_id que debe aparecer en el top)
EXPECTATIVAS: list[tuple[str, str, str]] = [
    # Cada consulta es materia de UNA norma y el COOTAD no la responde.
    ("derecho a la salud y acceso gratuito a la atencion", "cantonal", "constitucion"),
    ("derechos de la naturaleza y su restauracion", "provincial", "constitucion"),
    ("competencia exclusiva de vialidad cantonal y transito", "cantonal", "cootad"),
    ("silencio administrativo positivo si la administracion no resuelve un tramite en el plazo",
     "cantonal", "administrativo-coa"),
    ("recurso de apelacion y reclamo contra un acto administrativo del municipio",
     "cantonal", "administrativo-coa"),
    ("limite de endeudamiento publico y regla fiscal del gasto corriente",
     "provincial", "planificacion-y-finanzas"),
    ("el plan de desarrollo y ordenamiento territorial debe articular la inversion publica",
     "cantonal", "planificacion-y-finanzas"),
    ("regulacion de cooperativas de ahorro y credito y entidades financieras",
     "cantonal", "monetario-y-financiero"),
    ("presupuesto participativo y silla vacia en las sesiones del concejo",
     "cantonal", "participacion-ciudadana"),
    ("veedurias ciudadanas y rendicion de cuentas de las autoridades",
     "provincial", "participacion-ciudadana"),
    ("multas por infracciones de transito en el distrito metropolitano de quito",
     "cantonal", "ordenanza-quito"),
    # Control negativo: la ordenanza de Quito es cantonal y no debe salir en otros niveles.
    ("multas por infracciones de transito en el distrito metropolitano de quito",
     "provincial", "NO:ordenanza-quito"),
    # Caso real que motivo el sondeo: se espera COOTAD primero, ver la mezcla del top.
    ("Castillo propone eliminar la tasa de mantenimiento vial", "cantonal", "cootad"),
]


def inventario() -> dict[str, list[models.Record]]:
    por_doc: dict[str, list[models.Record]] = defaultdict(list)
    offset = None
    while True:
        puntos, offset = qc.get_client().scroll(
            collection_name=qc.ALIAS_MARCO_LEGAL,
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for p in puntos:
            por_doc[(p.payload or {}).get("doc_id", "?")].append(p)
        if offset is None:
            return por_doc


def buscar(texto: str, nivel: str) -> list[models.ScoredPoint]:
    return qc.search_marco_legal(
        embeddings.embed_query(texto), sparse.embed_query(texto), nivel, limit=TOP
    )


def main() -> None:
    por_doc = inventario()
    print("=== 1. INVENTARIO marco_legal ===")
    if not por_doc:
        print("La coleccion esta vacia.")
        return
    for doc_id, pts in sorted(por_doc.items()):
        pl = pts[0].payload or {}
        modelos = Counter((p.payload or {}).get("embedding_modelo", "?") for p in pts)
        print(f"- {doc_id}: {len(pts)} puntos | nivel={pl.get('nivel_gobierno')} "
              f"| vigente={pl.get('vigente')} | modelos={dict(modelos)}")

    print("\n=== 2. SONDAS SINTETICAS (el fragmento debe volver en el top) ===")
    azar = random.Random(7)
    for doc_id, pts in sorted(por_doc.items()):
        niveles_doc = (pts[0].payload or {}).get("nivel_gobierno") or []
        if isinstance(niveles_doc, str):
            niveles_doc = [niveles_doc]
        muestra = azar.sample(pts, min(MUESTRAS_POR_DOC, len(pts)))
        for nivel in NIVELES:
            declarado = nivel in niveles_doc
            aciertos = 0
            otros_docs: Counter[str] = Counter()
            for p in muestra:
                consulta = ((p.payload or {}).get("texto") or "")[:300]
                resultado = buscar(consulta, nivel)
                ids = [str(r.id) for r in resultado]
                if str(p.id) in ids:
                    aciertos += 1
                otros_docs.update((r.payload or {}).get("doc_id", "?") for r in resultado)
            esperado = "debe alcanzarse" if declarado else "NO deberia alcanzarse"
            print(f"- {doc_id} / {nivel} ({esperado}): {aciertos}/{len(muestra)} en el top-{TOP}"
                  f" | docs devueltos: {dict(otros_docs)}")

    print("\n=== 3. SONDAS MANUALES ===")
    for consulta, nivel, esperado in EXPECTATIVAS:
        resultado = buscar(consulta, nivel)
        docs = [(r.payload or {}).get("doc_id", "?") for r in resultado]
        if esperado.startswith("NO:"):
            presentes = [d for d in docs if esperado[3:] in d]
            estado = "FALLA (aparece)" if presentes else "OK (ausente)"
        else:
            posiciones = [i + 1 for i, d in enumerate(docs) if esperado in d]
            estado = f"OK (posicion {posiciones[0]})" if posiciones else "FALLA (no aparece)"
        print(f"- [{estado}] '{consulta}' ({nivel}) -> esperado '{esperado}'; top: {Counter(docs)}")


main()
