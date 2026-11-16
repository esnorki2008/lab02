"""Defensa del contrato: reparar / advertir / rechazar (incluye los casos E01–E10 del catálogo)."""
from __future__ import annotations

from datetime import date

import pytest

from novamercado.limpieza import Catalogo, ContextoLimpieza, Rechazo, limpiar_observaciones, limpiar_prediccion
from novamercado.limpieza.fechas import interpretar_fecha
from novamercado.limpieza.numeros import interpretar_numero

FAMILIAS = [{"familia_id": f, "nombre": n} for f, n in [
    ("abarrotes", "Abarrotes"), ("bebidas", "Bebidas"), ("lacteos", "Lácteos"), ("carnes", "Carnes"),
    ("panaderia", "Panadería"), ("frutas_verduras", "Frutas y verduras"), ("limpieza", "Limpieza"),
    ("cuidado_personal", "Cuidado personal"), ("licores", "Licores"), ("snacks", "Snacks y dulces"),
    ("bebe", "Bebé"), ("mascotas", "Mascotas")]]
TIENDAS = [{"tienda_id": i, "nombre": n, "ciudad": "Guatemala", "formato": "supermercado",
            "fecha_apertura": date(2019, 1, 1) if i != 10 else date(2025, 3, 15)}
           for i, n in [(1, "NovaMercado Zona 10"), (5, "NovaMercado Quetzaltenango"), (6, "NovaMercado Antigua"),
                        (10, "NovaMercado Express Zona 16")]]
TODAS = [f["familia_id"] for f in FAMILIAS]


@pytest.fixture()
def ctx() -> ContextoLimpieza:
    return ContextoLimpieza(
        catalogo=Catalogo(TIENDAS, FAMILIAS), fecha_minima=date(2024, 3, 1),
        fecha_maxima_calendario=date(2026, 12, 31), tolerancia_dias_sin_calendario=45,
        fecha_referencia=date(2026, 3, 10), familias_tienda=lambda t: TODAS,
        promo_referencia=lambda t, f, d: 2.0, promo_maximo={f: 19.0 for f in TODAS},
        ventas_umbral_atipico=lambda t, f: 10_000.0)


def solicitud(**cambios) -> dict:
    base = {"tienda_id": 5, "fecha": "2025-11-17",
            "familias": [{"familia_id": f, "en_promocion": 0} for f in TODAS]}
    base.update(cambios)
    return base


def reglas(rep) -> list[str]:
    return [o.regla for o in rep.observaciones]


# ------------------------------------------------------------------------------------------ números y fechas
@pytest.mark.parametrize("entrada,esperado", [
    (3, 3), ("3", 3), (2.0, 2), ("1,234", 1234), ("812,0", 812), ("1.234,5", 1234.5), ("1,234.5", 1234.5),
    ("2.5", 2.5), (" 7 ", 7), ("1.234.567", 1234567), ("-4", -4)])
def test_numeros_interpretables(entrada, esperado):
    assert interpretar_numero(entrada).valor == pytest.approx(esperado)


@pytest.mark.parametrize("entrada", ["tres", "", None, True, "1,2,3", "12a", float("nan"), [1]])
def test_numeros_no_interpretables(entrada):
    assert interpretar_numero(entrada).valor is None


@pytest.mark.parametrize("entrada,esperado", [
    ("2025-11-17", date(2025, 11, 17)), ("2025-11-17T00:00:00", date(2025, 11, 17)),
    ("2025-11-17 08:30:00", date(2025, 11, 17)), ("17/11/2025", date(2025, 11, 17)), ("2025/11/17", date(2025, 11, 17)),
    ("20251117", date(2025, 11, 17)), ("17 de noviembre de 2025", date(2025, 11, 17)),
    ("11/17/2025", date(2025, 11, 17)),
    ("17-11-25", date(2025, 11, 17))])
def test_fechas(entrada, esperado):
    assert interpretar_fecha(entrada).valor == esperado


def test_fecha_ambigua_usa_la_mas_cercana_a_la_operacion():
    f = interpretar_fecha("03/04/2026", referencia=date(2026, 3, 30))
    assert f.ambigua and f.valor == date(2026, 4, 3)          # 3 de abril, no 4 de marzo... cercana al 30-mar
    f = interpretar_fecha("03/04/2026", referencia=date(2026, 3, 2))
    assert f.valor == date(2026, 3, 4)


# ------------------------------------------------------------------------------------------ catálogo E01–E07
def test_valida_sin_observaciones(ctx):
    limpia, rep = limpiar_prediccion(solicitud(), ctx)
    assert rep.estado == "ok" and rep.observaciones == []
    assert limpia.tienda_id == 5 and limpia.fecha == date(2025, 11, 17) and len(limpia.familias) == 12


