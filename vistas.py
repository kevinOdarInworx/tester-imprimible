"""Pestaña "Vistas del pase": texto de las vistas INSOR en cada ambiente y
diff entre dos versiones.

Extraido de versiones-vistas-imprimibles/views.py. Diferencias:
- Trae varias vistas de un ambiente en una sola consulta (la pestaña arma una
  matriz vista x ambiente), con su LAST_DDL_TIME.
- Si una vista existe con el mismo nombre en mas de un esquema, prefiere
  INSOR_GDS (alla ganaba el primero en orden alfabetico, que es INSOR_DM).
- Usa el pool de conexiones de db.py de esta app.

Se lee TEXT de DBA_VIEWS (fallback ALL_VIEWS): es el SELECT con el que se creo
la vista, sin el ruido de DBMS_METADATA. Se compara sin comentarios ni
espacios de mas (sqltext.py), igual que en versiones-vistas-imprimibles.
"""
from __future__ import annotations

import difflib

from db import get_connection
from sqltext import comparable_lines, comparable_md5

_SQL = """
SELECT v.owner, v.view_name, v.text, o.last_ddl_time, o.status
FROM {views} v
LEFT JOIN {objects} o
       ON o.owner = v.owner AND o.object_name = v.view_name AND o.object_type = 'VIEW'
WHERE v.view_name IN ({names})
ORDER BY v.view_name,
         CASE v.owner WHEN 'INSOR_GDS' THEN 0 WHEN 'INSOR_DM' THEN 1 ELSE 2 END,
         v.owner
"""


def get_env_sources(env_key: str, view_names: list[str]) -> dict[str, dict]:
    """Por vista: el SELECT en ese ambiente, o {"found": False} si no existe."""
    names = sorted({(n or "").strip().upper() for n in view_names if (n or "").strip()})
    if not names:
        return {}
    binds = {f"v{i}": n for i, n in enumerate(names)}
    placeholders = ", ".join(f":v{i}" for i in range(len(names)))
    with get_connection(env_key) as conn:
        cur = conn.cursor()
        try:
            cur.execute(_SQL.format(views="dba_views", objects="dba_objects", names=placeholders), binds)
        except Exception:
            cur.execute(_SQL.format(views="all_views", objects="all_objects", names=placeholders), binds)
        rows = cur.fetchall()

    result: dict[str, dict] = {n: {"found": False} for n in names}
    for owner, view_name, text, last_ddl, status in rows:
        current = result[view_name]
        if current.get("found"):
            current["other_owners"].append(owner)  # rara ambiguedad: mismo nombre en +1 esquema
            continue
        sql = (text or "").replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
        result[view_name] = {
            "found": True,
            "owner": owner,
            "other_owners": [],
            "last_ddl_time": last_ddl.isoformat(sep=" ", timespec="seconds") if last_ddl else None,
            "status": status,
            "sql": sql,
            "line_count": len(sql.split("\n")),
            "char_count": len(sql),
            "md5": comparable_md5(sql),  # ignora comentarios/espacios: agrupa igual que el diff
        }
    return result


def build_diff(sql_a: str, sql_b: str, label_a: str, label_b: str) -> dict:
    """Diff lado a lado (HTML de difflib) + estadisticas, copiado de
    versiones-vistas-imprimibles/views.py.

    Cada linea se compara ya normalizada (sin comentarios, espacios simples,
    sin lineas vacias): el SQL de estas vistas usa corridas enormes de
    espacios para alinear, y sin normalizar una linea cambiada se pinta como
    una barra solida. Los numeros de linea del diff son los del SQL sin
    comentarios, no los del archivo original.
    """
    cmp_a = comparable_lines(sql_a)
    cmp_b = comparable_lines(sql_b)
    sm = difflib.SequenceMatcher(a=cmp_a, b=cmp_b, autojunk=False)
    identical = cmp_a == cmp_b
    stats = {
        "identical": identical,
        "lines_a": len(cmp_a),
        "lines_b": len(cmp_b),
        "similarity": round(sm.ratio() * 100, 1),
    }
    if identical:
        return {"html": "", "stats": stats}
    # context=True: solo las lineas afectadas y un par de lineas alrededor.
    table = difflib.HtmlDiff(tabsize=4).make_table(
        cmp_a, cmp_b, fromdesc=label_a, todesc=label_b, context=True, numlines=2
    )
    # difflib pone &nbsp; en cada espacio y eso impide cortar lineas largas.
    return {"html": table.replace("&nbsp;", " "), "stats": stats}
