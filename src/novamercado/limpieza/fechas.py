"""Interpretación de fechas en formatos distintos a AAAA-MM-DD. Funciones puras (sin reloj del sistema)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from novamercado.textos import sin_acentos

_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_ISO_HORA = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})[tT ]\d{1,2}:\d{2}(:\d{2}(\.\d+)?)?([zZ]|[+-]\d{2}:?\d{2})?$")
_YMD = re.compile(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$")
_DMY = re.compile(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4}|\d{2})$")
_COMPACTA = re.compile(r"^(\d{4})(\d{2})(\d{2})$")
_TEXTO = re.compile(r"^(\d{1,2})\s*(?:de\s+)?([a-z]+)\.?\s*(?:de\s+|del\s+)?(\d{4})$")

MESES = {
    "enero": 1, "ene": 1, "january": 1, "jan": 1,
    "febrero": 2, "feb": 2, "february": 2,
    "marzo": 3, "mar": 3, "march": 3,
    "abril": 4, "abr": 4, "april": 4, "apr": 4,
    "mayo": 5, "may": 5,
    "junio": 6, "jun": 6, "june": 6,
    "julio": 7, "jul": 7, "july": 7,
    "agosto": 8, "ago": 8, "august": 8, "aug": 8,
    "septiembre": 9, "setiembre": 9, "sep": 9, "sept": 9, "set": 9, "september": 9,
    "octubre": 10, "oct": 10, "october": 10,
    "noviembre": 11, "nov": 11, "november": 11,
    "diciembre": 12, "dic": 12, "december": 12, "dec": 12,
}


@dataclass(frozen=True)
class Fecha:
    valor: date | None                 # None = no interpretable
    formato_contrato: bool = False      # ya venía como AAAA-MM-DD
    ambigua: bool = False
    alternativas: tuple[date, ...] = field(default_factory=tuple)
    nota: str = ""


def _crear(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _anio(y: str) -> int:
    n = int(y)
    return 2000 + n if len(y) == 2 else n


def interpretar_fecha(v: object, referencia: date | None = None) -> Fecha:
    """Interpreta ``v`` como fecha.

    ``referencia`` (fecha esperada de operación) sólo se usa para resolver ambigüedades día/mes:
    se elige la interpretación más cercana a ella; sin referencia se prefiere día/mes (uso local).
    """
    if isinstance(v, bool) or v is None:
        return Fecha(None)
    if isinstance(v, int):
        v = str(v)
    if not isinstance(v, str):
        return Fecha(None)
    s = sin_acentos(v.strip()).lower()
    if not s:
        return Fecha(None)

    if m := _ISO.match(s):
        f = _crear(int(m[1]), int(m[2]), int(m[3]))
        return Fecha(f, formato_contrato=f is not None and s == v)
    if m := _ISO_HORA.match(s):
        return Fecha(_crear(int(m[1]), int(m[2]), int(m[3])), nota="fecha con hora")
    if m := _YMD.match(s):
        return Fecha(_crear(int(m[1]), int(m[2]), int(m[3])), nota="año-mes-día")
    if m := _COMPACTA.match(s):
        return Fecha(_crear(int(m[1]), int(m[2]), int(m[3])), nota="AAAAMMDD")
    if m := _TEXTO.match(s):
        mes = MESES.get(m[2])
        return Fecha(_crear(int(m[3]), mes, int(m[1])) if mes else None, nota="mes en texto")
    if m := _DMY.match(s):
        a, b, y = int(m[1]), int(m[2]), _anio(m[3])
        dia_mes = _crear(y, b, a)    # dd/mm/aaaa (uso en Guatemala)
        mes_dia = _crear(y, a, b)    # mm/dd/aaaa
        if dia_mes and mes_dia and dia_mes != mes_dia:
            elegida = dia_mes
            if referencia is not None and abs((mes_dia - referencia).days) < abs((dia_mes - referencia).days):
                elegida = mes_dia
            return Fecha(elegida, ambigua=True, alternativas=(dia_mes, mes_dia), nota="día/mes ambiguo")
        f = dia_mes or mes_dia
        nota = "día/mes/año" if f is dia_mes else "mes/día/año"
        return Fecha(f, nota=nota)
    return Fecha(None)


def dias(n: int) -> timedelta:
    return timedelta(days=n)
