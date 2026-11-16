"""Contrato HTTP del Anexo (sin MLflow, Airflow ni PostgreSQL: SQLite y dobles de prueba)."""
from __future__ import annotations

import sqlalchemy as sa

from novamercado.db import correcciones, predicciones, solicitudes

FAMILIAS = ["abarrotes", "bebidas", "lacteos", "panaderia", "frutas_verduras", "licores"]


def pred(**cambios) -> dict:
    p = {"tienda_id": 1, "fecha": "2025-03-01",
         "familias": [{"familia_id": f, "en_promocion": 0} for f in FAMILIAS]}
    p.update(cambios)
    return p


def obs(**cambios) -> dict:
    p = {"tienda_id": 1, "fecha": "2025-03-01", "ventas": [{"familia_id": f, "ventas": 100 + i}
                                                          for i, f in enumerate(FAMILIAS)]}
    p.update(cambios)
    return p


def contar(repo, tabla) -> int:
    with repo.engine.connect() as c:
        return c.execute(sa.select(sa.func.count()).select_from(tabla)).scalar()


def test_prediccion_valida_cumple_contrato(entorno):
    r = entorno["cliente"].post("/v1/prediccion", json=pred())
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["tienda_id"] == 1 and b["fecha"] == "2025-03-01"
    assert isinstance(b["version_modelo"], str) and b["version_modelo"] == "1"
    assert b["calidad"] == {"estado": "ok", "observaciones": []}
    assert [p["familia_id"] for p in b["predicciones"]] == FAMILIAS
    for p in b["predicciones"]:
        assert isinstance(p["venta_esperada"], float) and p["venta_esperada"] >= 0
        assert isinstance(p["cantidad_sugerida"], int) and p["cantidad_sugerida"] >= 0
    # quedó registrado: solicitud + una predicción por familia, con su versión de modelo
    repo = entorno["repo"]
    assert contar(repo, solicitudes) == 1 and contar(repo, predicciones) == len(FAMILIAS)


def test_prediccion_corregida_informa_y_registra(entorno):
    p = pred(tienda_id="T01", fecha="2025/03/01")
    p["familias"][2]["familia_id"] = "LÁCTEOS"
    r = entorno["cliente"].post("/v1/prediccion", json=p)
    b = r.json()
    assert r.status_code == 200 and b["calidad"]["estado"] == "corregido"
    assert {o["accion"] for o in b["calidad"]["observaciones"]} == {"reparar"}
    assert {o["campo"] for o in b["calidad"]["observaciones"]} == {"tienda_id", "fecha", "familias[2].familia_id"}
    assert contar(entorno["repo"], correcciones) == 3


def test_advertencia_si_hay_supuestos(entorno):
    p = pred()
    p["familias"] = p["familias"][:4]          # faltan familias que la tienda vende
    b = entorno["cliente"].post("/v1/prediccion", json=p).json()
    assert b["calidad"]["estado"] == "advertencia"
    assert len(b["predicciones"]) == len(FAMILIAS)


def test_misma_fecha_puede_pedirse_dos_veces(entorno):
    c = entorno["cliente"]
    a = c.post("/v1/prediccion", json=pred()).json()
    b = c.post("/v1/prediccion", json=pred()).json()
    assert a["predicciones"] == b["predicciones"]


def test_prediccion_no_usa_el_dato_del_mismo_dia(entorno):
    c = entorno["cliente"]
    antes = c.post("/v1/prediccion", json=pred(fecha="2025-03-02")).json()["predicciones"]
    c.post("/v1/observaciones", json=obs(fecha="2025-03-02", ventas=[{"familia_id": "abarrotes", "ventas": 9000}]))
    despues = c.post("/v1/prediccion", json=pred(fecha="2025-03-02")).json()["predicciones"]
    assert antes == despues


def test_observacion_de_ayer_cambia_el_pronostico(entorno):
    c = entorno["cliente"]
    antes = c.post("/v1/prediccion", json=pred(fecha="2025-03-02")).json()["predicciones"][0]
    c.post("/v1/observaciones", json=obs(fecha="2025-03-01", ventas=[{"familia_id": "abarrotes", "ventas": 5000}]))
    despues = c.post("/v1/prediccion", json=pred(fecha="2025-03-02")).json()["predicciones"][0]
    assert despues["venta_esperada"] != antes["venta_esperada"]


def test_rechazo_422_con_detail(entorno):
    for p in (pred(tienda_id=999), pred(fecha="2025-13-45"), pred(familias=[]), {"hola": 1}):
        r = entorno["cliente"].post("/v1/prediccion", json=p)
        assert r.status_code == 422 and isinstance(r.json()["detail"], str) and r.json()["detail"]
    r = entorno["cliente"].post("/v1/prediccion", content=b"{no es json", headers={"content-type": "application/json"})
    assert r.status_code == 422 and "JSON" in r.json()["detail"]
    with entorno["repo"].engine.connect() as cn:
        estados = [x[0] for x in cn.execute(sa.select(solicitudes.c.http_status))]
    assert estados.count(422) == 5


def test_observaciones_idempotentes(entorno):
    c = entorno["cliente"]
    r1 = c.post("/v1/observaciones", json=obs())
    assert r1.status_code == 200 and r1.json() == {"registros_guardados": 6,
                                                   "calidad": {"estado": "ok", "observaciones": []}}
    r2 = c.post("/v1/observaciones", json=obs())
    assert r2.json()["registros_guardados"] == 0
    cambiado = obs()
    cambiado["ventas"][0]["ventas"] = 7
    assert c.post("/v1/observaciones", json=cambiado).json()["registros_guardados"] == 1


