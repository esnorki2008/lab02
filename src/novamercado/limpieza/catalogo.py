"""Resolución de identificadores de tienda y familia contra los catálogos publicados."""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from datetime import date

from novamercado.limpieza.numeros import es_entero, interpretar_numero
from novamercado.textos import compactar

_PREFIJO_DIGITOS = re.compile(r"^(?:t|tda|tienda|store|st|suc|sucursal|id|no|nm|n)?[\s#_\-.:/]*0*(\d+)$")
_PALABRAS_VACIAS = {"y", "de", "del", "la", "las", "los", "el", "e"}


@dataclass(frozen=True)
class Resolucion:
    valor: object | None   # id resuelto; None = desconocido
    regla: str | None      # None = venía exacto; si no, regla aplicada
    detalle: str = ""


def _sin_vacias(texto: str) -> str:
    from novamercado.textos import normalizar_clave
    return "".join(p for p in normalizar_clave(texto).split("_") if p not in _PALABRAS_VACIAS)


class Catalogo:
    """Tiendas y familias válidas, con índices para reconocer otras formas de escribirlas."""

    def __init__(self, tiendas: list[dict], familias: list[dict], similitud_minima: float = 0.80):
        self.tiendas = {int(t["tienda_id"]): t for t in tiendas}
        self.familias = {str(f["familia_id"]): f for f in familias}
        self.similitud_minima = similitud_minima
        self._idx_tienda_nombre: dict[str, int] = {}
        for tid, t in self.tiendas.items():
            if t.get("nombre"):
                nombre = compactar(t["nombre"])
                self._idx_tienda_nombre[nombre] = tid
                corto = nombre.removeprefix("novamercado")
                if corto:
                    self._idx_tienda_nombre.setdefault(corto, tid)
        self._idx_familia: dict[str, str] = {}
        for fid, f in self.familias.items():
            for forma in (fid, f.get("nombre") or fid):
                self._idx_familia.setdefault(compactar(forma), fid)
                self._idx_familia.setdefault(_sin_vacias(forma), fid)

    # ------------------------------------------------------------------ tiendas
    def apertura(self, tienda_id: int) -> date | None:
        v = self.tiendas.get(tienda_id, {}).get("fecha_apertura")
        if v is None or v != v:  # NaN
            return None
        return v if isinstance(v, date) else date.fromisoformat(str(v)[:10])

    def resolver_tienda(self, v: object) -> Resolucion:
        if isinstance(v, int) and not isinstance(v, bool):
            if v in self.tiendas:
                return Resolucion(v, None)
            return Resolucion(None, None, f"la tienda {v} no existe en el catálogo")
        if isinstance(v, float):
            if es_entero(v) and int(v) in self.tiendas:
                return Resolucion(int(v), "tienda_formato", f"{v!r} se interpretó como {int(v)}")
            return Resolucion(None, None, f"{v!r} no es un identificador de tienda válido")
        if isinstance(v, str):
            s = v.strip()
            if s.startswith("-"):
                return Resolucion(None, None, f"{v!r} es negativo: no es un identificador de tienda")
            n = interpretar_numero(s)
            if n.valor is not None and es_entero(n.valor) and int(n.valor) in self.tiendas:
                return Resolucion(int(n.valor), "tienda_formato", f"{v!r} se interpretó como {int(n.valor)}")
            por_nombre = self._idx_tienda_nombre.get(compactar(s))
            if por_nombre is not None:
                return Resolucion(por_nombre, "tienda_nombre", f"{v!r} es el nombre de la tienda {por_nombre}")
            m = _PREFIJO_DIGITOS.match(s.lower())
            if m and int(m[1]) in self.tiendas:
                return Resolucion(int(m[1]), "tienda_formato", f"{v!r} se interpretó como {int(m[1])}")
        return Resolucion(None, None, f"{v!r} no corresponde a ninguna tienda del catálogo")

    # ------------------------------------------------------------------ familias
    def resolver_familia(self, v: object) -> Resolucion:
        if not isinstance(v, str):
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                v = str(v)
            else:
                return Resolucion(None, None, f"{v!r} no es un nombre de familia")
        if v in self.familias:
            return Resolucion(v, None)
        for clave in (compactar(v), _sin_vacias(v)):
            fid = self._idx_familia.get(clave)
            if fid is not None:
                return Resolucion(fid, "familia_formato", f"{v!r} se interpretó como {fid!r}")
        # error de escritura: un único candidato suficientemente parecido
        clave = compactar(v)
        if len(clave) >= 4:
            scores: dict[str, float] = {}
            for forma, fid in self._idx_familia.items():
                r = difflib.SequenceMatcher(None, clave, forma).ratio()
                scores[fid] = max(scores.get(fid, 0.0), r)
            ordenados = sorted(scores.items(), key=lambda kv: -kv[1])
            mejor, s1 = ordenados[0]
            s2 = ordenados[1][1] if len(ordenados) > 1 else 0.0
            if s1 >= self.similitud_minima and s1 - s2 >= 0.1:
                return Resolucion(mejor, "familia_aproximada",
                                  f"{v!r} no existe; se supuso {mejor!r} (similitud {s1:.2f})")
        return Resolucion(None, None, f"{v!r} no existe en el catálogo de familias")
