"""Compara dos PDFs por su texto extraido, para demostrar que dos versiones
de un mismo imprimible (p.ej. Car_Mul vs Car_Mul_V3) producen el mismo
contenido aunque una sea mucho mas rapida que la otra.

No se compara el PDF byte a byte (siempre va a diferir por metadata/fecha de
generacion): se extrae el texto de cada pagina con pdfplumber y se diffea con
difflib, igual que el diff de SQL de versiones-vistas-imprimibles. Cada linea
se normaliza a espacios simples antes de comparar: pdfplumber puede variar
levemente los espacios entre palabras segun el layout exacto de cada render,
y eso no es una diferencia de contenido real.
"""
from __future__ import annotations

import difflib
import io
import re

import pdfplumber


def extract_lines(pdf_bytes: bytes) -> list[str]:
    lines: list[str] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            lines.extend(text.split("\n"))
    return lines


# Jasper imprime el numero de pagina en el pie aunque la vista no devuelva
# filas: un PDF que solo dice "Pág. 1 de 1" sigue estando en blanco (visto con
# Car_Ind de un endoso cuyo motivo no cubre la carátula por endoso).
_PAGE_NUMBER = re.compile(r"(p[áa]g(ina)?\.?\s*)?\d+(\s*(de|/)\s*\d+)?", re.IGNORECASE)


def pdf_stats(pdf_bytes: bytes) -> tuple[int, int]:
    """(paginas, lineas con contenido, sin contar el numero de pagina). 0
    lineas = PDF en blanco: OIC responde 200 igual cuando la vista del reporte
    no devuelve filas."""
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        lines = sum(
            1
            for page in pdf.pages
            for line in (page.extract_text() or "").split("\n")
            if line.strip() and not _PAGE_NUMBER.fullmatch(_norm(line))
        )
        return len(pdf.pages), lines


def _norm(line: str) -> str:
    return " ".join(line.split())


def compare_pdfs(bytes_a: bytes, bytes_b: bytes, label_a: str, label_b: str) -> dict:
    lines_a = [_norm(l) for l in extract_lines(bytes_a)]
    lines_b = [_norm(l) for l in extract_lines(bytes_b)]
    # Lineas en blanco no aportan a la comparacion y varian facil entre renders.
    lines_a = [l for l in lines_a if l]
    lines_b = [l for l in lines_b if l]

    identical = lines_a == lines_b
    sm = difflib.SequenceMatcher(a=lines_a, b=lines_b, autojunk=False)
    result = {
        "identical": identical,
        "similarity": round(sm.ratio() * 100, 1),
        "lines_a": len(lines_a),
        "lines_b": len(lines_b),
    }
    if identical:
        return result

    hd = difflib.HtmlDiff(tabsize=4)
    table = hd.make_table(lines_a, lines_b, fromdesc=label_a, todesc=label_b, context=True, numlines=2)
    # difflib usa &nbsp; para preservar espacios; con texto largo eso impide
    # que el navegador corte la linea al ajustar el ancho de columna.
    result["html"] = table.replace("&nbsp;", " ")
    return result
