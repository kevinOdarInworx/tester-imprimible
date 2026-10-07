"""Vistas definidas en el repo INSOR (GitHub Inworx/INSOR), para compararlas
contra los ambientes como si el repo fuera un ambiente mas ("REPO").

Adaptado de versiones-vistas-imprimibles/repo_views.py (misma forma de sacar
el nombre de la vista y el SELECT de cada .sql). Diferencias:

- Lee de una referencia git del clon local (REPO_REF, por defecto
  `origin/develop`) con `git ls-tree` + `git cat-file --batch`, no del
  working tree: el clon suele tener cambios sin commitear o estar en otra
  rama, y para un pase importa lo commiteado. La referencia solo se mueve
  con `git fetch` (ver `fetch_origin`).
- Indexa varias carpetas (REPO_VIEW_DIRS): ademas de `GDS/FASE 1`,
  `GDS/Complementarias`, donde viven las CALCULUS_V*_VIEW.
- Devuelve el ultimo commit que toco el archivo, para saber de que bug viene
  la version del repo.
"""
from __future__ import annotations

import os
import re
import subprocess
import threading
import time

from dotenv import load_dotenv

from sqltext import comparable_md5

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))


def _resolve(path: str) -> str:
    if not path:
        return path
    return path if os.path.isabs(path) else os.path.normpath(os.path.join(BASE_DIR, path))


INSOR_REPO_DIR = _resolve(os.getenv("INSOR_REPO_DIR", os.path.join("..", "INSOR")))
REPO_REF = os.getenv("REPO_REF", "origin/develop")
# Separadas por ';'. Si una vista aparece en mas de una carpeta, gana la
# primera de la lista (y dentro de cada una, el archivo fuera de las
# subcarpetas de versiones viejas).
REPO_VIEW_DIRS = [
    d.strip().strip("/")
    for d in os.getenv("REPO_VIEW_DIRS", "GDS/FASE 1;GDS/Complementarias").split(";")
    if d.strip()
]
# Los .sql del repo estan en Windows-1252 (el encoding de SQL Developer), no
# UTF-8: leerlos como UTF-8 rompe tildes y el diff marca diferencias falsas.
REPO_FILE_ENCODING = os.getenv("REPO_FILE_ENCODING", "cp1252")

# Subcarpetas con versiones viejas o en prueba: solo cuentan si no hay otra.
_LOW_PRIORITY_DIRS = {"deprecadas", "versiones en prod", "no estables"}

_HEADER_RE = re.compile(
    r"(?is)CREATE\s+OR\s+REPLACE\s+(?:FORCE\s+)?(?:EDITIONABLE\s+)?VIEW\s+"
    r'"?(?P<a>[A-Za-z0-9_]+)"?(?:\s*\.\s*"?(?P<b>[A-Za-z0-9_]+)"?)?'
    r"\s*(?:\([^)]*\))?\s*AS\b"
)
_LEADING_COMMENT_RE = re.compile(r"\A(?:\s*(?:/\*.*?\*/|--[^\n]*)\s*)*", re.DOTALL)

_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _git(*args: str, input_bytes: bytes | None = None, timeout: int = 60) -> bytes:
    proc = subprocess.run(
        ["git", *args],
        cwd=INSOR_REPO_DIR,
        input=input_bytes,
        capture_output=True,
        timeout=timeout,
        creationflags=_CREATE_NO_WINDOW,
    )
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(f"git {' '.join(args[:2])}: {err or 'fallo sin mensaje'}")
    return proc.stdout