def test_observaciones_e08_e10(entorno):
    p = obs()
    p["ventas"][0]["ventas"] = -4
    p["ventas"][1]["ventas"] = "1,234"
    b = entorno["cliente"].post("/v1/observaciones", json=p).json()
    assert b["registros_guardados"] == 5 and b["calidad"]["estado"] == "advertencia"
    assert [o["regla"] for o in b["calidad"]["observaciones"]] == ["ventas_negativa", "ventas_formato"]


def test_modelo_y_health(entorno):
    c = entorno["cliente"]
    assert c.get("/modelo").json() == {"nombre": "novamercado-ventas", "alias": "champion", "version": "1"}
    h = c.get("/health")
    assert h.status_code == 200 and h.json()["estado"] == "ok"


def test_health_degradado_si_mlflow_cae_pero_sigue_prediciendo(entorno):
    entorno["registro"].disponible = False
    c = entorno["cliente"]
    h = c.get("/health")
    assert h.status_code == 200 and h.json()["estado"] == "degradado" and "mlflow" in h.json()["motivo"]
    entorno["gestor"].refrescar()                       # el sondeo falla pero no descarga el modelo
    assert c.post("/v1/prediccion", json=pred()).status_code == 200
    entorno["registro"].disponible = True
    assert c.get("/health").json()["estado"] == "ok"


def test_sin_modelo_responde_503(entorno):
    entorno["gestor"]._actual = None
    assert entorno["cliente"].get("/health").status_code == 503
    assert entorno["cliente"].post("/v1/prediccion", json=pred()).status_code == 503


def test_recarga_en_caliente(entorno, modelo_entrenado):
    reg, gestor = entorno["registro"], entorno["gestor"]
    reg.versiones["2"] = modelo_entrenado[0]
    reg.vigente_version = "2"
    gestor.refrescar()                                   # lo que hace el hilo cada 20 s
    b = entorno["cliente"].post("/v1/prediccion", json=pred()).json()
    assert b["version_modelo"] == "2"
    assert entorno["cliente"].get("/modelo").json()["version"] == "2"


def test_cache_local_permite_arrancar_sin_registro(entorno, settings, tmp_path):
    from novamercado.gestor_modelo import GestorModelo
    from novamercado.registro import RegistroMemoria

    caido = RegistroMemoria()
    caido.disponible = False
    g = GestorModelo(caido, nombre="n", alias="a", cache_dir=entorno["gestor"].cache_dir)
    assert g.cargar_cache() and g.actual().version == "1"


def test_reentrenamiento_admin(entorno):
    c, af = entorno["cliente"], entorno["airflow"]
    r = c.post("/admin/reentrenar")
    assert r.status_code == 202 and isinstance(r.json()["id"], str)
    rid = r.json()["id"]
    assert af.disparos == [rid]
    assert c.get(f"/admin/reentrenar/{rid}").json()["estado"] == "en_curso"
    entorno["repo"].terminar_reentrenamiento(rid, "exitoso", True, "2", "Costo 1 vs 2 del vigente")
    b = c.get(f"/admin/reentrenar/{rid}").json()
    assert b["estado"] == "exitoso" and b["promovido"] is True and b["version"] == "2" and b["motivo"]
    assert c.get("/admin/reentrenar/no-existe").status_code == 404


def test_reentrenamiento_fallido_en_airflow(entorno):
    c, af = entorno["cliente"], entorno["airflow"]
    rid = c.post("/admin/reentrenar").json()["id"]
    af.estados[rid] = "failed"
    b = c.get(f"/admin/reentrenar/{rid}").json()
    assert b["estado"] == "fallido" and b["promovido"] is False and b["version"] == "1"


def test_reentrenar_503_si_airflow_no_esta(entorno):
    entorno["airflow"].caido = True
    assert entorno["cliente"].post("/admin/reentrenar").status_code == 503


def test_nan_en_json_no_tumba_el_servicio(entorno):
    c = entorno["cliente"]
    cuerpo = obs(fecha="2025-03-03")
    texto = __import__("json").dumps(cuerpo).replace('"ventas": 101', '"ventas": NaN')
    r = c.post("/v1/observaciones", content=texto.encode(), headers={"content-type": "application/json"})
    assert r.status_code == 200 and r.json()["registros_guardados"] == 5
    assert [o["regla"] for o in r.json()["calidad"]["observaciones"]] == ["ventas_faltante"]
    texto = __import__("json").dumps(pred()).replace('"en_promocion": 0}]', '"en_promocion": Infinity}]')
    r = c.post("/v1/prediccion", content=texto.encode(), headers={"content-type": "application/json"})
    assert r.status_code == 200 and r.json()["calidad"]["estado"] == "advertencia"


def test_advierte_si_la_tienda_lleva_dias_sin_datos(entorno):
    b = entorno["cliente"].post("/v1/prediccion", json=pred(fecha="2025-03-20")).json()
    assert "historia_desactualizada" in [o["regla"] for o in b["calidad"]["observaciones"]]
    assert b["calidad"]["estado"] == "advertencia" and len(b["predicciones"]) == len(FAMILIAS)
