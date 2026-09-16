"""Descubrimiento de casos de prueba (polizas reales) para comparar Car_Mul vs
Car_Mul_V3, consultando exclusivamente INSOR_GDS.CAR_MUL_VIEW (la vista que
usa el imprimible, ver Car_Mul.jrxml / Car_Mul_V3/Car_Mul.jrxml — ambas
lecturas devuelven POLICY_ID = string_param1, ANNEX_ID = string_param2).

Solo se devuelven casos con CANT_UBICACIONES > 1: "Mul" (Car_Mul, Cot_Mul,
etc.) es justamente el imprimible multinciso, y con 0 o 1 ubicacion no se
ejercita el fan-out por inciso que Car_Mul_V3 optimiza — no serian casos
utiles para demostrar ni la equivalencia ni la mejora de performance.

CAR_MUL_VIEW "no tiene filtro propio" (ver
INSOR/shared_workspace/PRINTOUTS/FASE_1/Car_Mul_V3/README.md): el costo caro
de la version ORIGINAL de Car_Mul.jrxml (el LISTAGG sobre toda AGENTS_VIEW,
~19-30s) no depende de que tan grande sea la poliza consultada, es un costo
de golpear la vista. Por eso NO se hace ningun SELECT sin WHERE POLICY_ID
sobre ella (seria altisimo costo/riesgo en PROD): los candidatos a
POLICY_ID salen de insis_gen_v10.policy (tabla base, filtro barato por
policy_state/insr_type), y recien ahi se consulta CAR_MUL_VIEW, una vez por
candidato y siempre filtrada por POLICY_ID, hasta juntar los casos pedidos.
Como cada golpe a la vista puede tardar bastante, el limite de casos por
defecto es chico.
"""
from __future__ import annotations

from db import get_connection

_CANDIDATES_SQL = """
    SELECT policy_id
    FROM insis_gen_v10.policy
    WHERE policy_state >= 0
      AND insr_type NOT LIKE '1%'
    ORDER BY policy_id DESC
    FETCH FIRST :pool ROWS ONLY
"""

_VIEW_SQL = """
    SELECT POLICY_ID, ANNEX_ID, NO_POLIZA, NOMBRE_CLIENTE, CANT_UBICACIONES
    FROM INSOR_GDS.CAR_MUL_VIEW
    WHERE POLICY_ID = :p
"""

# Tope dursimo de candidatos a probar contra la vista, aunque no se junten
# los casos pedidos (para no dejar la busqueda corriendo indefinidamente si
# la mayoria de los candidatos no tiene filas en CAR_MUL_VIEW).
_MAX_PROBES = 60


def _to_native(value):
    """Decimal/None de oracledb -> int/None, para que sea JSON-serializable."""
    if value is None:
        return None
    return int(value)


def list_cases(env_key: str, limit: int = 5) -> list[dict]:
    """Hasta `limit` casos (POLICY_ID, ANNEX_ID) reales, leidos de CAR_MUL_VIEW."""
    limit = max(1, min(int(limit or 5), 30))
    # El filtro de multinciso (cant_ubicaciones > 1) descarta bastantes
    # candidatos, asi que conviene arrancar pidiendo el pool completo
    # permitido en vez de un multiplo chico de `limit`.
    pool = _MAX_PROBES

    cases: list[dict] = []
    with get_connection(env_key) as conn:
        with conn.cursor() as cur:
            cur.execute(_CANDIDATES_SQL, {"pool": pool})
            candidates = [r[0] for r in cur.fetchall()]

            probed = 0
            for policy_id in candidates:
                if len(cases) >= limit or probed >= _MAX_PROBES:
                    break
                probed += 1
                cur.execute(_VIEW_SQL, {"p": policy_id})
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
                        cases.append(r)

    cases.sort(key=lambda r: (r.get("cant_ubicaciones") or 0), reverse=True)
    return cases[:limit]