def _dec(raw: bytes) -> str:
    """Texto que devuelve git (mensajes, nombres de archivo): UTF-8, y si no
    lo es, Windows-1252 (asi quedaron varios commits hechos desde Windows)."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def _extract_view_name(text: str) -> str | None:
    m = _HEADER_RE.search(text)
    if not m:
        return None
    return (m.group("b") or m.group("a")).upper()


def _first_statement(sql: str) -> str:
    """El SQL hasta el primer `;` (o una linea con solo `/`) que este fuera de
    literales y comentarios.

    Varios .sql del repo traen, despues del SELECT de la vista, otras queries
    de validacion (p.ej. rec-pag_nota-cred-acumulativo.sql): esas nunca estan
    en la base, asi que no cuentan para comparar.
    """
    i, n = 0, len(sql)
    while i < n:
        c = sql[i]
        if c == "'" or c == '"':
            j = i + 1
            while j < n:
                if sql[j] == c:
                    if j + 1 < n and sql[j + 1] == c:  # comilla escapada
                        j += 2
                        continue
                    break
                j += 1
            i = j + 1
        elif sql.startswith("--", i):
            j = sql.find("\n", i)
            i = n if j == -1 else j
        elif sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            i = n if j == -1 else j + 2
        elif c == ";":
            return sql[:i]
        elif c == "\n":
            j = sql.find("\n", i + 1)
            if sql[i + 1:n if j == -1 else j].strip() == "/":  # terminador de SQL*Plus
                return sql[:i]
            i += 1
        else:
            i += 1
    return sql


def _extract_select(text: str) -> str:
    m = _HEADER_RE.search(text)
    remainder = text[m.end():] if m else text
    lead = _LEADING_COMMENT_RE.match(remainder)
    if lead:
        remainder = remainder[lead.end():]
    return _first_statement(remainder).rstrip()


def _priority(path: str) -> tuple[int, int]:
    """(es version vieja, posicion de su carpeta en REPO_VIEW_DIRS): menor gana."""
    dir_idx = next(
        (i for i, d in enumerate(REPO_VIEW_DIRS) if path == d or path.startswith(d + "/")),
        len(REPO_VIEW_DIRS),
    )
    parts = path.lower().split("/")[:-1]
    return (1 if any(p in _LOW_PRIORITY_DIRS for p in parts) else 0, dir_idx)


def _read_blobs(shas: list[str]) -> dict[str, bytes]:
    """Contenido de varios blobs en una sola llamada a `git cat-file --batch`."""
    out = _git("cat-file", "--batch", input_bytes=("\n".join(shas) + "\n").encode())
    blobs: dict[str, bytes] = {}
    pos = 0
    while pos < len(out):
        nl = out.index(b"\n", pos)
        header = out[pos:nl].decode().split()
        pos = nl + 1
        if len(header) < 3 or header[1] == "missing":
            continue
        size = int(header[2])
        blobs[header[0]] = out[pos:pos + size]
        pos += size + 1  # el contenido termina con un \n extra
    return blobs


def _build_index(commit: str) -> dict[str, dict]:
    """view_name (MAYUSCULA) -> {path, text, otros}, para el commit dado."""
    listing = _git("ls-tree", "-r", "-z", commit, "--", *REPO_VIEW_DIRS)
    entries = []
    for rec in listing.split(b"\0"):
        if not rec:
            continue
        meta, path = rec.split(b"\t", 1)
        _mode, kind, sha = meta.decode().split()
        path = _dec(path)
        if kind == "blob" and path.lower().endswith(".sql"):
            entries.append((sha, path))
    blobs = _read_blobs([sha for sha, _ in entries])

    found: dict[str, list[tuple[tuple[int, int], str, str]]] = {}
    for sha, path in entries:
        raw = blobs.get(sha)
        if raw is None:
            continue
        text = raw.decode(REPO_FILE_ENCODING, errors="replace")
        name = _extract_view_name(text)
        if name:
            found.setdefault(name, []).append((_priority(path), path, text))

    index = {}
    for name, cands in found.items():
        cands.sort(key=lambda c: (c[0], c[1]))
        _prio, path, text = cands[0]
        index[name] = {"path": path, "text": text, "otros": [c[1] for c in cands[1:]]}
    return index


_cache: dict[str, object] = {"commit": None, "index": {}}
_cache_lock = threading.Lock()
_last_commit_cache: dict[tuple[str, str], dict] = {}


def _ref_commit() -> str:
    return _git("rev-parse", REPO_REF).decode().strip()


def get_index() -> dict[str, dict]:
    """Indice de la referencia actual; se rearma solo si la referencia se movio."""
    commit = _ref_commit()
    with _cache_lock:
        if _cache["commit"] != commit:
            _cache["index"] = _build_index(commit)
            _cache["commit"] = commit
        return _cache["index"]  # type: ignore[return-value]


def _last_commit(commit: str, path: str) -> dict | None:
    key = (commit, path)
    if key not in _last_commit_cache:
        out = _git("log", "-1", "--format=%h%x1f%ad%x1f%an%x1f%s", "--date=short", commit, "--", path)
        parts = _dec(out).strip().split("\x1f")
        _last_commit_cache[key] = (
            {"hash": parts[0], "date": parts[1], "author": parts[2], "subject": parts[3]}
            if len(parts) == 4 else None
        )
    return _last_commit_cache[key]


def get_repo_sources(view_names: list[str]) -> dict[str, dict]:
    """Por vista: el SELECT segun el repo, o {"found": False}. Misma forma que
    vistas.get_env_sources para que el frontend trate al repo como un ambiente."""
    index = get_index()
    commit = _cache["commit"]
    result = {}
    for name in view_names:
        name = (name or "").strip().upper()
        entry = index.get(name)
        if not entry:
            result[name] = {"found": False}
            continue
        sql = _extract_select(entry["text"]).replace("\r\n", "\n").replace("\r", "\n")
        result[name] = {
            "found": True,
            "file": entry["path"],
            "other_files": entry["otros"],
            "commit": _last_commit(commit, entry["path"]),
            "sql": sql,
            "line_count": len(sql.split("\n")),
            "char_count": len(sql),
            "md5": comparable_md5(sql),
        }
    return result


def repo_info() -> dict:
    """Rama local, referencia que se compara y cuando fue el ultimo fetch."""
    head = _git("rev-parse", "--abbrev-ref", "HEAD").decode().strip()
    tip = _dec(_git("log", "-1", "--format=%h%x1f%ad%x1f%s", "--date=short", REPO_REF)).strip().split("\x1f")
    fetch_head = os.path.join(INSOR_REPO_DIR, ".git", "FETCH_HEAD")
    fetched_at = os.path.getmtime(fetch_head) if os.path.exists(fetch_head) else None
    return {
        "dir": INSOR_REPO_DIR,
        "ref": REPO_REF,
        "local_branch": head,
        "tip": {"hash": tip[0], "date": tip[1], "subject": tip[2]} if len(tip) == 3 else None,
        "last_fetch_epoch": fetched_at,
        "last_fetch_age_min": round((time.time() - fetched_at) / 60) if fetched_at else None,
        "view_dirs": REPO_VIEW_DIRS,
    }


def fetch_origin() -> dict:
    """`git fetch origin`: solo actualiza las referencias remotas del clon
    (no toca el working tree ni la rama local)."""
    _git("fetch", "origin", "--quiet", timeout=120)
    return repo_info()
