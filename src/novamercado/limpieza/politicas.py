"""Política explícita para cada tipo de problema en los datos de entrada.

Cada regla tiene una sola acción, aplicada siempre igual:

* ``reparar``: la corrección es inequívoca (mismo dato, otra forma de escribirlo).
* ``advertir``: se responde, pero con un supuesto que se informa en ``calidad``.
* ``rechazar``: no hay forma razonable de responder sin inventar datos -> HTTP 422.

Esta tabla es la única fuente de verdad: el código consulta la acción aquí y ``DECISIONES.md`` la
documenta (una prueba verifica que cada regla esté documentada).
"""
from __future__ import annotations

from dataclasses import dataclass

REPARAR = "reparar"
ADVERTIR = "advertir"
RECHAZAR = "rechazar"


@dataclass(frozen=True)
class Politica:
    accion: str
    descripcion: str


POLITICAS: dict[str, Politica] = {
    # --- Estructura de la solicitud ---------------------------------------------------------------
    "cuerpo_invalido": Politica(RECHAZAR, "El cuerpo no es un objeto JSON."),
    "campo_renombrado": Politica(REPARAR, "Campo con otro nombre o forma ('tienda', 'TiendaId') -> el del contrato."),
    "campo_conflicto": Politica(ADVERTIR, "Mismo campo con dos nombres y valores distintos: se usa el del contrato."),
    # --- Tienda -----------------------------------------------------------------------------------
    "tienda_faltante": Politica(RECHAZAR, "No hay identificador de tienda."),
    "tienda_formato": Politica(REPARAR, "Id como texto, decimal entero o con prefijo ('T05', '5', 5.0) -> 5."),
    "tienda_nombre": Politica(REPARAR, "Se envió el nombre de la tienda del catálogo en vez del id."),
    "tienda_desconocida": Politica(RECHAZAR, "La tienda no existe en el catálogo (o el id no es interpretable)."),
    "tienda_no_abierta": Politica(RECHAZAR, "La fecha es anterior a la apertura de la tienda."),
    # --- Fecha ------------------------------------------------------------------------------------
    "fecha_faltante": Politica(RECHAZAR, "No hay fecha."),
    "fecha_formato": Politica(REPARAR, "Fecha en otro formato no ambiguo ('17/11/2025', '2025-11-17T00:00:00')."),
    "fecha_ambigua": Politica(ADVERTIR, "Día y mes intercambiables ('05/11/2025'): se elige la interpretación más "
                                        "cercana a la fecha de operación."),
    "fecha_invalida": Politica(RECHAZAR, "Texto que no es una fecha o fecha imposible (2025-02-30)."),
    "fecha_fuera_de_rango": Politica(RECHAZAR, "Fecha antes del histórico o fuera del calendario publicado."),
    "fecha_sin_calendario": Politica(ADVERTIR, "Fecha posterior al último feriado publicado (dentro de la "
                                               "tolerancia): se supone que no hay eventos especiales."),
    # --- Lista de familias / ventas -------------------------------------------------------------
    "lista_faltante": Politica(RECHAZAR, "No viene la lista de familias (predicción) o de ventas (observación)."),
    "lista_estructura": Politica(REPARAR, "Lista en otra estructura reconocible (objeto {familia: valor})."),
    "elemento_invalido": Politica(ADVERTIR, "Elemento de la lista que no es un objeto reconocible: se omite."),
    "sin_registros_validos": Politica(RECHAZAR, "Ningún elemento de la lista es utilizable."),
    # --- Familia ----------------------------------------------------------------------------------
    "familia_formato": Politica(REPARAR, "Familia escrita de otra forma: mayúsculas, acentos, espacios o el nombre "
                                         "del catálogo ('LÁCTEOS', 'Frutas y Verduras')."),
    "familia_aproximada": Politica(ADVERTIR, "Familia con error de escritura y un único candidato muy parecido."),
    "familia_desconocida": Politica(ADVERTIR, "Familia que no existe en el catálogo: se omite del resultado."),
    "familia_repetida": Politica(REPARAR, "Familia repetida con los mismos valores: se deja una."),
    "familia_repetida_conflicto": Politica(ADVERTIR, "Familia repetida con valores distintos: se usa la última."),
    "familias_incompletas": Politica(ADVERTIR, "Faltan familias que la tienda vende: se pronostican también, "
                                               "suponiendo la promoción habitual."),
    "historia_desactualizada": Politica(ADVERTIR, "La tienda lleva varios días sin ventas conocidas antes de la "
                                                  "fecha: se pronostica con su último nivel conocido."),
    # --- en_promocion -----------------------------------------------------------------------------
    "promocion_formato": Politica(REPARAR, "Número como texto o decimal entero ('3', 2.0, '1,0')."),
    "promocion_faltante": Politica(ADVERTIR, "Falta o no es interpretable: se usa la mediana reciente de la serie."),
    "promocion_negativa": Politica(ADVERTIR, "Valor negativo (imposible): se usa la mediana reciente de la serie."),
    "promocion_decimal": Politica(ADVERTIR, "Valor no entero (2.5): se redondea."),
    "promocion_atipica": Politica(ADVERTIR, "Muy por encima del máximo histórico de la familia: se acota."),
    # --- ventas (observaciones) -------------------------------------------------------------------
    "ventas_formato": Politica(REPARAR, "Número como texto, con separador de miles o coma decimal ('1,234', '812,0')."),
    "ventas_faltante": Politica(ADVERTIR, "Falta o no es interpretable: el registro no se guarda."),
    "ventas_negativa": Politica(ADVERTIR, "Venta negativa (devoluciones): el registro no se guarda."),
    "ventas_decimal": Politica(ADVERTIR, "Ventas no enteras (unidades): se redondean."),
    "ventas_atipica": Politica(ADVERTIR, "Valor extremo para la serie: se guarda marcado y no se usa para modelar."),
}


def accion(regla: str) -> str:
    return POLITICAS[regla].accion
