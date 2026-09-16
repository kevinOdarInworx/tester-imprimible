"""Descubrimiento de casos de prueba (polizas reales) para comparar dos
versiones de un mismo imprimible.

Dos fuentes de casos:
- `list_mul_cases`: para la familia Car_Mul (multinciso), consultando
  exclusivamente INSOR_GDS.CAR_MUL_VIEW (ver detalle en su docstring).
- `list_products` / `list_policy_cases`: para Car_Ind / Cot_Ind, filtrando
  insis_gen_v10.policy por estado + producto (insr_type).

Todo caso devuelto trae un campo `params`, la lista lista para mandar tal
cual a /run_case (string_param1..N) — asi el frontend no necesita saber la
forma de los parametros de cada familia.
"""
from __future__ import annotations

from db import get_connection


def _to_native(value):
    """Decimal/None de oracledb -> int/None, para que sea JSON-serializable."""
    if value is None:
        return None
    return int(value)


# ---------------------------------------------------------------------------
# Car_Mul / Car_Mul_V3 (multinciso) — via INSOR_GDS.CAR_MUL_VIEW
# ---------------------------------------------------------------------------

_MUL_CANDIDATES_SQL = """
    SELECT policy_id
    FROM insis_gen_v10.policy
    WHERE policy_state >= 0
      AND insr_type NOT LIKE '1%'
    ORDER BY policy_id DESC
    FETCH FIRST :pool ROWS ONLY
"""

_MUL_VIEW_SQL = """
    SELECT POLICY_ID, ANNEX_ID, NO_POLIZA, NOMBRE_CLIENTE, CANT_UBICACIONES
    FROM INSOR_GDS.CAR_MUL_VIEW
    WHERE POLICY_ID = :p
"""

# Tope dursimo de candidatos a probar contra la vista, aunque no se junten
# los casos pedidos (para no dejar la busqueda corriendo indefinidamente si
# la mayoria de los candidatos no tiene filas en CAR_MUL_VIEW).
_MUL_MAX_PROBES = 60


def list_mul_cases(env_key: str, limit: int = 5) -> list[dict]:
    """Hasta `limit` casos (POLICY_ID, ANNEX_ID) reales, leidos de CAR_MUL_VIEW.

    Solo se devuelven casos con CANT_UBICACIONES > 1: "Mul" (Car_Mul,
    Cot_Mul, etc.) es justamente el imprimible multinciso, y con 0 o 1
    ubicacion no se ejercita el fan-out por inciso que Car_Mul_V3 optimiza —
    no serian casos utiles para demostrar ni la equivalencia ni la mejora de
    performance.

    CAR_MUL_VIEW "no tiene filtro propio" (ver
    INSOR/shared_workspace/PRINTOUTS/FASE_1/Car_Mul_V3/README.md): el costo
    caro de la version ORIGINAL de Car_Mul.jrxml (el LISTAGG sobre toda
    AGENTS_VIEW, ~19-30s) no depende de que tan grande sea la poliza
    consultada, es un costo de golpear la vista. Por eso NO se hace ningun
    SELECT sin WHERE POLICY_ID sobre ella (seria altisimo costo/riesgo en
    PROD): los candidatos a POLICY_ID salen de insis_gen_v10.policy (tabla
    base, filtro barato por policy_state/insr_type), y recien ahi se
    consulta CAR_MUL_VIEW, una vez por candidato y siempre filtrada por
    POLICY_ID, hasta juntar los casos pedidos. Como cada golpe a la vista
    puede tardar bastante, el limite de casos por defecto es chico.
    """
    limit = max(1, min(int(limit or 5), 30))
    # El filtro de multinciso (cant_ubicaciones > 1) descarta bastantes
    # candidatos, asi que conviene arrancar pidiendo el pool completo
    # permitido en vez de un multiplo chico de `limit`.
    pool = _MUL_MAX_PROBES

    cases: list[dict] = []
    with get_connection(env_key) as conn:
        with conn.cursor() as cur:
            cur.execute(_MUL_CANDIDATES_SQL, {"pool": pool})
            candidates = [r[0] for r in cur.fetchall()]

            probed = 0
            for policy_id in candidates:
                if len(cases) >= limit or probed >= _MUL_MAX_PROBES:
                    break
                probed += 1
                cur.execute(_MUL_VIEW_SQL, {"p": policy_id})
                cols = [d[0].lower() for d in cur.description]
                for row in cur.fetchall():
                    r = dict(zip(cols, row))
                    r["policy_id"] = _to_native(r["policy_id"])
                    r["annex_id"] = _to_native(r["annex_id"])
                    r["cant_ubicaciones"] = _to_native(r.get("cant_ubicaciones"))
                    # "Mul" (Car_Mul, Cot_Mul, etc.) es multinciso: un caso con
                    # 0 o 1 ubicacion no ejercita el fan-out que precisamente
                    # es lo que Car_Mul_V3 optimiza, asi que no sirve de prueba.
                    if (r["cant_ubicaciones"] or 0) > 1:
                        r["params"] = [r["policy_id"], r["annex_id"]]
                        cases.append(r)

    cases.sort(key=lambda r: (r.get("cant_ubicaciones") or 0), reverse=True)
    return cases[:limit]


# ---------------------------------------------------------------------------
# Car_Ind / Cot_Ind — via insis_gen_v10.policy filtrada por estado + producto
# ---------------------------------------------------------------------------

