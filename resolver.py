"""Resolver de identificador de poliza (policy_id, policy_no, policy_lot,
engagement_id o quote_id) — el mismo buscador de generar-imprimibles
(policy.py + el orden de /resolve en app.py), sin el motor de elegibilidad de
imprimibles (imprimibles.py): acá solo interesa identificar la poliza, no
calcular que reportes ofrecerle.

De imprimibles.py solo se copiaron `resolve_engagement`/`resolve_quote` (las
dos formas de identificador que no son una columna directa de policy).
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from db import get_connection

# Columnas de policy que devolvemos (agrega policy_lot al listado original de
# generar-imprimibles/policy.py: acá la tabla de resultados lo muestra).
_SELECT_COLUMNS = [
    "policy_id",
    "policy_no",
    "policy_lot",
    "insr_type",
    "policy_state",
    "registration_date",
]


def analyze_input(raw: str) -> dict:
    """Normaliza el texto recibido e infiere que formatos podria representar."""
    value = (raw or "").strip()
    has_slash = "/" in value
    is_numeric = value.isdigit()

    formats: list[str] = []
    if has_slash:
        formats.append("policy_no")
        formats.append("policy_lot (completo)")
    if is_numeric:
        formats.append("policy_id")
        formats.append("policy_lot (3ra parte)")
        formats.append("engagement_id")
        formats.append("quote_id")

    return {
        "value": value,
        "has_slash": has_slash,
        "is_numeric": is_numeric,
        "candidate_formats": formats,
        "valid": bool(value) and (has_slash or is_numeric),
    }


def _build_where(info: dict) -> tuple[str, dict]:
    conds: list[str] = []
    binds: dict[str, str] = {"pval": info["value"]}
    if info["has_slash"]:
        conds.append("policy_no = :pval")
        conds.append("policy_lot = :pval")
    if info["is_numeric"]:
        # policy_id se compara como texto para evitar overflow numerico.
        conds.append("TO_CHAR(policy_id) = :pval")
        # 3ra parte de policy_lot: el ultimo segmento tras la ultima '/'.
        conds.append("REGEXP_SUBSTR(policy_lot, '[^/]+$') = :pval")
    return " OR ".join(conds), binds


def _serialize(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        # oracledb puede devolver Decimal para columnas NUMBER (ya visto en
        # cases.py con CAR_MUL_VIEW) -- jsonify no lo serializa tal cual.
        return int(value) if value == value.to_integral_value() else float(value)
    return value


def lookup_policy(env_key: str, raw: str, limit: int = 50) -> dict:
    """Resuelve la poliza en el ambiente indicado.

    Devuelve {'input': <analisis>, 'matches': [ {col: val}, ... ]}.
    """
    info = analyze_input(raw)
    if not info["valid"]:
        raise ValueError("Ingresa un policy_id, policy_no, policy_lot, engagement_id o quote_id valido.")

    where, binds = _build_where(info)
    cols = ", ".join(_SELECT_COLUMNS)
    sql = (
        f"SELECT {cols} "
        f"FROM insis_gen_v10.policy "
        f"WHERE {where} "
        f"FETCH FIRST {int(limit)} ROWS ONLY"
    )

    with get_connection(env_key) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, binds)
            columns = [c[0].lower() for c in cur.description]
            rows = [
                {col: _serialize(val) for col, val in zip(columns, record)}
                for record in cur.fetchall()
            ]

    return {"input": info, "matches": rows}


def _scalar(cur, sql, binds):
    cur.execute(sql, binds)
    row = cur.fetchone()
    return row[0] if row else None


def resolve_engagement(env_key: str, value: str):
    """Si 'value' es un engagement_id de autos multinciso, lo devuelve; si no, None."""
    v = (value or "").strip()
    if not v.isdigit():
        return None
    with get_connection(env_key) as conn:
        with conn.cursor() as cur:
            return _scalar(
                cur,
                "SELECT DISTINCT engagement_id FROM insis_gen_v10.policy_eng_policies "
                "WHERE engagement_id = :e AND eng_pol_type = 'MASTER' "
                "FETCH FIRST 1 ROWS ONLY",
                {"e": int(v)},
            )


def resolve_quote(env_key: str, value: str) -> list:
    """Si 'value' es un quote_id de cotizacion, devuelve los policy_id asociados
    (inverso de _quote_id en imprimibles.py): policy_eng_policies.quote_id ->
    policy_engagement_quote. Aplica a autos y danios, no solo a autos multinciso.
    """
    v = (value or "").strip()
    if not v.isdigit():
        return []
    with get_connection(env_key) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT pep.policy_id "
                "FROM insis_gen_v10.policy po "
                "JOIN insis_gen_v10.policy_eng_policies pep ON po.policy_id = pep.policy_id "
                "JOIN insis_gen_v10.policy_engagement_quote peq ON pep.quote_id = peq.quote_id "
                "WHERE peq.quote_id = :q",
                {"q": int(v)},
            )
            return [r[0] for r in cur.fetchall()]


def list_engagement_policies(env_key: str, engagement_id) -> list[dict]:
    """Todas las polizas dependientes de un engagement de autos multinciso
    (no solo las MASTER en estado 0 que usa `_masters` en imprimibles.py para
    la caratula — acá interesa listar lo que hay, no decidir elegibilidad de
    un reporte puntual)."""
    with get_connection(env_key) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pep.policy_id, po.policy_no, po.policy_lot, po.insr_type, "
                "  po.policy_state, pep.eng_pol_type, pep.engagement_id "
                "FROM insis_gen_v10.policy_eng_policies pep "
                "JOIN insis_gen_v10.policy po ON po.policy_id = pep.policy_id "
                "WHERE pep.engagement_id = :e "
                "ORDER BY pep.eng_pol_type, pep.policy_id",
                {"e": int(engagement_id)},
            )
            cols = [d[0].lower() for d in cur.description]
            return [
                {col: _serialize(val) for col, val in zip(cols, record)}
                for record in cur.fetchall()
            ]


def engagement_of_policies(env_key: str, policy_ids) -> dict:
    """policy_id -> {engagement_id, eng_pol_type} de las polizas que estan en
    un engagement con alguna MASTER: una DEPENDENT buscada por su policy_no
    tambien dice a que engagement pertenece. Pasarle solo polizas de autos."""
    policy_ids = sorted({int(p) for p in policy_ids if p is not None})
    if not policy_ids:
        return {}
    binds = {f"p{i}": p for i, p in enumerate(policy_ids)}
    with get_connection(env_key) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT pep.policy_id, pep.engagement_id, pep.eng_pol_type "
                "FROM insis_gen_v10.policy_eng_policies pep "
                f"WHERE pep.policy_id IN ({', '.join(':' + k for k in binds)}) "
                "  AND pep.eng_pol_type IN ('MASTER', 'DEPENDENT') "
                "  AND EXISTS (SELECT 1 FROM insis_gen_v10.policy_eng_policies m "
                "              WHERE m.engagement_id = pep.engagement_id AND m.eng_pol_type = 'MASTER') "
                "ORDER BY pep.policy_id, pep.engagement_id",
                binds,
            )
            return {
                _serialize(pid): {"engagement_id": _serialize(eng), "eng_pol_type": tipo}
                for pid, eng, tipo in cur.fetchall()
            }


def list_annexes(env_key: str, policy_id) -> list[dict]:
    """Endosos de una poliza. Sale de insor_gds.annex_details_view y no de
    CONSUTA_DE_ENDOSOS_VIEW (la que usa _endosos en generar-imprimibles):
    esa es un subset de esta y no expone la tipificacion (NAME: el motivo,
    de HT_ANNEX_REASON), que afina al tipo_endoso (HST_ANNEX_TYPE, la
    categoria general)."""
    with get_connection(env_key) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT policy_id, annex_id, no_endoso, tipo_endoso, name "
                "FROM insor_gds.annex_details_view "
                "WHERE policy_id = :p "
                "ORDER BY annex_id",
                {"p": int(policy_id)},
            )
            cols = [d[0].lower() for d in cur.description]
            rows = [
                {col: _serialize(val) for col, val in zip(cols, record)}
                for record in cur.fetchall()
            ]
    return [
        {
            "policy_id": r["policy_id"],
            "annex_id": r["annex_id"],
            "annex_no": r["no_endoso"],
            "tipo_endoso": r["tipo_endoso"],
            "tipificacion": r["name"],
        }
        for r in rows
    ]


def resolve(env_key: str, raw: str) -> dict:
    """Replica el orden de /resolve en generar-imprimibles/app.py: poliza
    directa (policy_id/policy_no/policy_lot) -> engagement_id (autos
    multinciso) -> quote_id (cotizacion, resuelve a policy_id(s))."""
    result = lookup_policy(env_key, raw)
    matches = result["matches"]
    if not matches:
        eng_id = resolve_engagement(env_key, raw)
        if eng_id:
            result["engagement_id"] = eng_id
            matches.extend(list_engagement_policies(env_key, eng_id))
        else:
            policy_ids = resolve_quote(env_key, raw)
            if policy_ids:
                result["quote_id"] = raw
                for pid in policy_ids:
                    matches.extend(lookup_policy(env_key, str(pid))["matches"])
    # Por policy_no/policy_lot/quote_id no se sabe si es de un multinciso de
    # autos: se busca su engagement (el path por engagement_id ya lo trae).
    # Solo autos (insr_type 1xxx): en danios tambien hay filas MASTER en
    # policy_eng_policies (0600201107/0135867/00 en PROD) y no aplica.
    sin_engagement = [m for m in matches if not m.get("engagement_id")
                      and str(m.get("insr_type") or "").startswith("1")]
    if sin_engagement:
        engs = engagement_of_policies(env_key, [m["policy_id"] for m in sin_engagement])
        for m in sin_engagement:
            m.update(engs.get(m["policy_id"], {}))
    return result