@pytest.mark.parametrize("valor", ["T05", "5", 5.0, "tienda 5", "T-05", "NovaMercado Quetzaltenango"])
def test_e01_tienda_texto_se_repara(ctx, valor):
    limpia, rep = limpiar_prediccion(solicitud(tienda_id=valor), ctx)
    assert limpia.tienda_id == 5 and rep.estado == "corregido"
    assert set(reglas(rep)) <= {"tienda_formato", "tienda_nombre"}


def test_e02_familia_escrita_distinto_se_repara(ctx):
    p = solicitud()
    p["familias"][2]["familia_id"] = "LÁCTEOS"
    p["familias"][5]["familia_id"] = "Frutas y Verduras"
    p["familias"][0]["familia_id"] = "abarrotes "
    limpia, rep = limpiar_prediccion(p, ctx)
    assert rep.estado == "corregido"
    assert {f.familia_id for f in limpia.familias} == set(TODAS)
    assert [o.campo for o in rep.observaciones] == ["familias[0].familia_id", "familias[2].familia_id",
                                                    "familias[5].familia_id"]


def test_e03_promocion_texto_o_decimal_entero_se_repara(ctx):
    p = solicitud()
    p["familias"][0]["en_promocion"] = "3"
    p["familias"][1]["en_promocion"] = 2.0
    limpia, rep = limpiar_prediccion(p, ctx)
    assert rep.estado == "corregido" and reglas(rep) == ["promocion_formato", "promocion_formato"]
    assert [f.en_promocion for f in limpia.familias[:2]] == [3, 2]


def test_e04_fecha_otro_formato_se_repara(ctx):
    for v in ("2025-11-17T00:00:00", "17/11/2025"):
        limpia, rep = limpiar_prediccion(solicitud(fecha=v), ctx)
        assert limpia.fecha == date(2025, 11, 17) and reglas(rep) == ["fecha_formato"]


def test_e05_familias_faltantes_se_completan_con_advertencia(ctx):
    p = solicitud()
    p["familias"] = [f for f in p["familias"] if f["familia_id"] not in ("bebe", "mascotas")]
    limpia, rep = limpiar_prediccion(p, ctx)
    assert rep.estado == "advertencia" and reglas(rep) == ["familias_incompletas"]
    completadas = [f for f in limpia.familias if f.completada]
    assert {f.familia_id for f in completadas} == {"bebe", "mascotas"}
    assert all(f.promocion_supuesta and f.en_promocion == 2 for f in completadas)


def test_e06_familia_repetida(ctx):
    p = solicitud()
    p["familias"].append({"familia_id": "abarrotes", "en_promocion": 3})
    limpia, rep = limpiar_prediccion(p, ctx)
    assert rep.estado == "advertencia" and reglas(rep) == ["familia_repetida_conflicto"]
    assert [f.en_promocion for f in limpia.familias if f.familia_id == "abarrotes"] == [3]
    p["familias"][-1]["en_promocion"] = 0     # repetida idéntica: sólo se repara
    limpia, rep = limpiar_prediccion(p, ctx)
    assert rep.estado == "corregido" and reglas(rep) == ["familia_repetida"]


@pytest.mark.parametrize("campo", ["tienda", "TiendaId", "store_id", "id_tienda"])
def test_e07_campo_renombrado(ctx, campo):
    p = solicitud()
    p[campo] = p.pop("tienda_id")
    limpia, rep = limpiar_prediccion(p, ctx)
    assert limpia.tienda_id == 5 and reglas(rep) == ["campo_renombrado"]


def test_campo_duplicado_con_valores_distintos_advierte(ctx):
    limpia, rep = limpiar_prediccion(solicitud(tienda=6), ctx)
    assert limpia.tienda_id == 5 and reglas(rep) == ["campo_conflicto"]


# ------------------------------------------------------------------------------------------ fuera del catálogo
def test_familias_como_objeto(ctx):
    limpia, rep = limpiar_prediccion(solicitud(familias={f: 1 for f in TODAS}), ctx)
    assert reglas(rep) == ["lista_estructura"] and all(f.en_promocion == 1 for f in limpia.familias)


def test_familia_con_error_de_escritura_se_advierte(ctx):
    p = solicitud()
    p["familias"][3]["familia_id"] = "carnez"
    limpia, rep = limpiar_prediccion(p, ctx)
    assert reglas(rep) == ["familia_aproximada"] and rep.estado == "advertencia"
    assert "carnes" in {f.familia_id for f in limpia.familias}


