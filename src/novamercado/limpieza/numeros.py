"""Interpretación de números que llegan como texto o con separadores. Funciones puras."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

_SOLO_DIGITOS = re.compile(r"^[+-]?\d+$")
_NUMERO = re.compile(r"^[+-]?[\d.,]+$")


@dataclass(frozen=True)
class Numero:
    valor: float | None   # None = no interpretable
    reformateado: bool    # llegó en una forma distinta a un número JSON entero
    nota: str = ""


def interpretar_numero(v: object) -> Numero:
    """Convierte ``v`` a número.

    Reglas para texto (los valores del contrato son cantidades en unidades):

    * ``"12"`` -> 12.
    * Con ``.`` y ``,`` a la vez, el último es el separador decimal (``"1.234,5"`` -> 1234.5).
    * Un separador seguido de exactamente 3 dígitos, o repetido, es separador de miles
      (``"1,234"`` -> 1234, ``"1.234.567"`` -> 1234567).
    * En otro caso el separador es decimal (``"812,0"`` -> 812.0, ``"2.5"`` -> 2.5).
    """
    if v is None or isinstance(v, bool):
        return Numero(None, False)
    if isinstance(v, int):
        return Numero(float(v), False)
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return Numero(None, False)
        return Numero(v, True, "decimal")
    if not isinstance(v, str):
        return Numero(None, False)
    s = v.strip().replace(" ", "").replace(" ", "").replace("_", "")
    if not s:
        return Numero(None, False)
    if _SOLO_DIGITOS.match(s):
        return Numero(float(int(s)), True, "texto")
    if not _NUMERO.match(s):
        return Numero(None, False)
    signo = -1.0 if s.startswith("-") else 1.0
    s = s.lstrip("+-")
    if "." in s and "," in s:
        dec = "." if s.rfind(".") > s.rfind(",") else ","
        mil = "," if dec == "." else "."
        partes = s.split(dec)
        if len(partes) != 2:
            return Numero(None, False)
        entero = partes[0].replace(mil, "")
        if not entero.isdigit() or not partes[1].isdigit():
            return Numero(None, False)
        return Numero(signo * float(f"{entero}.{partes[1]}"), True, "separadores")
    sep = "." if "." in s else ","
    partes = s.split(sep)
    if any(p == "" for p in partes) or not all(p.isdigit() for p in partes):
        return Numero(None, False)
    if len(partes) > 2 or len(partes[1]) == 3:
        # separador de miles: todos los grupos después del primero deben tener 3 dígitos
        if all(len(p) == 3 for p in partes[1:]) and 1 <= len(partes[0]) <= 3:
            return Numero(signo * float("".join(partes)), True, "miles")
        if len(partes) > 2:
            return Numero(None, False)
    return Numero(signo * float(f"{partes[0]}.{partes[1]}"), True, "decimal")


def es_entero(x: float) -> bool:
    return abs(x - round(x)) < 1e-9
