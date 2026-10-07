"""App web para probar que dos versiones de un mismo imprimible Jasper
(p.ej. Car_Mul vs Car_Mul_V3) producen el mismo contenido, con datos reales.

- /cases: arma una tanda de casos de prueba multinciso (POLICY_ID, ANNEX_ID)
  leyendo INSOR_GDS.CAR_MUL_VIEW (ver cases.py).
- /products: catalogo producto/subtipo -> product_code, para los selectores
  de la busqueda por producto (Car_Ind/Cot_Ind).
- /policy_cases: arma una tanda de casos para Car_Ind/Cot_Ind filtrando
  insis_gen_v10.policy por estado + producto.
- /policy_cases_sample: junta unos pocos casos de CADA producto del catalogo
  (opcionalmente solo autos o solo danios), para cobertura amplia en una
  sola tanda.
- /resolve_policy: busca una poliza por policy_id/policy_no/policy_lot/
  engagement_id/quote_id (el mismo buscador de generar-imprimibles, ver
  resolver.py), para la otra forma de conseguir insumos.
- /policy_annexes: endosos de las polizas resueltas por identificador
  (annex_details_view), solo informativo.
- /report_families: nombres de carpeta bajo PRINTOUTS_DIR (printouts.py),
  para sugerir valores en los campos Reporte A/B.
- /run_case: para un caso, pide el PDF a ambos reportes via OIC (reports.py),
  mide cuanto tarda cada uno y diffea el texto extraido (compare.py).
- /doc_resolve, /doc_for_policy, /doc_download: pestaña "Descargar
  documentos" — que imprimibles tiene una poliza (documentos.py, port de
  generar-imprimibles) y su descarga, avisando si el PDF vino en blanco.
- /pdf/<token>: sirve el PDF de una corrida anterior (para verlo/descargarlo).
- /vistas/*: pestaña "Comparar vistas" — el texto de cualquier vista (todas
  las del repo, las de un release o las que se agreguen) en cada ambiente y
  en el repo INSOR (vistas.py, repo_views.py), y el diff entre dos de esas
  versiones. Extraido de versiones-vistas-imprimibles. /vistas/pisar
  instala en un ambiente la version del repo (instalar.py).
- /releases, /releases/<fecha>: pestaña "Releases" — registro de que
  tocamos de nuestro lado (vistas y Jasper) en cada pase a PROD (releases.py,
  un JSON por release en releases/).

Un solo usuario, sin persistencia entre reinicios: los PDFs de la corrida
viven en memoria (ver _pdf_store).
"""
from __future__ import annotations

import time
import uuid

import requests
from flask import Flask, Response, abort, jsonify, render_template, request

from cases import FAMILIES, list_mul_cases, list_policy_cases, list_products, list_products_sample
from compare import compare_pdfs, pdf_stats
from config.environments import ENVIRONMENTS
from documentos import REPORTS as DOC_REPORTS, blank_hint, compute_documents, resolve as resolve_documents
from printouts import PRINTOUTS_DIR, list_families as list_report_families
import instalar
import repo_views
from reports import OIC_PASSWORD, fetch_report
from releases import get_release, list_releases
from resolver import list_annexes, resolve as resolve_policy
from vistas import build_diff as build_view_diff, get_env_sources

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.json.sort_keys = False

# token -> bytes PDF de la ultima corrida. Se acota para no crecer sin limite
# en una sesion larga (no hace falta persistir entre corridas viejas).
_pdf_store: dict[str, bytes] = {}
_pdf_order: list[str] = []
_PDF_STORE_MAX = 200

# Fuente extra de la pestaña "Comparar vistas": el .sql de la vista en el repo.
REPO_SOURCE = "REPO"


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


