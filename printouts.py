"""Catalogo de familias de imprimibles en disco (nombres de carpeta bajo
PRINTOUTS_DIR), para sugerir valores en los campos Reporte A/B.

Copiado (solo `list_families`) de versiones-vistas-imprimibles/printouts.py.
No toca la base: el nombre de carpeta es exactamente el nombre de reporte
que espera el flujo OIC JASPER_INSOR (ver reports.py/oic_url) — por eso
sirve tanto para reportes ya catalogados (Car_Mul) como para experimentales
sin desplegar todavia (Car_Mul_V3), que es justo el caso de uso de esta app.
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))


def _resolve(path: str) -> str:
    if not path:
        return path
    return path if os.path.isabs(path) else os.path.normpath(os.path.join(BASE_DIR, path))


PRINTOUTS_DIR = _resolve(
    os.getenv("PRINTOUTS_DIR", os.path.join("..", "INSOR", "shared_workspace", "PRINTOUTS", "FASE_1"))
)

# Carpetas que no son familias de imprimibles (assets, no reportes).
_SKIP_DIRS = {"images"}


def list_families() -> list[str]:
    """Carpetas bajo PRINTOUTS_DIR que tienen al menos un .jrxml propio."""
    families = []
    if not os.path.isdir(PRINTOUTS_DIR):
        return families
    for entry in sorted(os.listdir(PRINTOUTS_DIR), key=str.lower):
        full = os.path.join(PRINTOUTS_DIR, entry)
        if not os.path.isdir(full) or entry.lower() in _SKIP_DIRS:
            continue
        try:
            has_jrxml = any(f.lower().endswith(".jrxml") for f in os.listdir(full))
        except OSError:
            continue
        if has_jrxml:
            families.append(entry)
    return families
