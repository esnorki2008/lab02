"""Limpieza de las solicitudes de ``/v1/prediccion`` y ``/v1/observaciones``.

Entrada: el JSON tal como llega (``dict`` de Python). Salida: un modelo Pydantic del contrato ya válido
más el ``ReporteCalidad`` con lo que se hizo. Si el problema es de rechazo se lanza ``Rechazo``.
Todo depende de un ``ContextoLimpieza`` (catálogos y estadísticas), nunca del reloj del sistema.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta

from novamercado.limpieza.catalogo import Catalogo
from novamercado.limpieza.fechas import interpretar_fecha
from novamercado.limpieza.numeros import es_entero, interpretar_numero
from novamercado.limpieza.reporte import Rechazo, ReporteCalidad
from novamercado.schemas import (
    FamiliaSolicitada,
    ObservacionLimpia,
    PrediccionLimpia,
    VentaObservada,
)
from novamercado.textos import normalizar_clave

# nombre del contrato -> formas aceptadas (normalizadas con normalizar_clave)
ALIAS_RAIZ = {
    "tienda_id": ["tienda_id", "tienda", "id_tienda", "tiendaid", "store", "store_id", "store_nbr", "sucursal",
                  "sucursal_id", "codigo_tienda", "tienda_codigo", "tienda_nro", "no_tienda", "num_tienda"],
    "fecha": ["fecha", "date", "dia", "fecha_prediccion", "fecha_pronostico", "fecha_venta", "fecha_ventas",
              "fecha_observacion"],
}
ALIAS_LISTA = {
    "prediccion": ["familias", "familia", "productos", "items", "lineas", "detalle", "familia_list", "families"],
    "observacion": ["ventas", "venta", "observaciones", "items", "detalle", "registros", "familias", "sales"],
}
ALIAS_ITEM = {
    "familia_id": ["familia_id", "familia", "id_familia", "familiaid", "family", "family_id", "categoria",
                   "nombre_familia", "familia_nombre"],
    "en_promocion": ["en_promocion", "promocion", "enpromocion", "promo", "promociones", "on_promotion",
                     "onpromotion", "en_promo", "productos_en_promocion", "num_promocion"],
    "ventas": ["ventas", "venta", "ventas_reales", "sales", "unidades", "cantidad", "unidades_vendidas", "valor"],
}


@dataclass
class ContextoLimpieza:
    """Lo que la limpieza necesita saber del mundo. Lo construye el servicio a partir de la base de datos."""

    catalogo: Catalogo
    fecha_minima: date                      # inicio del histórico
    fecha_maxima_calendario: date           # último día cubierto por feriados.csv
    tolerancia_dias_sin_calendario: int = 45
    fecha_referencia: date | None = None    # próxima fecha de operación esperada (desambiguar día/mes)
    familias_tienda: Callable[[int], list[str]] | None = None
    promo_referencia: Callable[[int, str, date], float] = field(default=lambda t, f, d: 0.0)
    promo_maximo: dict[str, float] = field(default_factory=dict)
    promocion_factor_atipico: float = 3.0
    ventas_umbral_atipico: Callable[[int, str], float] = field(default=lambda t, f: float("inf"))


# ---------------------------------------------------------------------------------------------------- utilidades
def _campos(obj: dict, alias: dict[str, list[str]], reporte: ReporteCalidad, prefijo: str = "") -> dict:
    """Mapea las llaves de ``obj`` a los nombres del contrato (reparando nombres distintos)."""
    out: dict = {}
    for canon, formas in alias.items():
        if canon in obj:
            out[canon] = obj[canon]
        for k, v in obj.items():
            if k == canon:
                continue
            if normalizar_clave(k) in formas:
                if canon in out:
                    if out[canon] != v:
                        reporte.agregar("campo_conflicto", f"{prefijo}{canon}",
                                        f"llegaron '{canon}'={out[canon]!r} y '{k}'={v!r}; se usó '{canon}'")
                    continue
                out[canon] = v
                reporte.agregar("campo_renombrado", f"{prefijo}{canon}", f"el campo '{k}' se interpretó como '{canon}'")
    return out


def _cuerpo(payload: object) -> dict:
    if not isinstance(payload, dict):
        raise Rechazo("cuerpo_invalido", "body", "El cuerpo de la solicitud debe ser un objeto JSON")
    return payload


def _tienda(raiz: dict, ctx: ContextoLimpieza, reporte: ReporteCalidad) -> int:
    if raiz.get("tienda_id") in (None, ""):
        raise Rechazo("tienda_faltante", "tienda_id", "Falta el identificador de tienda 'tienda_id'")
    r = ctx.catalogo.resolver_tienda(raiz["tienda_id"])
    if r.valor is None:
        raise Rechazo("tienda_desconocida", "tienda_id", f"Tienda desconocida: {r.detalle}")
    if r.regla:
        reporte.agregar(r.regla, "tienda_id", r.detalle)
    return int(r.valor)


def _fecha(raiz: dict, ctx: ContextoLimpieza, reporte: ReporteCalidad) -> date:
    v = raiz.get("fecha")
    if v in (None, ""):
        raise Rechazo("fecha_faltante", "fecha", "Falta la fecha 'fecha' (formato AAAA-MM-DD)")
    f = interpretar_fecha(v, ctx.fecha_referencia)
    if f.valor is None:
        raise Rechazo("fecha_invalida", "fecha", f"{v!r} no es una fecha válida (formato esperado AAAA-MM-DD)")
    limite = ctx.fecha_maxima_calendario + timedelta(days=ctx.tolerancia_dias_sin_calendario)
    if f.valor < ctx.fecha_minima or f.valor > limite:
        raise Rechazo("fecha_fuera_de_rango", "fecha",
                      f"La fecha {f.valor.isoformat()} está fuera del rango atendido "
                      f"({ctx.fecha_minima.isoformat()} a {limite.isoformat()})")
    if f.ambigua:
        otra = [a for a in f.alternativas if a != f.valor][0]
        reporte.agregar("fecha_ambigua", "fecha",
                        f"{v!r} es ambigua; se interpretó como {f.valor.isoformat()} (no {otra.isoformat()}) "
                        "por ser la más cercana a la fecha de operación")
    elif not f.formato_contrato:
        reporte.agregar("fecha_formato", "fecha", f"{v!r} se interpretó como {f.valor.isoformat()}")
    if f.valor > ctx.fecha_maxima_calendario:
        reporte.agregar("fecha_sin_calendario", "fecha",
                        f"no hay calendario de feriados publicado después de "
                        f"{ctx.fecha_maxima_calendario.isoformat()}; se supuso un día sin eventos especiales")
    return f.valor


def _validar_apertura(tienda_id: int, fecha: date, ctx: ContextoLimpieza) -> None:
    apertura = ctx.catalogo.apertura(tienda_id)
    if apertura is not None and fecha < apertura:
        raise Rechazo("tienda_no_abierta", "fecha",
                      f"La tienda {tienda_id} abrió el {apertura.isoformat()}; no hay ventas que pronosticar "
                      f"el {fecha.isoformat()}")


def _lista(raiz_original: dict, tipo: str, reporte: ReporteCalidad, valor_item: str) -> list[tuple[str, object]]:
    """Devuelve [(campo_original, elemento)] normalizando estructuras alternativas."""
    canon = "familias" if tipo == "prediccion" else "ventas"
    lista = _campos(raiz_original, {canon: ALIAS_LISTA[tipo]}, reporte).get(canon)
    if lista is None or (isinstance(lista, (list, dict)) and len(lista) == 0):
        raise Rechazo("lista_faltante", canon, f"Falta la lista '{canon}' o viene vacía")
    if isinstance(lista, dict):
        # {"abarrotes": 3, "bebidas": 8}  ->  [{"familia_id": "abarrotes", valor_item: 3}, ...]
        reporte.agregar("lista_estructura", canon,
                        f"'{canon}' llegó como objeto {{familia: valor}}; se convirtió a lista")
        return [(f"{canon}.{k}", {"familia_id": k, valor_item: v}) for k, v in lista.items()]
    if not isinstance(lista, list):
        raise Rechazo("lista_faltante", canon, f"'{canon}' debe ser una lista")
    return [(f"{canon}[{i}]", item) for i, item in enumerate(lista)]


def _item(campo: str, item: object, ctx: ContextoLimpieza, reporte: ReporteCalidad,
          valor_item: str) -> tuple[str, object, bool] | None:
    """Resuelve un elemento a (familia_id, valor_crudo, valor_presente) o None si se omite."""
    if isinstance(item, str):
        item = {"familia_id": item}
    if not isinstance(item, dict):
        reporte.agregar("elemento_invalido", campo, f"{item!r} no es un objeto con 'familia_id'; se omitió")
        return None
    datos = _campos(item, {"familia_id": ALIAS_ITEM["familia_id"], valor_item: ALIAS_ITEM[valor_item]},
                    reporte, prefijo=f"{campo}.")
    if datos.get("familia_id") in (None, ""):
        reporte.agregar("elemento_invalido", campo, "elemento sin 'familia_id'; se omitió")
        return None
    r = ctx.catalogo.resolver_familia(datos["familia_id"])
    if r.valor is None:
        reporte.agregar("familia_desconocida", f"{campo}.familia_id", f"{r.detalle}; se omitió")
        return None
    if r.regla:
        reporte.agregar(r.regla, f"{campo}.familia_id", r.detalle)
    return str(r.valor), datos.get(valor_item), valor_item in datos


def _deduplicar(items: list[tuple[str, str, object]], reporte: ReporteCalidad) -> dict[str, tuple[str, object]]:
    """familia -> (campo, valor). Repetidas: iguales se reparan; distintas -> se usa la última (advertencia)."""
    out: dict[str, tuple[str, object]] = {}
    for campo, fid, valor in items:
        if fid in out:
            campo_prev, valor_prev = out[fid]
            if valor_prev == valor:
                reporte.agregar("familia_repetida", campo, f"'{fid}' venía repetida con el mismo valor; se dejó una")
                continue
            reporte.agregar("familia_repetida_conflicto", campo,
                            f"'{fid}' venía repetida con valores distintos ({valor_prev!r} en {campo_prev} y "
                            f"{valor!r} en {campo}); se usó el último")
        out[fid] = (campo, valor)
    return out


# ---------------------------------------------------------------------------------------------- en_promocion
def _promocion(campo: str, valor: object, presente: bool, tienda: int, familia: str, fecha: date,
               ctx: ContextoLimpieza, reporte: ReporteCalidad) -> tuple[int, bool]:
    """Devuelve (en_promocion, fue_supuesta)."""
    def imputar(regla: str, motivo: str) -> tuple[int, bool]:
        ref = int(round(ctx.promo_referencia(tienda, familia, fecha)))
        reporte.agregar(regla, campo, f"{motivo}; se supuso {ref} (mediana reciente de la tienda y familia)")
        return ref, True

    if not presente or valor is None or valor == "":
        return imputar("promocion_faltante", "no se indicó en_promocion")
    n = interpretar_numero(valor)
    if n.valor is None:
        return imputar("promocion_faltante", f"{valor!r} no es un número")
    if n.valor < 0:
        return imputar("promocion_negativa", f"{valor!r} es negativo")
    x = n.valor
    if not es_entero(x):
        nuevo = int(round(x))
        reporte.agregar("promocion_decimal", campo, f"{valor!r} no es entero; se redondeó a {nuevo}")
        x = float(nuevo)
    elif n.reformateado:
        reporte.agregar("promocion_formato", campo, f"{valor!r} se interpretó como {int(x)}")
    maximo = ctx.promo_maximo.get(familia)
    if maximo is not None and maximo > 0 and x > ctx.promocion_factor_atipico * maximo:
        tope = int(round(maximo))
        reporte.agregar("promocion_atipica", campo,
                        f"{int(x)} supera {ctx.promocion_factor_atipico:g}× el máximo histórico de la familia "
                        f"({tope}); se acotó a {tope}")
        x = float(tope)
    return int(x), False


# ---------------------------------------------------------------------------------------------- API pública
def limpiar_prediccion(payload: object, ctx: ContextoLimpieza) -> tuple[PrediccionLimpia, ReporteCalidad]:
    reporte = ReporteCalidad()
    cuerpo = _cuerpo(payload)
    raiz = _campos(cuerpo, ALIAS_RAIZ, reporte)
    tienda = _tienda(raiz, ctx, reporte)
    fecha = _fecha(raiz, ctx, reporte)
    _validar_apertura(tienda, fecha, ctx)

    resueltos: list[tuple[str, str, object]] = []
    presentes: dict[str, bool] = {}
    for campo, item in _lista(cuerpo, "prediccion", reporte, "en_promocion"):
        r = _item(campo, item, ctx, reporte, "en_promocion")
        if r is None:
            continue
        fid, valor, presente = r
        resueltos.append((campo, fid, valor))
        presentes[campo] = presente
    if not resueltos:
        raise Rechazo("sin_registros_validos", "familias", "Ninguna familia de la solicitud es válida")
    unicos = _deduplicar(resueltos, reporte)

    familias = []
    for fid, (campo, valor) in unicos.items():
        promo, supuesta = _promocion(f"{campo}.en_promocion", valor, presentes[campo], tienda, fid, fecha, ctx,
                                     reporte)
        familias.append(FamiliaSolicitada(familia_id=fid, en_promocion=promo, promocion_supuesta=supuesta))

    if ctx.familias_tienda is not None:
        faltan = [f for f in ctx.familias_tienda(tienda) if f not in unicos]
        if faltan:
            for fid in faltan:
                promo = int(round(ctx.promo_referencia(tienda, fid, fecha)))
                familias.append(FamiliaSolicitada(familia_id=fid, en_promocion=promo, completada=True,
                                                  promocion_supuesta=True))
            reporte.agregar("familias_incompletas", "familias",
                            f"faltaban {len(faltan)} familias que la tienda vende ({', '.join(faltan)}); se "
                            "pronosticaron también con la promoción habitual (mediana reciente)")
    return PrediccionLimpia(tienda_id=tienda, fecha=fecha, familias=familias), reporte


def limpiar_observaciones(payload: object, ctx: ContextoLimpieza) -> tuple[ObservacionLimpia, ReporteCalidad]:
    reporte = ReporteCalidad()
    cuerpo = _cuerpo(payload)
    raiz = _campos(cuerpo, ALIAS_RAIZ, reporte)
    tienda = _tienda(raiz, ctx, reporte)
    fecha = _fecha(raiz, ctx, reporte)
    _validar_apertura(tienda, fecha, ctx)

    resueltos: list[tuple[str, str, object]] = []
    for campo, item in _lista(cuerpo, "observacion", reporte, "ventas"):
        r = _item(campo, item, ctx, reporte, "ventas")
        if r is None:
            continue
        fid, valor, _ = r
        resueltos.append((campo, fid, valor))
    if not resueltos:
        raise Rechazo("sin_registros_validos", "ventas", "Ninguna familia de la solicitud es válida")
    unicos = _deduplicar(resueltos, reporte)

    ventas = []
    for fid, (campo, valor) in unicos.items():
        c = f"{campo}.ventas"
        n = interpretar_numero(valor)
        if n.valor is None:
            reporte.agregar("ventas_faltante", c, f"{valor!r} no es un número de ventas; el registro no se guardó")
            continue
        if n.valor < 0:
            reporte.agregar("ventas_negativa", c,
                            f"{valor!r} es negativo (posibles devoluciones); el registro no se guardó")
            continue
        x = n.valor
        if not es_entero(x):
            x = float(round(x))
            reporte.agregar("ventas_decimal", c, f"{valor!r} no es entero; se redondeó a {int(x)}")
        elif n.reformateado:
            reporte.agregar("ventas_formato", c, f"{valor!r} se interpretó como {int(x)}")
        atipico = False
        umbral = ctx.ventas_umbral_atipico(tienda, fid)
        if x > umbral:
            atipico = True
            reporte.agregar("ventas_atipica", c,
                            f"{int(x)} es extremo para la serie (umbral {umbral:.0f}); se guardó marcado y no se "
                            "usará para pronosticar ni entrenar")
        ventas.append(VentaObservada(familia_id=fid, ventas=int(x), atipico=atipico))
    if not ventas:
        raise Rechazo("sin_registros_validos", "ventas", "Ningún registro de ventas es utilizable: "
                      + "; ".join(o.detalle for o in reporte.observaciones if o.campo.endswith(".ventas")))
    return ObservacionLimpia(tienda_id=tienda, fecha=fecha, ventas=ventas), reporte
