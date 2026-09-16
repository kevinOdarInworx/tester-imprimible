"""Llamada al flujo OIC JASPER_INSOR para descargar un imprimible en PDF.

A diferencia de generar-imprimibles/config/imprimibles.py, acá el nombre del
reporte es libre (no hay catalogo fijo): esta app existe justamente para
probar reportes que todavia no estan catalogados (p.ej. Car_Mul_V3).
"""
from __future__ import annotations

import os

import requests
from dotenv import load_dotenv

from config.environments import get_environment

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

OIC_USER = os.getenv("OIC_USER", "insis.api")
OIC_PASSWORD = os.getenv("OIC_PASSWORD", "")
OIC_TIMEOUT = 180  # las versiones "originales" sin optimizar pueden tardar bastante

# SIT se mapea a 'preprod' por convencion del equipo (SIT == PREPROD).
OIC_ENV_SLUG = {
    "DEV": "dev",
    "UAT": "uat",
    "SIT": "preprod",
    "STST": "stst",
    "PROD": "prod",
}

OIC_HOST_TMPL = "oic-gs-{slug}-idqdbrk4vlf3-ia.integration.ocp.oraclecloud.com"
OIC_PATH_TMPL = "/ic/api/integration/v1/flows/rest/JASPER_INSOR/1.0/{report}.pdf"


def oic_url(env_key: str, report: str) -> str:
    get_environment(env_key)  # valida el ambiente (lanza KeyError si no existe)
    slug = OIC_ENV_SLUG[env_key.upper()]
    host = OIC_HOST_TMPL.format(slug=slug)
    return "https://" + host + OIC_PATH_TMPL.format(report=report)


def fetch_report(env_key: str, report: str, params: list) -> requests.Response:
    """POST al flujo OIC del reporte con string_param1..N = params. Puede tardar."""
    if not OIC_PASSWORD:
        raise RuntimeError("OIC_PASSWORD no esta configurado en .env.")
    body = {
        "parameters": [
            {"paramName": f"string_param{i}", "paramValue": v}
            for i, v in enumerate(params, start=1)
            if v is not None
        ]
    }
    url = oic_url(env_key, report)
    return requests.post(
        url, json=body, auth=(OIC_USER, OIC_PASSWORD),
        headers={"Accept": "application/pdf", "Content-Type": "application/json"},
        timeout=OIC_TIMEOUT,
    )
