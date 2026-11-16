"""Normalización de textos: sin acentos, minúsculas, separadores uniformes. Funciones puras."""
from __future__ import annotations

import re
import unicodedata

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NO_ALNUM = re.compile(r"[^a-z0-9]+")


def sin_acentos(texto: str) -> str:
    nfkd = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def normalizar_clave(texto: object) -> str:
    """'TiendaID' -> 'tienda_id'; ' En Promoción ' -> 'en_promocion'; 'familia-id' -> 'familia_id'."""
    s = str(texto).strip()
    s = _CAMEL.sub("_", s)
    s = sin_acentos(s).lower()
    return _NO_ALNUM.sub("_", s).strip("_")


def compactar(texto: object) -> str:
    """Forma más agresiva para comparar: sin acentos, minúsculas y sin separadores ('Frutas y Verduras' ->
    'frutasyverduras')."""
    return _NO_ALNUM.sub("", sin_acentos(str(texto)).lower())
