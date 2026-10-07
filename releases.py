"""Registro de releases (pases a PROD): que items fueron en cada uno y que
tocamos de nuestro lado (imprimibles: vistas de INSOR y reportes Jasper).

Cada release es un JSON en releases/AAAA-MM-DD.json, versionado con la app.
Se arma a mano (o pidiendoselo a Claude) a partir del correo de VoBo del pase,
del contenido a liberar de cada item en Azure DevOps y de los commits del
repo INSOR que lo nombran. Campos de cada item:

- id, tipo, titulo, track: como vienen en el correo del pase.
- lado: "imprimibles" (cambiamos vistas y/o Jasper), "insis" (es de
  imprimibles pero el arreglo esta en INSIS), "no_aplica" (no toca
  imprimibles) o "sin_registro" (no hay commits ni items en ADO para saberlo,
  p.ej. incidentes de L2).
- vistas / jasper / commits: lo que cambiamos (solo si lado = imprimibles).
- en_correo: False si va en el pase pero no figuraba en el correo (p.ej. bugs
  hijos de una historia que si figura, ver incluido_por). confirmar: True si
  no se sabe si va en este pase.

La "foto" (tomar_foto) guarda, por cada vista del release, que commit del repo
y que version (md5 normalizado de sqltext) habia en cada ambiente al momento
de registrarlo: sirve para saber despues que version fue en ese pase.

CLI:  py -3.10 releases.py foto 2026-10-06
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RELEASES_DIR = os.path.join(BASE_DIR, "releases")
_FECHA_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
FOTO_AMBIENTES = ["UAT", "STST", "PROD"]


def _path(fecha: str) -> str:
    if not _FECHA_RE.match(fecha or ""):
        raise ValueError("Fecha de release invalida (se espera AAAA-MM-DD).")
    return os.path.join(RELEASES_DIR, f"{fecha}.json")


def get_release(fecha: str) -> dict:
    path = _path(fecha)
    if not os.path.exists(path):
        raise ValueError(f"No hay release registrado para {fecha}.")
    with open(path, encoding="utf-8") as fh:
        release = json.load(fh)
    release["vistas"] = vistas_de(release)
    return release


def vistas_de(release: dict) -> list[dict]:
    """Vistas unicas del release, en orden de aparicion, con los items que las
    tocan (misma forma que usa la pestaña Comparar vistas)."""
    vistas: dict[str, dict] = {}
    for item in release.get("items", []):
        for nombre in item.get("vistas", []):
            v = vistas.setdefault(nombre, {"vista": nombre, "items": []})
            v["items"].append({
                "id": item["id"], "tipo": item["tipo"], "titulo": item["titulo"],
                "en_correo": item.get("en_correo", True), "confirmar": bool(item.get("confirmar")),
            })
    return list(vistas.values())


def list_releases() -> list[dict]:
    """Resumen de cada release, el mas nuevo primero, con las vistas que toco."""
    out = []
    if not os.path.isdir(RELEASES_DIR):
        return out
    for f in sorted(os.listdir(RELEASES_DIR), reverse=True):
        if not f.endswith(".json"):
            continue
        try:
            r = get_release(f[:-5])
        except (ValueError, json.JSONDecodeError):
            continue
        items = r.get("items", [])
        out.append({
            "fecha": r["fecha"],
            "nombre": r.get("nombre", ""),
            "items": len(items),
            "imprimibles": sum(1 for i in items if i.get("lado") == "imprimibles"),
            "vistas": [v["vista"] for v in r["vistas"]],
        })
    return out


def tomar_foto(fecha: str) -> dict:
    """Guarda en el release el commit del repo y el md5 de cada vista por ambiente."""
    import repo_views
    import vistas as vistas_db

    path = _path(fecha)
    with open(path, encoding="utf-8") as fh:
        release = json.load(fh)
    nombres = [v["vista"] for v in vistas_de(release)]
    repo = repo_views.get_repo_sources(nombres)
    por_env, errores = {}, {}
    for env in FOTO_AMBIENTES:
        try:
            por_env[env] = vistas_db.get_env_sources(env, nombres)
        except Exception as exc:  # un ambiente caido no invalida la foto del resto
            errores[env] = f"{type(exc).__name__}: {exc}"
    foto = {"tomada": datetime.now().isoformat(timespec="seconds"), "ref": repo_views.REPO_REF,
            "errores": errores, "vistas": {}}
    for n in nombres:
        r = repo.get(n, {})
        foto["vistas"][n] = {
            "archivo": r.get("file"),
            "commit": r.get("commit"),
            "md5_repo": r.get("md5"),
            "md5": {env: (por_env[env].get(n) or {}).get("md5") for env in por_env},
        }
    release["foto"] = foto
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(release, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return foto


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "foto":
        f = tomar_foto(sys.argv[2])
        for n, v in f["vistas"].items():
            estado = ", ".join(f"{e}={'igual' if m == v['md5_repo'] else 'distinta'}" for e, m in v["md5"].items())
            print(f"{n}: {v['archivo']} @ {(v['commit'] or {}).get('hash')} -> {estado}")
        if f["errores"]:
            print("Sin foto en:", f["errores"])
    else:
        print(__doc__)
