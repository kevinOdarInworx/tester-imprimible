"""Pisar una vista de un ambiente con la version del repo (boton "Pisar" del
diff en la pestaña Comparar vistas).

El DDL es el CREATE que el .sql trae comentado (asi se guardan en el repo),
descomentado, seguido de la primera query del archivo: la misma que se
compara en la matriz. Se le saca el FORCE para que, si la query tiene un
error, Oracle no la cree y la vista del ambiente quede como estaba.

Antes de ejecutar:
- el repo y el ambiente tienen que seguir como los vio el usuario (md5 que
  manda el frontend). Si el repo se movio o alguien instalo otra cosa en el
  ambiente mientras tanto, no pisa (ver memoria "re-bajar la vista antes de
  instalar": el 02/10 se pisaron cambios ajenos en REPORT_CONSULTA_POLIZAS_V2);
- se guarda el DDL que tenia el ambiente en backups/<AMBIENTE>/.

Despues vuelve a leer la vista y avisa si quedo INVALID o distinta del repo.
Cada intento (bien o mal) queda en backups/instalaciones.jsonl.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import threading

import repo_views
from config.environments import get_environment
from db import get_connection
from sqltext import comparable_md5
from vistas import get_env_sources

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BACKUP_DIR = os.path.join(BASE_DIR, "backups")
LOG_FILE = os.path.join(BACKUP_DIR, "instalaciones.jsonl")
CONFIRMAR_PROD = "PROD"

_FORCE_RE = re.compile(r"\bREPLACE\s+FORCE\b", re.I)
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


class Conflicto(Exception):
    """El repo o el ambiente cambiaron desde que el usuario los miro."""


def _lock(key: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def armar_ddl(view_name: str) -> dict:
    """CREATE descomentado + primera query del archivo del repo."""
    name = (view_name or "").strip().upper()
    index = repo_views.get_index()
    commit = repo_views._cache["commit"]
    entry = index.get(name)
    if not entry:
        raise ValueError(f"{name} no tiene archivo en el repo.")
    text = entry["text"].replace("\r\n", "\n").replace("\r", "\n")
    m = repo_views._HEADER_RE.search(text)
    if not m:
        raise ValueError(f"{entry['path']} no tiene el CREATE de la vista.")

    prefix = text[text.rfind("\n", 0, m.start()) + 1:m.start()].strip()
    header = m.group(0)
    if prefix == "--":
        header = re.sub(r"(?m)^(\s*)--", r"\1", header)  # columnas en varias lineas, tambien comentadas
    elif prefix:
        raise ValueError(f"No sé descomentar el CREATE de {entry['path']}: la línea empieza con {prefix!r}.")
    if "--" in header or "/*" in header:
        raise ValueError(f"El CREATE de {entry['path']} tiene comentarios adentro; instalala a mano.")
    header = _FORCE_RE.sub("REPLACE", header, count=1)

    schema = m.group("a").upper() if m.group("b") else None
    if (m.group("b") or m.group("a")).upper() != name:
        raise ValueError(f"El CREATE de {entry['path']} es de otra vista.")
    if not schema:
        raise ValueError(f"El CREATE de {entry['path']} no dice el esquema; se crearía en el del usuario de la conexión.")

    select = repo_views._extract_select(entry["text"]).replace("\r\n", "\n").replace("\r", "\n")
    return {
        "view": name,
        "schema": schema,
        "header": header,
        "ddl": header + "\n" + select,
        "file": entry["path"],
        "commit": repo_views._last_commit(commit, entry["path"]),
        "md5": comparable_md5(select),
    }


def _ddl_actual(env_key: str, owner: str, name: str, antes: dict) -> str:
    """DDL exacto de la vista en el ambiente (con lista de columnas); si
    DBMS_METADATA falla, el CREATE se arma con el texto de DBA_VIEWS."""
    try:
        with get_connection(env_key) as conn:
            cur = conn.cursor()
            cur.execute("SELECT dbms_metadata.get_ddl('VIEW', :n, :o) FROM dual", n=name, o=owner)
            clob = cur.fetchone()[0]
            return (clob.read() if hasattr(clob, "read") else str(clob)).strip()
    except Exception:
        return f'CREATE OR REPLACE VIEW "{owner}"."{name}" AS\n{antes["sql"]}'


def _guardar_backup(env_key: str, name: str, antes: dict, ahora: dt.datetime) -> str:
    ddl = _ddl_actual(env_key, antes["owner"], name, antes)
    contenido = (
        f"-- Copia de {antes['owner']}.{name} en {env_key} antes de pisarla con el repo\n"
        f"-- Tomada el {ahora:%Y-%m-%d %H:%M:%S} · LAST_DDL_TIME {antes.get('last_ddl_time')} · {antes.get('status')}\n"
        f"-- Para volver a esta versión, ejecutar el CREATE de abajo.\n\n{ddl}\n"
    ).replace("\n", "\r\n")
    carpeta = os.path.join(BACKUP_DIR, env_key)
    os.makedirs(carpeta, exist_ok=True)
    ruta = os.path.join(carpeta, f"{name}_{ahora:%Y%m%d-%H%M%S}.sql")
    try:
        data = contenido.encode("cp1252")  # como el resto de los .sql (SQL Developer)
    except UnicodeEncodeError:
        data = contenido.encode("utf-8")
    with open(ruta, "wb") as f:
        f.write(data)
    return os.path.relpath(ruta, BASE_DIR).replace("\\", "/")


def _errores(env_key: str, owner: str, name: str) -> list[str]:
    try:
        with get_connection(env_key) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT line, text FROM dba_errors WHERE owner = :o AND name = :n AND type = 'VIEW' ORDER BY sequence",
                o=owner, n=name,
            )
            return [f"línea {line}: {text.strip()}" for line, text in cur]
    except Exception:
        return []


def _log(registro: dict) -> None:
    os.makedirs(BACKUP_DIR, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(registro, ensure_ascii=False) + "\n")


def instalar(env_key: str, view_name: str, md5_repo: str | None, md5_env: str | None,
             confirmacion: str = "") -> dict:
    """Pisa (o crea) la vista en el ambiente con la version del repo.

    md5_repo / md5_env: lo que el usuario vio en la matriz (md5_env None = la
    vista no existia). Si no coinciden con lo que hay ahora, lanza Conflicto.
    """
    env_key = get_environment(env_key)["key"]
    name = (view_name or "").strip().upper()
    if env_key == "PROD" and (confirmacion or "").strip() != CONFIRMAR_PROD:
        raise PermissionError(f'Para pisar PROD hay que escribir "{CONFIRMAR_PROD}".')

    rep = armar_ddl(name)
    if rep["md5"] != md5_repo:
        raise Conflicto("El repo cambió desde que lo miraste. Refrescá y revisá el diff de nuevo.")

    with _lock(f"{env_key}:{name}"):
        antes = get_env_sources(env_key, [name])[name]
        md5_antes = antes["md5"] if antes.get("found") else None
        if md5_antes != (md5_env or None):
            raise Conflicto(f"La vista cambió en {env_key} desde que la miraste. Refrescá y revisá el diff de nuevo.")
        if antes.get("found") and antes["owner"] != rep["schema"]:
            raise ValueError(f"En {env_key} la vista es de {antes['owner']} y el CREATE del repo la crea en {rep['schema']}.")
        if md5_antes == rep["md5"]:
            raise ValueError(f"{name} ya está igual al repo en {env_key}.")

        ahora = dt.datetime.now()
        backup = _guardar_backup(env_key, name, antes, ahora) if antes.get("found") else None
        registro = {
            "fecha": ahora.isoformat(timespec="seconds"), "ambiente": env_key, "vista": f"{rep['schema']}.{name}",
            "archivo": rep["file"], "commit": (rep["commit"] or {}).get("hash"),
            "md5_antes": md5_antes, "md5_repo": rep["md5"], "backup": backup,
        }
        try:
            with get_connection(env_key) as conn:
                conn.cursor().execute(rep["ddl"])
        except Exception as exc:
            registro.update(ok=False, error=str(exc))
            _log(registro)
            raise RuntimeError(f"Oracle no la creó, la vista quedó como estaba. {exc}") from exc

        despues = get_env_sources(env_key, [name])[name]
        status = despues.get("status")
        errores = _errores(env_key, rep["schema"], name) if status != "VALID" else []
        igual = despues.get("md5") == rep["md5"]
        registro.update(ok=True, status=status, igual_al_repo=igual, errores=errores)
        _log(registro)
        return {
            "ok": True, "ambiente": env_key, "vista": name, "status": status, "igual_al_repo": igual,
            "errores": errores, "backup": backup, "creada": not antes.get("found"),
        }
