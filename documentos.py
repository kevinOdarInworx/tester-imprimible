"""Documentos (imprimibles) disponibles por poliza, para la pestaña
"Descargar documentos".

Port de generar-imprimibles/imprimibles.py (compute_imprimibles /
compute_for_engagement; reglas en generar-imprimibles/docs/imprimibles.md),
con estos cambios:
- Endosos desde annex_details_view (list_annexes), con tipificacion, igual
  que la tarjeta "Endosos" de "Buscar polizas".
- SIN_04 se ofrece descargable: su jrxml solo pide claim_id. El resto de los
  SIN_0X pide string_param2/3 que no estan mapeados y sigue sin descarga.
- Cada recibo dice si tiene todas las cuotas pagadas: la version neteada de
  Rec_Pag (CALCULUS_V5_VIEW descarta paid_status 'Y') lo devuelve en blanco.
"""
from __future__ import annotations

from decimal import Decimal

from db import get_connection
from resolver import list_annexes, lookup_policy, resolve_engagement, resolve_quote

# report -> (label, grupo). Grupos en el orden en que se muestran.
GRUPOS = ["Carátula", "Cotización", "Endosos", "Recibos", "Siniestros"]
REPORTS = {
    "Car_Ind": ("Carátula de póliza", "Carátula"),
    "Car_Mul": ("Carátula Multinciso (Daños)", "Carátula"),
    "Car_Mul_Autos": ("Carátula Multinciso Autos", "Carátula"),
    "Car_Mul_Autos_Maestra": ("Carátula Multinciso Autos (Póliza maestra)", "Carátula"),
    "Cot_Ind": ("Cotización", "Cotización"),
    "Cot_Mul": ("Cotización Multinciso (Daños)", "Cotización"),
    "Cot_Mul_Autos": ("Cotización Multinciso Autos", "Cotización"),
    "End_Ind": ("Carátula de endoso", "Endosos"),
    "Rec_Pag": ("Recibo de Pago / Nota de crédito", "Recibos"),
    "SIN_04": ("Siniestro (SIN_04)", "Siniestros"),
}


