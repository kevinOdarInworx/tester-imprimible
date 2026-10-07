"""Normalizacion de texto SQL para comparar vistas: quita comentarios y
espacios de mas, sin dependencia de la base ni del repo.

Copiado tal cual de versiones-vistas-imprimibles/sqltext.py (pestaña "Vistas
del pase"). Si se corrige algo aca, revisar si aplica alla tambien.

Se usa tanto para el diff como para el hash de agrupamiento por version, de
modo que dos fuentes que solo difieren en comentarios cuenten como iguales.
"""
from __future__ import annotations

import hashlib


def strip_comments(sql: str) -> str:
    """Quita comentarios `-- ...` y `/* ... */`, respetando literales '...',
    identificadores "..." y hints de optimizador (`/*+ ... */`, que si afectan
    el plan y se conservan). Los saltos de linea se preservan."""
    out: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        c = sql[i]
        if c == "'" or c == '"':
            # literal / identificador entrecomillado; '' o "" es una comilla escapada
            j = i + 1
            while j < n:
                if sql[j] == c:
                    if j + 1 < n and sql[j + 1] == c:
                        j += 2
                        continue
                    break
                j += 1
            out.append(sql[i : j + 1])
            i = j + 1
        elif sql.startswith("--", i):
            j = sql.find("\n", i)
            i = n if j == -1 else j  # deja el \n para no pegar lineas
        elif sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            end = n if j == -1 else j + 2
            if sql.startswith("/*+", i):
                out.append(sql[i:end])
            else:
                out.append("\n" * sql.count("\n", i, end))  # conserva el conteo de lineas
            i = end
        else:
            out.append(c)
            i += 1
    return "".join(out)


def comparable_lines(sql: str) -> list[str]:
    """Lineas del SQL sin comentarios, con espacios colapsados y sin lineas
    vacias: la forma sobre la que se compara y se calcula el hash.

    Tambien descarta un `;` terminador al final: en los .sql del repo puede
    quedar seguido de comentarios (bitacora de cambios), y una vez quitados
    estos ya no es "el ultimo caracter" para repo_views._extract_select.
    """
    text = strip_comments((sql or "").replace("\r\n", "\n").replace("\r", "\n"))
    text = text.rstrip()
    while text.endswith(";"):
        text = text[:-1].rstrip()
    lines = (" ".join(line.split()) for line in text.split("\n"))
    return [line for line in lines if line]


def comparable_md5(sql: str) -> str:
    """Hash del SQL comparable (ignora comentarios, espacios y lineas vacias)."""
    return hashlib.md5("\n".join(comparable_lines(sql)).encode("utf-8", "ignore")).hexdigest()
