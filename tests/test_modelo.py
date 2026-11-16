"""Modelo: disponibilidad temporal de la información, cantidad sugerida y criterio de reemplazo."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from novamercado.entrenamiento import ciclo, decidir
from novamercado.features import construir_features, tipos_evento
from novamercado.modelos import calibrar_multiplicadores, costo, cuantil_ponderado, nivel_servicio
from novamercado.registro import RegistroMemoria


def test_features_no_usan_ventas_del_dia_ni_posteriores(repo_base):
    panel = repo_base.ventas(tienda_id=1)
    tiendas, feriados = repo_base.tabla("tiendas"), repo_base.tabla("feriados")
    kw = dict(familias=sorted(panel["familia_id"].unique()), formatos=["hipermercado"], tipos=tipos_evento(feriados))
    d = pd.Timestamp("2024-12-10")
    base = construir_features(panel, tiendas, feriados, **kw)
    alterado = panel.copy()
    alterado.loc[alterado["fecha"] >= d, "ventas"] *= 50
    otro = construir_features(alterado, tiendas, feriados, **kw)
    cols = [c for c in base.columns if c.startswith(("r_", "lag", "media", "nivel", "escala", "dias_sin"))]
    a = base[base["fecha"] == d][cols].reset_index(drop=True)
    b = otro[otro["fecha"] == d][cols].reset_index(drop=True)
    pd.testing.assert_frame_equal(a, b)


def test_nivel_servicio_y_cuantil():
    assert nivel_servicio(1.0, 0.2) == pytest.approx(1 / 1.2)
    assert nivel_servicio(1.0, 2.5) == pytest.approx(1 / 3.5)
    x = np.arange(1, 101, dtype=float)
    assert cuantil_ponderado(x, np.ones(100), 0.9) == pytest.approx(90)


def test_multiplicador_sigue_a_los_costos():
    rng = np.random.default_rng(0)
    esperada = np.full(2000, 100.0)
    real = rng.normal(100, 20, 2000).clip(0)
    fam = np.array(["barata_sobrar"] * 1000 + ["cara_sobrar"] * 1000)
    m = calibrar_multiplicadores(real, esperada, fam, {"barata_sobrar": (1.0, 0.1), "cara_sobrar": (1.0, 2.5)},
                                 minimo=0.5, maximo=3, min_puntos=30)
    assert m["barata_sobrar"] > 1.15 > 1.0 > 0.92 > m["cara_sobrar"]


def test_costo_de_negocio():
    assert costo(np.array([10.0]), np.array([7.0]), 1.5, 2.0)[0] == pytest.approx(4.5)
    assert costo(np.array([10.0]), np.array([12.0]), 1.5, 2.0)[0] == pytest.approx(4.0)


def test_criterio_de_reemplazo():
    p = {"mejora_minima_costo": 0.01, "max_degradacion_wape": 0.05}
    assert decidir({"costo": 100, "wape": 0.2}, None, p)[0]
    assert decidir({"costo": 90, "wape": 0.2}, {"costo": 100, "wape": 0.2}, p)[0]
    assert not decidir({"costo": 99.5, "wape": 0.2}, {"costo": 100, "wape": 0.2}, p)[0]
    assert not decidir({"costo": 90, "wape": 0.3}, {"costo": 100, "wape": 0.2}, p)[0]


def test_primer_modelo_supera_al_baseline(modelo_entrenado):
    modelo, resumen = modelo_entrenado
    m = resumen["metricas_candidatos"]
    assert modelo.tipo == "lgbm" and m["lgbm"]["costo"] < m["baseline"]["costo"]
    assert set(modelo.multiplicadores) == {"abarrotes", "bebidas", "lacteos", "panaderia", "frutas_verduras",
                                           "licores"}
    # familia con faltante caro y sobrante barato -> sugerir más; panadería (sobrante caro) -> menos
    assert modelo.multiplicadores["licores"] > modelo.multiplicadores["panaderia"]


def test_sin_datos_nuevos_no_se_reemplaza(settings, repo_base, modelo_entrenado):
    reg = RegistroMemoria()
    reg.versiones["1"] = modelo_entrenado[0]
    reg.vigente_version = "1"
    r = ciclo(settings, repo_base, reg, origen="pruebas")
    assert r["promovido"] is False and r["version"] == "1" and "insuficientes" in r["motivo"]


def test_fallo_al_registrar_no_cambia_el_vigente(settings, repo_base):
    class RegistroQueFalla(RegistroMemoria):
        def publicar_ciclo(self, *a, **k):
            raise RuntimeError("MLflow se cayó a mitad del ciclo")

    reg = RegistroQueFalla()
    with pytest.raises(RuntimeError):
        ciclo(settings, repo_base, reg, origen="pruebas")
    assert reg.vigente_version is None


def test_observaciones_sueltas_no_bastan_para_reemplazar(settings, tmp_path, modelo_entrenado):
    """Pocas observaciones dispersas en fechas futuras no deben promover un modelo (caso real de desarrollo)."""
    from datetime import date

    from conftest import nuevo_repo
    from novamercado.limpieza.reporte import ReporteCalidad
    from novamercado.schemas import ObservacionLimpia, VentaObservada

    repo = nuevo_repo(settings, tmp_path / "sueltas.db")
    for d in (date(2025, 3, 20), date(2025, 3, 25), date(2025, 3, 30)):
        repo.guardar_observaciones({}, ObservacionLimpia(tienda_id=1, fecha=d, ventas=[
            VentaObservada(familia_id="abarrotes", ventas=999)]), ReporteCalidad())
    reg = RegistroMemoria()
    reg.versiones["1"] = modelo_entrenado[0]
    reg.vigente_version = "1"
    r = ciclo(settings, repo, reg, origen="pruebas")
    assert r["promovido"] is False and r["version"] == "1" and "insuficiente" in r["motivo"].lower()