# Catalogo producto/subtipo -> product_code (= policy.INSR_TYPE). Query tal
# cual la usa el equipo para resolver nombres de producto (cfg_nl_product no
# tiene el nombre: hay que pasar por cfg_nl_product_text). product_text y
# subtipo (hst_object_type.name, via cpr_objects/cfg_nl_objects) no son 1 a 1
# con product_code -- un product_code puede aparecer con mas de un subtipo
# segun a que objeto de negocio (poliza, cotizacion, endoso...) este ligada
# esa configuracion -- por eso el par (producto, subtipo) es lo que resuelve
# un product_code exacto para filtrar polizas, no el producto solo.
_PRODUCTS_SQL = """
    SELECT product_code, product_text, subtipo
    FROM (
        SELECT
            cnp.product_code,
            CASE
                WHEN cnp.product_code = 2205 THEN 'Transportes Pólizas Anuales'
                ELSE cnpx.product_text
            END AS product_text,
            ot.name AS subtipo
        FROM insis_gen_cfg_v10.cfg_nl_product cnp
        JOIN insis_gen_cfg_v10.cfg_nl_objects cno ON cnp.product_link_id = cno.product_link_id
        JOIN insis_gen_cfg_v10.cpr_objects co ON cno.object_type_cpr_id = co.object_type_cpr_id
        JOIN insis_gen_v10.hst_object_type ot ON co.object_type = ot.id
        LEFT JOIN insis_gen_cfg_v10.cfg_nl_product_text cnpx ON cnpx.product_link_id = cnp.product_link_id
    )
    -- product_text NULL: el LEFT JOIN no encontro nombre (visto en la
    -- practica, product_code=1/"Additional Equipment") -- sin nombre no
    -- sirve para un selector, se descarta en vez de mostrar un item vacio.
    WHERE product_text IS NOT NULL
      AND UPPER(product_text) NOT LIKE '%NO USAR%'
      AND UPPER(subtipo) NOT LIKE '%NO USAR%'
    ORDER BY product_code, subtipo
"""


def list_products(env_key: str) -> list[dict]:
    """Catalogo {product_code, product_text, subtipo} para los selectores de
    Producto/Subtipo. No toca la tabla policy: es puro catalogo/configuracion,
    asi que es una consulta barata sin importar el ambiente.
    """
    with get_connection(env_key) as conn:
        with conn.cursor() as cur:
            cur.execute(_PRODUCTS_SQL)
            cols = [d[0].lower() for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    for r in rows:
        r["product_code"] = _to_native(r["product_code"])
    return rows


# Cada familia define el filtro de policy_state y si necesita resolver un
# quote_id (Cot_Ind se descarga por quote_id, no por policy_id/annex_id).
FAMILIES = {
    "Car_Ind": {
        "label": "Car_Ind (carátula individual, emitida)",
        "state_filter": "policy_state >= 1",
        "needs_quote": False,
    },
    "Cot_Ind": {
        "label": "Cot_Ind (cotización)",
        "state_filter": "policy_state = -4",
        "needs_quote": True,
    },
}

_POLICY_CANDIDATES_SQL_TMPL = """
    SELECT policy_id, policy_no, policy_lot, insr_type, policy_state
    FROM insis_gen_v10.policy
    WHERE {state_filter}
      AND insr_type IN ({codes})
    ORDER BY policy_id DESC
    FETCH FIRST :pool ROWS ONLY
"""

_QUOTE_ID_SQL = """
    SELECT quote_id
    FROM insis_gen_v10.policy_eng_policies
    WHERE policy_id = :p AND quote_id IS NOT NULL
    ORDER BY quote_id DESC FETCH FIRST 1 ROWS ONLY
"""


def list_policy_cases(env_key: str, family: str, product_codes: list[int], limit: int = 5) -> list[dict]:
    """Hasta `limit` casos para Car_Ind/Cot_Ind: polizas de insis_gen_v10.policy
    filtradas por el estado de la familia y por producto (insr_type IN codes).

    `product_codes` trae mas de un valor cuando el usuario eligio "Todos" los
    subtipos de un producto (varios product_code para el mismo product_text).
    """
    if family not in FAMILIES:
        raise ValueError(f"Familia desconocida: {family}")
    if not product_codes:
        raise ValueError("Falta elegir un producto.")
    spec = FAMILIES[family]
    limit = max(1, min(int(limit or 5), 30))
    # Cot_Ind descarta candidatos sin quote_id, asi que pide de entrada un
    # pool mas grande que `limit` para no tener que volver a golpear la base.
    pool = limit * 4 if spec["needs_quote"] else limit

    placeholders = ", ".join(f":c{i}" for i in range(len(product_codes)))
    binds = {f"c{i}": code for i, code in enumerate(product_codes)}
    binds["pool"] = pool
    sql = _POLICY_CANDIDATES_SQL_TMPL.format(state_filter=spec["state_filter"], codes=placeholders)
    cases: list[dict] = []
    with get_connection(env_key) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, binds)
            candidates = cur.fetchall()
            for policy_id, policy_no, policy_lot, insr_type, policy_state in candidates:
                if len(cases) >= limit:
                    break
                policy_id = _to_native(policy_id)
                base = {
                    "policy_id": policy_id,
                    "policy_no": policy_no,
                    "policy_lot": policy_lot,
                    "insr_type": _to_native(insr_type),
                    "policy_state": _to_native(policy_state),
                }
                if spec["needs_quote"]:
                    cur.execute(_QUOTE_ID_SQL, {"p": policy_id})
                    row = cur.fetchone()
                    if not row:
                        continue  # sin quote_id, Cot_Ind no se puede descargar
                    quote_id = _to_native(row[0])
                    cases.append({
                        **base, "no_poliza": policy_no,
                        "annex_id": None, "quote_id": quote_id,
                        "params": [quote_id],
                    })
                else:
                    cases.append({
                        **base, "no_poliza": policy_no,
                        "annex_id": 0, "quote_id": None,
                        "params": [policy_id, 0],
                    })
    return cases