@app.route("/policy_cases_sample", methods=["POST"])
def policy_cases_sample_route():
    data = request.get_json(silent=True) or {}
    env = data.get("env", "")
    family = data.get("family", "")
    scope = data.get("scope", "all")
    per_product = data.get("per_product", 2)
    if env not in ENVIRONMENTS:
        return jsonify({"error": "Ambiente invalido."}), 400
    try:
        cases = list_products_sample(env, family, scope=scope, per_product=per_product)
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

    # Mismo formato de caso que /policy_cases, para reusar tal cual la tabla
    # "Pólizas encontradas".
    # engagement_id/eng_pol_type solo vienen poblados para polizas de un
    # multinciso de autos (ver engagement_of_policies en resolver.py).
    cases = [
        {
            "policy_id": m.get("policy_id"),
            "policy_no": m.get("policy_no"),
            "policy_lot": m.get("policy_lot"),
            "insr_type": m.get("insr_type"),
            "policy_state": m.get("policy_state"),
            "quote_id": None,
            "eng_pol_type": m.get("eng_pol_type"),
            "engagement_id": m.get("engagement_id"),
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


@app.route("/policy_annexes", methods=["POST"])
def policy_annexes_route():
    data = request.get_json(silent=True) or {}
    env = data.get("env", "")
    policy_ids = data.get("policy_ids") or []
    if env not in ENVIRONMENTS:
        return jsonify({"error": "Ambiente invalido."}), 400
    try:
        annexes = [a for pid in policy_ids for a in list_annexes(env, pid)]
    except Exception as exc:  # tunel / conexion / oracle
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 502
    return jsonify({"annexes": annexes})


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


@app.route("/doc_resolve", methods=["POST"])
def doc_resolve_route():
    data = request.get_json(silent=True) or {}
    env = data.get("env", "")
    if env not in ENVIRONMENTS:
        return jsonify({"error": "Ambiente invalido."}), 400
    try:
        return jsonify(resolve_documents(env, data.get("policy", "")))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # tunel / conexion / oracle
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 502


@app.route("/doc_for_policy", methods=["POST"])
def doc_for_policy_route():
    """Documentos de una poliza puntual, cuando /doc_resolve encontro varias."""
    data = request.get_json(silent=True) or {}
    env = data.get("env", "")
    policy = data.get("policy") or {}
    if env not in ENVIRONMENTS:
        return jsonify({"error": "Ambiente invalido."}), 400
    if not policy.get("policy_id"):
        return jsonify({"error": "Falta policy_id."}), 400
    try:
        return jsonify(compute_documents(env, policy))
    except Exception as exc:
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 502


@app.route("/doc_download", methods=["POST"])
def doc_download_route():
    data = request.get_json(silent=True) or {}
    env = data.get("env", "")
    report = data.get("report", "")
    params = data.get("params") or []
    if env not in ENVIRONMENTS:
        return jsonify({"error": "Ambiente invalido."}), 400
    if report not in DOC_REPORTS:
        return jsonify({"error": f"Reporte desconocido: {report}"}), 400
    if not OIC_PASSWORD:
        return jsonify({"error": "OIC_PASSWORD no configurado en .env."}), 500

    t0 = time.monotonic()
    try:
        resp = fetch_report(env, report, params)
    except requests.RequestException as exc:
        return jsonify({"error": f"Error llamando a OIC: {exc}"}), 502
    elapsed = time.monotonic() - t0

    if resp.status_code != 200:
        detail = resp.text[:500].strip()
        hint = None
        if resp.status_code == 500 and not detail:
            # OIC no devuelve la traza de Jasper: sin cuerpo es o reporte no
            # desplegado en el ambiente o un jrxml que explota con estos datos.
            hint = ("El reporte no está desplegado en este ambiente o el .jrxml falla con "
                    "estos datos (OIC no da detalle).")
        return jsonify({"error": f"OIC devolvio {resp.status_code}", "detail": detail,
                        "hint": hint, "elapsed": elapsed}), 502

    try:
        pages, lines = pdf_stats(resp.content)
    except Exception as exc:
        return jsonify({"error": f"OIC devolvio algo que no es un PDF legible: {exc}"}), 502
    blank = lines == 0
    return jsonify({
        "token": _store_pdf(resp.content),
        "filename": f"{report}_{'_'.join(str(v) for v in params)}_{env}.pdf",
        "size": len(resp.content),
        "pages": pages,
        "blank": blank,
        "hint": blank_hint(report, params) if blank else None,
        "elapsed": elapsed,
    })


@app.route("/pdf/<token>")
def pdf_route(token):
    pdf_bytes = _pdf_store.get(token)
    if pdf_bytes is None:
        abort(404)
    return Response(pdf_bytes, mimetype="application/pdf")


@app.route("/vistas/repo_info")
def vistas_repo_info_route():
    try:
        return jsonify({"repo": repo_views.repo_info()})
    except Exception as exc:  # repo no encontrado / git no instalado
        return jsonify({"repo": {"error": f"{type(exc).__name__}: {exc}"}})


@app.route("/vistas/repo_index")
def vistas_repo_index_route():
    try:
        return jsonify({"views": sorted(repo_views.get_index())})
    except Exception as exc:
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 502


@app.route("/releases")
def releases_route():
    return jsonify({"releases": list_releases()})


@app.route("/releases/<fecha>")
def release_route(fecha):
    try:
        return jsonify(get_release(fecha))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404


@app.route("/vistas/fuente", methods=["POST"])
def vistas_fuente_route():
    """Las vistas pedidas en UNA fuente (un ambiente o REPO). El frontend llama
    una vez por fuente en paralelo, asi un ambiente caido no frena al resto."""
    data = request.get_json(silent=True) or {}
    source = data.get("source", "")
    # "Todas las vistas del repo" son ~80; el tope queda lejos del limite de
    # 1000 elementos de un IN de Oracle.
    names = [str(n) for n in (data.get("views") or [])][:500]
    if not names:
        return jsonify({"error": "Faltan las vistas."}), 400
    try:
        if source == REPO_SOURCE:
            views = repo_views.get_repo_sources(names)
        elif source in ENVIRONMENTS:
            views = get_env_sources(source, names)
        else:
            return jsonify({"error": "Fuente invalida."}), 400
    except Exception as exc:  # tunel / conexion / oracle / git
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 502
    return jsonify({"source": source, "views": views})


@app.route("/vistas/diff", methods=["POST"])
def vistas_diff_route():
    data = request.get_json(silent=True) or {}
    return jsonify(build_view_diff(
        data.get("sql_a") or "", data.get("sql_b") or "",
        data.get("label_a") or "A", data.get("label_b") or "B",
    ))


@app.route("/vistas/repo_fetch", methods=["POST"])
def vistas_repo_fetch_route():
    try:
        return jsonify({"repo": repo_views.fetch_origin()})
    except Exception as exc:
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 502


@app.route("/vistas/pisar_preview")
def vistas_pisar_preview_route():
    """El CREATE que se va a ejecutar (sin la query), para mostrarlo antes de confirmar."""
    try:
        rep = instalar.armar_ddl(request.args.get("view", ""))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 502
    return jsonify({k: rep[k] for k in ("view", "schema", "header", "file", "commit", "md5")})


@app.route("/vistas/pisar", methods=["POST"])
def vistas_pisar_route():
    data = request.get_json(silent=True) or {}
    if data.get("env") not in ENVIRONMENTS:
        return jsonify({"error": "Ambiente invalido."}), 400
    try:
        return jsonify(instalar.instalar(
            data["env"], data.get("view", ""), data.get("md5_repo"), data.get("md5_env"),
            data.get("confirmacion", ""),
        ))
    except instalar.Conflicto as exc:
        return jsonify({"error": str(exc), "conflicto": True}), 409
    except (ValueError, PermissionError) as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # oracle / tunel / git
        return jsonify({"error": f"{type(exc).__name__}: {exc}" if not isinstance(exc, RuntimeError) else str(exc)}), 502


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5003, debug=True)