def test_familia_desconocida_se_omite_y_se_advierte(ctx):
    p = solicitud()
    p["familias"].append({"familia_id": "electrodomesticos", "en_promocion": 1})
    limpia, rep = limpiar_prediccion(p, ctx)
    assert reglas(rep) == ["familia_desconocida"] and len(limpia.familias) == 12


@pytest.mark.parametrize("valor,regla,esperado", [
    (None, "promocion_faltante", 2), ("muchas", "promocion_faltante", 2), (-3, "promocion_negativa", 2),
    (2.6, "promocion_decimal", 3), (500, "promocion_atipica", 19)])
def test_promocion_con_supuestos(ctx, valor, regla, esperado):
    p = solicitud()
    p["familias"][0]["en_promocion"] = valor
    limpia, rep = limpiar_prediccion(p, ctx)
    assert reglas(rep) == [regla] and rep.estado == "advertencia"
    assert limpia.familias[0].en_promocion == esperado


def test_promocion_ausente_se_imputa(ctx):
    p = solicitud()
    del p["familias"][0]["en_promocion"]
    limpia, rep = limpiar_prediccion(p, ctx)
    assert reglas(rep) == ["promocion_faltante"] and limpia.familias[0].promocion_supuesta


def test_fecha_ambigua_advierte(ctx):
    _, rep = limpiar_prediccion(solicitud(fecha="03/04/2026"), ctx)
    assert reglas(rep) == ["fecha_ambigua"]


def test_fecha_sin_calendario_advierte(ctx):
    limpia, rep = limpiar_prediccion(solicitud(fecha="2027-01-10"), ctx)
    assert reglas(rep) == ["fecha_sin_calendario"]


@pytest.mark.parametrize("cambios,regla", [
    ({"tienda_id": 99}, "tienda_desconocida"), ({"tienda_id": "-5"}, "tienda_desconocida"),
    ({"tienda_id": "Zona 99"}, "tienda_desconocida"),
    ({"tienda_id": None}, "tienda_faltante"), ({"fecha": "2025-02-30"}, "fecha_invalida"),
    ({"fecha": "mañana"}, "fecha_invalida"), ({"fecha": "2071-08-10"}, "fecha_fuera_de_rango"),
    ({"fecha": "2020-01-01"}, "fecha_fuera_de_rango"), ({"fecha": None}, "fecha_faltante"),
    ({"familias": []}, "lista_faltante"), ({"familias": [{"familia_id": "xyz"}]}, "sin_registros_validos"),
    ({"tienda_id": 10, "fecha": "2025-01-10"}, "tienda_no_abierta")])
def test_rechazos(ctx, cambios, regla):
    with pytest.raises(Rechazo) as e:
        limpiar_prediccion(solicitud(**cambios), ctx)
    assert e.value.regla == regla and e.value.detalle


def test_cuerpo_que_no_es_objeto_se_rechaza(ctx):
    with pytest.raises(Rechazo) as e:
        limpiar_prediccion([1, 2], ctx)
    assert e.value.regla == "cuerpo_invalido"


# ------------------------------------------------------------------------------------------ observaciones E08–E10
def observacion(**cambios) -> dict:
    base = {"tienda_id": 5, "fecha": "2025-11-17", "ventas": [{"familia_id": f, "ventas": 100} for f in TODAS]}
    base.update(cambios)
    return base


def test_e08_ventas_negativas_no_se_guardan(ctx):
    p = observacion()
    p["ventas"][5]["ventas"] = -4
    limpia, rep = limpiar_observaciones(p, ctx)
    assert reglas(rep) == ["ventas_negativa"] and len(limpia.ventas) == 11


def test_e10_numeros_con_separadores(ctx):
    p = observacion()
    p["ventas"][0]["ventas"] = "1,234"
    p["ventas"][1]["ventas"] = "812,0"
    limpia, rep = limpiar_observaciones(p, ctx)
    assert reglas(rep) == ["ventas_formato", "ventas_formato"]
    assert [v.ventas for v in limpia.ventas[:2]] == [1234, 812]


def test_ventas_atipicas_se_marcan(ctx):
    p = observacion()
    p["ventas"][0]["ventas"] = 99_999
    limpia, rep = limpiar_observaciones(p, ctx)
    assert reglas(rep) == ["ventas_atipica"] and limpia.ventas[0].atipico


def test_observacion_sin_registros_utilizables_se_rechaza(ctx):
    with pytest.raises(Rechazo) as e:
        limpiar_observaciones(observacion(ventas=[{"familia_id": "abarrotes", "ventas": -1}]), ctx)
    assert e.value.regla == "sin_registros_validos"
