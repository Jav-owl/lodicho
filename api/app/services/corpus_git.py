"""Automatiza el commit + push a lodicho-corpus para que el panel de
admin nunca requiera que el revisor toque una terminal ni tenga sus
propias credenciales de git. Usa una llave SSH dedicada (deploy key, solo
con permiso de escritura sobre lodicho-corpus) — nunca las credenciales
personales de nadie.

Pushea directo a la URL SSH del remoto (settings.corpus_git_remote) en
vez de depender de que el remote "origin" del checkout local este
configurado con esa URL — asi no importa como se haya clonado el repo.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from app.config.settings import settings

AUTOR_NOMBRE = "Lo Dicho (panel de admin)"
AUTOR_EMAIL = "admin@lodicho.zamarrito.com"


class GitCorpusError(Exception):
    pass


def commitear_y_pushear(corpus_path: Path, rutas_relativas: list[str], mensaje: str) -> str:
    """Agrega, commitea y pushea los archivos dados. Devuelve el git_sha
    del commit resultante."""
    if not settings.corpus_git_remote:
        raise GitCorpusError("CORPUS_GIT_REMOTE no esta configurado en .env")

    add = _run(["add", *rutas_relativas], corpus_path)
    if add.returncode != 0:
        raise GitCorpusError(f"git add fallo: {add.stderr.strip()}")

    commit = _run(
        [
            "-c",
            f"user.name={AUTOR_NOMBRE}",
            "-c",
            f"user.email={AUTOR_EMAIL}",
            "commit",
            "-m",
            mensaje,
        ],
        corpus_path,
    )
    if commit.returncode != 0:
        salida = f"{commit.stdout}\n{commit.stderr}"
        # Reintentar una confirmacion cuyo commit ya existe no es un error:
        # el contenido ya esta en el historial; solo falta (re)pushear.
        if "nothing to commit" not in salida and "nothing added to commit" not in salida:
            raise GitCorpusError(f"git commit fallo: {commit.stderr.strip() or commit.stdout.strip()}")

    sha = _run(["log", "-1", "--format=%H", "--", *rutas_relativas], corpus_path)
    if sha.returncode != 0 or not sha.stdout.strip():
        raise GitCorpusError("no se pudo leer el git_sha del commit recien hecho")
    git_sha = sha.stdout.strip()

    push = _run(["push", settings.corpus_git_remote, "HEAD:main"], corpus_path, con_ssh=True)
    if push.returncode != 0:
        raise GitCorpusError(f"git push fallo (el commit SI quedo hecho en local): {push.stderr.strip()}")

    return git_sha


def _run(args: list[str], corpus_path: Path, *, con_ssh: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(corpus_path), *args],
        capture_output=True,
        text=True,
        env=_env_ssh() if con_ssh else None,
    )


def _env_ssh() -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_SSH_COMMAND"] = (
        f"ssh -i {settings.corpus_git_ssh_key} -o StrictHostKeyChecking=accept-new -o IdentitiesOnly=yes"
    )
    return env