def _native(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return value


def _rows(cur, sql, binds):
    cur.execute(sql, binds)
    cols = [d[0].lower() for d in cur.description]
    return [{c: _native(v) for c, v in zip(cols, r)} for r in cur.fetchall()]


def _scalar(cur, sql, binds):
    cur.execute(sql, binds)
    row = cur.fetchone()
    return _native(row[0]) if row else None


def _product(insr_type) -> str:
    return "autos" if str(insr_type or "").startswith("1") else "daños"


def _count_incisos_danios(cur, policy_id) -> int:
    n = _scalar(cur, "SELECT COUNT(*) FROM INSOR_GDS.INCISOS_VIEW WHERE POLICY_ID = :p", {"p": policy_id})
    return int(n or 0)


def _engagement_of(cur, policy_id):
    rows = _rows(
        cur,
        "SELECT engagement_id, eng_pol_type, master_policy_id "
        "FROM insis_gen_v10.policy_eng_policies WHERE policy_id = :p "
        "ORDER BY engagement_id DESC FETCH FIRST 1 ROWS ONLY",
        {"p": policy_id},
    )
    return rows[0] if rows else None


def _masters(cur, engagement_id):
    """Polizas MASTER en estado 0 del engagement, con su nro de incisos."""
    return _rows(
        cur,
        "SELECT pep.policy_id, po.policy_no, po.policy_lot, "
        "  (SELECT COUNT(*) FROM insis_gen_v10.policy_eng_policies d "
        "     WHERE d.engagement_id = pep.engagement_id "
        "       AND d.master_policy_id = pep.policy_id) AS incisos "
        "FROM insis_gen_v10.policy_eng_policies pep "
        "JOIN insis_gen_v10.policy po ON po.policy_id = pep.policy_id "
        "WHERE pep.engagement_id = :e AND pep.eng_pol_type = 'MASTER' "
        "  AND po.policy_state = 0 "
        "ORDER BY pep.policy_id",
        {"e": engagement_id},
    )


def _quote_id(cur, policy_id):
    return _scalar(
        cur,
        "SELECT quote_id FROM insis_gen_v10.policy_eng_policies "
        "WHERE policy_id = :p AND quote_id IS NOT NULL "
        "ORDER BY quote_id DESC FETCH FIRST 1 ROWS ONLY",
        {"p": policy_id},
    )


def _receipts(cur, policy_id, policy_no):
    """Recibos/notas de credito del agreement (= policy_no), con el annex del
    endoso al que corresponden (0/NULL = emision) y si la version neteada de
    Rec_Pag lo devuelve en blanco.

    `en_blanco` replica el filtro de CALCULUS_V5_VIEW: una transaccion entra
    si no esta pagada (paid_status N/P) o si es negativa con saldo 0 y razon
    de endoso distinta de 11 (cambio de forma de pago). Un doc sin ninguna
    transaccion que entre sale en blanco. Antes se miraba solo paid_status =
    'Y' y una nota de credito pagada (PREMIUM-244217 en PROD, -116) se
    avisaba en blanco aunque se genera bien."""
    if not policy_no:
        return []
    return _rows(
        cur,
        "SELECT doc_number, annex, MIN(oculta) AS en_blanco FROM ("
        "  SELECT blc_doc.doc_number, blc_tr.annex, "
        "    CASE WHEN blc_tr.paid_status IN ('N', 'P') THEN 0 "
        "         WHEN blc_tr.open_balance = 0 AND blc_tr.amount < 0 AND EXISTS ("
        "           SELECT 1 FROM insis_gen_v10.gen_annex_reason ar "
        "           WHERE ar.policy_id = :pid AND TO_CHAR(ar.annex_id) = blc_tr.annex "
        "             AND ar.annex_reason != '11') THEN 0 "
        "         ELSE 1 END AS oculta "
        "  FROM insis_gen_blc_v10.blc_items blc_it "
        "  JOIN insis_gen_blc_v10.blc_transactions blc_tr ON blc_tr.item_id = blc_it.item_id "
        "  JOIN insis_gen_blc_v10.blc_documents blc_doc ON blc_tr.doc_id = blc_doc.doc_id "
        "  JOIN insis_gen_blc_v10.blc_installments blc_ins ON blc_ins.transaction_id = blc_tr.transaction_id "
        "  WHERE blc_it.agreement = :ag AND blc_it.item_type = 'POLICY' "
        "    AND blc_doc.doc_number IS NOT NULL"
        ") GROUP BY doc_number, annex "
        "ORDER BY doc_number",
        {"ag": policy_no, "pid": policy_id},
    )


def _claims(cur, policy_id):
    return _rows(
        cur,
        "SELECT claim_id, claim_regid, claim_type FROM insis_gen_v10.claim "
        "WHERE policy_id = :p ORDER BY claim_id",
        {"p": policy_id},
    )


def _item(report, params, subtitulo=None, nota=None, downloadable=True, label=None):
    default_label, grupo = REPORTS[report]
    return {
        "report": report,
        "label": label or default_label,
        "grupo": grupo,
        "params": params,
        "subtitulo": subtitulo,
        "nota": nota,
        "downloadable": downloadable,
    }


def _maestra_items(engagement_id, masters):
    return [
        _item("Car_Mul_Autos_Maestra", [engagement_id, m["policy_id"]],
              subtitulo=f"Maestra {m.get('policy_no') or m['policy_id']} ({m.get('incisos', 0)} incisos)")
        for m in masters
    ]


def compute_for_engagement(env_key: str, engagement_id) -> dict:
    """Engagement de autos multinciso: Car_Mul_Autos + una maestra por MASTER en estado 0."""
    engagement_id = _native(engagement_id)
    with get_connection(env_key) as conn:
        with conn.cursor() as cur:
            masters = _masters(cur, engagement_id)
    items = [_item("Car_Mul_Autos", [engagement_id])] + _maestra_items(engagement_id, masters)
    return {"producto": "autos", "engagement_id": engagement_id, "masters": len(masters), "items": items}


def compute_documents(env_key: str, policy: dict) -> dict:
    policy_id = policy["policy_id"]
    policy_no = policy.get("policy_no")
    producto = _product(policy.get("insr_type"))
    estado = policy.get("policy_state")
    emitida = estado is not None and estado >= 0
    cotizacion = estado == -4

    endosos = []
    if emitida:
        for a in list_annexes(env_key, policy_id):
            if not a["annex_id"]:
                continue  # annex 0 es la emision, ya va aparte
            # annex_no = letra del endoso + "-" + numero (annex_details_view).
            # Puede venir sin letra ("-104767") o sin numero ("C-": visto en
            # PROD en cancelaciones por falta de pago, varias en la misma
            # poliza) — sin numero se agrega el annex_id para distinguirlas.
            letra, _, numero = (a["annex_no"] or "").partition("-")
            if numero:
                annex_no = a["annex_no"]
            elif a["annex_no"]:
                annex_no = f"{a['annex_no']} (annex_id {a['annex_id']})"
            else:
                annex_no = f"annex_id {a['annex_id']}"
            partes = [annex_no, a["tipo_endoso"], a["tipificacion"]]
            endosos.append({
                "annex_id": a["annex_id"],
                "sub": " — ".join(p for p in partes if p),
                "version": f"{annex_no} · Endoso {letra}" if letra else annex_no,
            })

    items = []
    masters = []
    engagement_id = None
    with get_connection(env_key) as conn:
        with conn.cursor() as cur:
            autos_multinciso = False
            eng_tipo = None
            if producto == "autos":
                eng = _engagement_of(cur, policy_id)
                engagement_id = eng["engagement_id"] if eng else None
                eng_tipo = eng["eng_pol_type"] if eng else None
                if engagement_id:
                    masters = _masters(cur, engagement_id)
                    autos_multinciso = bool(masters)
                incisos = len(masters) if autos_multinciso else 0
                multinciso = autos_multinciso
            else:
                incisos = _count_incisos_danios(cur, policy_id)
                multinciso = incisos > 1

            # Carátula: una por la emision y una por cada endoso, salvo autos
            # multinciso (Car_Mul_Autos* no reciben annex_id).
            if emitida:
                if autos_multinciso:
                    items.append(_item("Car_Mul_Autos", [engagement_id]))
                    if eng_tipo == "MASTER":
                        items.append(_item("Car_Mul_Autos_Maestra", [engagement_id, policy_id],
                                           subtitulo=f"Maestra {policy_no or policy_id}"))
                    else:
                        items.extend(_maestra_items(engagement_id, masters))
                else:
                    report = "Car_Mul" if multinciso else "Car_Ind"
                    items.append(_item(report, [policy_id, 0], subtitulo="Emisión"))
                    for e in endosos:
                        items.append(_item(report, [policy_id, e["annex_id"]], subtitulo=e["sub"]))

            if cotizacion:
                quote_id = _quote_id(cur, policy_id)
                nota = None if quote_id is not None else "No se encontró quote_id para esta póliza"
                report = "Cot_Mul_Autos" if autos_multinciso else "Cot_Mul" if multinciso else "Cot_Ind"
                items.append(_item(report, [quote_id], nota=nota, downloadable=quote_id is not None))

            for e in endosos:
                items.append(_item("End_Ind", [policy_id, e["annex_id"]], subtitulo=e["sub"]))

            # Recibos agrupados por version: emision y despues cada endoso, en
            # ese orden. Una version sin recibo igual se lista (placeholder)
            # para que se vea a que endoso le falta — p.ej. los endosos B no
            # mueven prima y no generan recibo.
            # blc_transactions.annex es texto ("0", "3000104377"): sin
            # convertir, "0" no cuenta como emision y no matchea los annex_id
            # (int) de los endosos. generar-imprimibles tiene el mismo bug.
            recibos_por_annex: dict[int, list] = {}
            for r in _receipts(cur, policy_id, policy_no):
                recibos_por_annex.setdefault(int(r["annex"] or 0), []).append(r)
            versiones = []
            if emitida:
                versiones = [(0, "Endoso 0 (Emisión)")] + [(e["annex_id"], e["version"]) for e in endosos]
            conocidos = {annex for annex, _ in versiones}
            # Recibos de un annex que no esta en la lista (p.ej. endoso
            # cancelado, que annex_details_view excluye) van al final.
            versiones += [(annex, "Endoso 0 (Emisión)" if not annex else f"annex_id {annex}")
                          for annex in sorted(recibos_por_annex) if annex not in conocidos]
            for annex, version in versiones:
                recibos = recibos_por_annex.get(annex, [])
                if not recibos:
                    items.append({**_item("Rec_Pag", [], subtitulo=version, downloadable=False),
                                  "placeholder": True, "annex_id": annex})
                for r in recibos:
                    doc = r["doc_number"]
                    nota = ("Todas sus cuotas están pagadas: la versión neteada de Rec_Pag lo devuelve en blanco"
                            if r["en_blanco"] else None)
                    items.append({**_item("Rec_Pag", [doc], subtitulo=f"{version} — {doc}", nota=nota),
                                  "annex_id": annex})

            for c in _claims(cur, policy_id):
                sub = f"{c['claim_regid'] or c['claim_id']} - {c['claim_type'] or ''}".strip(" -")
                items.append(_item("SIN_04", [c["claim_id"]], subtitulo=sub,
                                   nota="Los demás SIN_0X piden string_param2/3 sin mapear"))

    return {
        "policy_id": policy_id,
        "policy_no": policy_no,
        "producto": producto,
        "estado": estado,
        "emitida": emitida,
        "cotizacion": cotizacion,
        "incisos": incisos,
        "engagement_id": engagement_id,
        "masters": len(masters),
        "items": items,
    }


def resolve(env_key: str, raw: str) -> dict:
    """Mismo orden que /resolve de generar-imprimibles: poliza directa ->
    engagement_id (calcula sus documentos directo) -> quote_id. Solo calcula
    documentos si queda una sola poliza; con varias, el frontend pide los de
    la que se elija (/doc_for_policy)."""
    result = lookup_policy(env_key, raw)
    matches = result["matches"]
    if not matches:
        eng_id = resolve_engagement(env_key, raw)
        if eng_id:
            result["engagement_id"] = _native(eng_id)
            result["documentos"] = compute_for_engagement(env_key, eng_id)
            return result
        policy_ids = resolve_quote(env_key, raw)
        if policy_ids:
            result["quote_id"] = raw
            for pid in policy_ids:
                matches.extend(lookup_policy(env_key, str(pid))["matches"])
    if len(matches) == 1:
        result["documentos"] = compute_documents(env_key, matches[0])
    return result


def blank_hint(report: str, params: list) -> str:
    """Por que un PDF puede venir en blanco (200 OK pero sin texto: la vista
    del reporte no devolvio filas)."""
    base = ("La vista no devolvió filas: el parámetro no existe en este ambiente, o la póliza "
            "es muy reciente y todavía no llegó a RAWDB (la réplica tarda ~3-10 s).")
    if report == "Rec_Pag":
        return base + " En recibos, también pasa si todas las cuotas están pagadas (versión neteada)."
    if report in ("Car_Ind", "Car_Mul") and len(params) > 1 and str(params[1]) not in ("", "0"):
        # CAR_IND_DANIOS_VIEW (desplegada en PROD el 06/10) solo arma filas de
        # endoso para estas razones; el resto (p.ej. 55 aumento de suma
        # asegurada, 38 endoso B libre) sale en blanco.
        return ("La carátula de un endoso solo sale para algunos motivos: cambio de agente, de forma "
                "de pago, de cliente/asegurado o de domicilio, corrección de nombre/RFC y asegurado "
                "alterno. Para otros (p.ej. aumento de suma asegurada o endoso B libre) sale en blanco. "
                + base)
    return base
