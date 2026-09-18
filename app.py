"""App web para probar que dos versiones de un mismo imprimible Jasper
(p.ej. Car_Mul vs Car_Mul_V3) producen el mismo contenido, con datos reales.

- /cases: arma una tanda de casos de prueba multinciso (POLICY_ID, ANNEX_ID)
  leyendo INSOR_GDS.CAR_MUL_VIEW (ver cases.py).
- /products: catalogo producto/subtipo -> product_code, para los selectores
  de la busqueda por producto (Car_Ind/Cot_Ind).
- /policy_cases: arma una tanda de casos para Car_Ind/Cot_Ind filtrando
  insis_gen_v10.policy por estado + producto.
- /resolve_policy: busca una poliza por policy_id/policy_no/policy_lot/
  engagement_id/quote_id (el mismo buscador de generar-imprimibles, ver
  resolver.py), para la otra forma de conseguir insumos.
- /report_families: nombres de carpeta bajo PRINTOUTS_DIR (printouts.py),
  para sugerir valores en los campos Reporte A/B.
- /run_case: para un caso, pide el PDF a ambos reportes via OIC (reports.py),
  mide cuanto tarda cada uno y diffea el texto extraido (compare.py).
- /pdf/<token>: sirve el PDF de una corrida anterior (para verlo/descargarlo).

Un solo usuario, sin persistencia entre reinicios: los PDFs de la corrida
viven en memoria (ver _pdf_store).
"""
from __future__ import annotations

import time
import uuid

import requests
from flask import Flask, Response, abort, jsonify, render_template, request

from cases import FAMILIES, list_mul_cases, list_policy_cases, list_products
from compare import compare_pdfs
from config.environments import ENVIRONMENTS
from printouts import PRINTOUTS_DIR, list_families as list_report_families
from reports import OIC_PASSWORD, fetch_report
from resolver import resolve as resolve_policy

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.json.sort_keys = False

# token -> bytes PDF de la ultima corrida. Se acota para no crecer sin limite
# en una sesion larga (no hace falta persistir entre corridas viejas).
_pdf_store: dict[str, bytes] = {}
_pdf_order: list[str] = []
_PDF_STORE_MAX = 200


def _store_pdf(pdf_bytes: bytes | None) -> str | None:
    if not pdf_bytes:
        return None
    token = uuid.uuid4().hex
    _pdf_store[token] = pdf_bytes
    _pdf_order.append(token)
    while len(_pdf_order) > _PDF_STORE_MAX:
        old = _pdf_order.pop(0)
        _pdf_store.pop(old, None)
    return token


@app.route("/")
def index():
    envs = [{"key": k, "label": v["label"]} for k, v in ENVIRONMENTS.items()]
    return render_template("index.html", environments=envs)


@app.route("/report_families")
def report_families_route():
    return jsonify({"families": list_report_families(), "dir": PRINTOUTS_DIR})


@app.route("/cases", methods=["POST"])
def cases_route():
    data = request.get_json(silent=True) or {}
    env = data.get("env", "")
    limit = data.get("limit", 5)
    if env not in ENVIRONMENTS:
        return jsonify({"error": "Ambiente invalido."}), 400
    try:
        cases = list_mul_cases(env, limit=limit)
    except Exception as exc:  # tunel / conexion / oracle
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 502
    return jsonify({"cases": cases})


@app.route("/products", methods=["POST"])
def products_route():
    data = request.get_json(silent=True) or {}
    env = data.get("env", "")
    if env not in ENVIRONMENTS:
        return jsonify({"error": "Ambiente invalido."}), 400
    try:
        products = list_products(env)
    except Exception as exc:
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 502
    return jsonify({"products": products, "families": [{"key": k, "label": v["label"]} for k, v in FAMILIES.items()]})


@app.route("/policy_cases", methods=["POST"])
def policy_cases_route():
    data = request.get_json(silent=True) or {}
    env = data.get("env", "")
    family = data.get("family", "")
    product_codes = data.get("product_codes") or []
    limit = data.get("limit", 5)
    if env not in ENVIRONMENTS:
        return jsonify({"error": "Ambiente invalido."}), 400
    if not product_codes:
        return jsonify({"error": "Falta elegir un producto."}), 400
    try:
        cases = list_policy_cases(env, family, [int(c) for c in product_codes], limit=limit)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # tunel / conexion / oracle
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 502
    return jsonify({"cases": cases})


@app.route("/resolve_policy", methods=["POST"])
def resolve_policy_route():
    data = request.get_json(silent=True) or {}
    env = data.get("env", "")
    policy = data.get("policy", "")
    if env not in ENVIRONMENTS:
        return jsonify({"error": "Ambiente invalido."}), 400
    try:
        result = resolve_policy(env, policy)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # tunel / conexion / oracle
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 502

    # Mismo formato de caso que /cases y /policy_cases, para reusar tal cual
    # la tabla "Pólizas encontradas" y el traspaso a "Casos de prueba".
    # eng_pol_type solo viene poblado cuando el match salio de resolver un
    # engagement_id (ver list_engagement_policies en resolver.py).
    cases = [
        {
            "policy_id": m.get("policy_id"),
            "policy_no": m.get("policy_no"),
            "policy_lot": m.get("policy_lot"),
            "insr_type": m.get("insr_type"),
            "policy_state": m.get("policy_state"),
            "quote_id": None,
            "eng_pol_type": m.get("eng_pol_type"),
            "annex_id": 0,
            "no_poliza": m.get("policy_no"),
            "params": [m.get("policy_id"), 0],
        }
        for m in result.get("matches", [])
    ]
    return jsonify({
        "cases": cases,
        "engagement_id": result.get("engagement_id"),
        "quote_id": result.get("quote_id"),
    })


@app.route("/run_case", methods=["POST"])
def run_case():
    data = request.get_json(silent=True) or {}
    env = data.get("env", "")
    report_a = (data.get("report_a") or "").strip()
    report_b = (data.get("report_b") or "").strip()
    params = data.get("params") or []
    swap_order = bool(data.get("swap_order"))
    if env not in ENVIRONMENTS:
        return jsonify({"error": "Ambiente invalido."}), 400
    if not report_a or not report_b:
        return jsonify({"error": "Faltan los nombres de los reportes."}), 400
    if not OIC_PASSWORD:
        return jsonify({"error": "OIC_PASSWORD no configurado en .env."}), 500

    # El que se pide primero se beneficia del buffer cache de Oracle que deja
    # tibio el que se pidio antes (mismos bloques de la misma poliza), lo que
    # infla la mejora medida si siempre se llama en el mismo orden. Alternar
    # el orden entre casos (swap_order lo decide el frontend) reparte ese
    # sesgo entre A y B en vez de favorecer siempre al mismo.
    order = (("b", report_b), ("a", report_a)) if swap_order else (("a", report_a), ("b", report_b))

    sides = {}
    for side, report in order:
        t0 = time.monotonic()
        try:
            resp = fetch_report(env, report, params)
        except requests.RequestException as exc:
            sides[side] = {"ok": False, "error": f"Error llamando a OIC: {exc}", "elapsed": time.monotonic() - t0}
            continue
        elapsed = time.monotonic() - t0
        if resp.status_code != 200:
            sides[side] = {
                "ok": False,
                "elapsed": elapsed,
                "error": f"OIC devolvio {resp.status_code}: {resp.text[:300]}",
            }
            continue
        sides[side] = {"ok": True, "elapsed": elapsed, "size": len(resp.content), "bytes": resp.content}

    diff = None
    if sides["a"]["ok"] and sides["b"]["ok"]:
        try:
            diff = compare_pdfs(sides["a"]["bytes"], sides["b"]["bytes"], report_a, report_b)
        except Exception as exc:
            diff = {"error": f"No se pudo leer alguno de los PDFs: {exc}"}

    result = {}
    for side in ("a", "b"):
        s = sides[side]
        out = {k: v for k, v in s.items() if k != "bytes"}
        out["token"] = _store_pdf(s.get("bytes"))
        result[side] = out
    result["diff"] = diff
    result["order"] = [side for side, _ in order]
    return jsonify(result)


@app.route("/pdf/<token>")
def pdf_route(token):
    pdf_bytes = _pdf_store.get(token)
    if pdf_bytes is None:
        abort(404)
    return Response(pdf_bytes, mimetype="application/pdf")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5003, debug=True)
