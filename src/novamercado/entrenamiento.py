"""Ciclo de entrenamiento, evaluación y criterio de reemplazo (champion/challenger).

Se usa igual en el arranque (primer modelo) y en el DAG de reentrenamiento. No depende de MLflow: recibe un
``registro`` (``novamercado.registro.RegistroMLflow`` en producción, uno en memoria en las pruebas).

Esquema temporal (``fin`` = último día con ventas conocidas):

    ... entrenamiento ... | C: calibración | E: evaluación |
                          ^C_ini           ^E_ini          ^fin

* ``E`` = días que NINGÚN modelo comparado vio al entrenar: los últimos ``dias_evaluacion`` días, recortados
  para empezar después del fin de entrenamiento del modelo vigente. Si ``E`` tiene menos de
  ``dias_minimos_evaluacion`` días no hay evidencia nueva y no se reemplaza. Como ``E`` es corta (14 días)
  el candidato entrena con todas las observaciones anteriores a ``E``, aunque el vigente sea antiguo.
* ``C`` = los ``dias_calibracion`` días previos a ``E``.
* Cada candidato se entrena con datos < C_ini para calibrar la cantidad sugerida en ``C`` y con datos < E_ini
  para medir en ``E`` (fuera de muestra). El vigente se mide en ``E`` con su propia calibración.
* Si el ganador reemplaza al vigente se re-entrena con todo (≤ fin) y se registra.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import timedelta

import numpy as np
import pandas as pd

from novamercado.config import Settings
from novamercado.features import columnas_modelo, construir_features, ocultar_recientes, tipos_evento
from novamercado.modelos import ModeloVentas, calibrar_multiplicadores, entrenar, metricas
from novamercado.textos import normalizar_clave

log = logging.getLogger(__name__)


@dataclass
class Insumos:
    tiendas: pd.DataFrame
    feriados: pd.DataFrame
    costos: dict[str, tuple[float, float]]
    familias: list[str]
    formatos: list[str]
    tipos: list[str]
    panel: pd.DataFrame


def cargar_insumos(repo) -> Insumos:
    tiendas = repo.tabla("tiendas")
    feriados = repo.tabla("feriados")
    costos_df = repo.tabla("costos")
    familias = sorted(repo.tabla("familias")["familia_id"].tolist())
    formatos = sorted({normalizar_clave(x or "") for x in tiendas["formato"]})
    costos = {r.familia_id: (float(r.costo_faltante), float(r.costo_sobrante)) for r in costos_df.itertuples()}
    for f in familias:
        costos.setdefault(f, (1.0, 1.0))
    return Insumos(tiendas=tiendas, feriados=feriados, costos=costos, familias=familias, formatos=formatos,
                   tipos=tipos_evento(feriados), panel=repo.ventas())


def _features(ins: Insumos, familias: list[str], formatos: list[str], tipos: list[str]) -> pd.DataFrame:
    return construir_features(ins.panel, ins.tiendas, ins.feriados, familias=familias, formatos=formatos, tipos=tipos)


def _evaluar(modelo: ModeloVentas, X: pd.DataFrame, costos) -> tuple[dict, np.ndarray]:
    esperada = modelo.venta_esperada(X)
    fam = X["familia_id"].to_numpy()
    sugerida = modelo.cantidad(esperada, fam)
    cf = np.array([costos[f][0] for f in fam])
    cs = np.array([costos[f][1] for f in fam])
    return metricas(X["ventas"].to_numpy(float), esperada, sugerida, cf, cs), esperada


def decidir(nuevo: dict, vigente: dict | None, params: dict) -> tuple[bool, str]:
    """Criterio de reemplazo: costo de negocio en E al menos ``mejora_minima_costo`` menor y WAPE que no
    empeore más de ``max_degradacion_wape`` (relativo)."""
    if vigente is None:
        return True, f"Primer modelo: costo en validación {nuevo['costo']:,.0f}".replace(",", " ")
    mejora = params["mejora_minima_costo"]
    deg = params["max_degradacion_wape"]
    c_n, c_v = nuevo["costo"], vigente["costo"]
    txt = f"Costo en validación {c_n:,.0f} vs {c_v:,.0f} del vigente".replace(",", " ")
    if c_n > c_v * (1 - mejora):
        return False, f"{txt}: no mejora al menos {mejora:.0%}"
    if nuevo["wape"] > vigente["wape"] * (1 + deg):
        return False, f"{txt}, pero WAPE {nuevo['wape']:.3f} vs {vigente['wape']:.3f} empeora más de {deg:.0%}"
    return True, txt


def ciclo(settings: Settings, repo, registro, *, origen: str, ejecucion: str | None = None) -> dict:
    """Entrena candidatos, compara con el vigente y, si corresponde, registra y promueve. Devuelve un resumen."""
    t0 = time.time()
    p_mod = settings.params["modelo"]
    p_cant = settings.params["cantidad_sugerida"]
    ins = cargar_insumos(repo)
    if ins.panel["ventas"].notna().sum() == 0:
        raise RuntimeError("No hay ventas en la base de datos: ¿se cargaron los archivos publicados?")
    fin = ins.panel.loc[ins.panel["ventas"].notna(), "fecha"].max()
    X = _features(ins, ins.familias, ins.formatos, ins.tipos)
    X = X[X["fecha"] <= fin]
    validas = X["ventas"].notna() & X["nivel28"].notna() & (X["n28"] >= 7)
    validas &= X["fecha"] >= fin - timedelta(days=p_mod["ventana_entrenamiento_dias"])
    X = X[validas].reset_index(drop=True)
    columnas = columnas_modelo(ins.tipos)

    vigente = registro.vigente()
    version_vigente, modelo_vigente = vigente if vigente else (None, None)
    e_ini = fin - timedelta(days=int(p_mod["dias_evaluacion"]) - 1)
    if modelo_vigente is not None and modelo_vigente.meta.get("fin_entrenamiento"):
        e_ini = max(e_ini, pd.Timestamp(modelo_vigente.meta["fin_entrenamiento"]) + timedelta(days=1))
    dias_e = (fin - e_ini).days + 1
    c_ini = e_ini - timedelta(days=int(p_mod["dias_calibracion"]))
    resumen = {
        "origen": origen, "ejecucion": ejecucion, "fin_datos": fin.date().isoformat(),
        "evaluacion_inicio": e_ini.date().isoformat(), "dias_evaluacion": int(max(dias_e, 0)),
        "calibracion_inicio": c_ini.date().isoformat(), "version_vigente_previa": version_vigente,
        "n_observaciones_operacion": _n_obs(repo),
    }
    if modelo_vigente is not None and dias_e < int(p_mod["dias_minimos_evaluacion"]):
        motivo = (f"Datos nuevos insuficientes: {max(dias_e, 0)} días desde el fin de entrenamiento del vigente "
                  f"(mínimo {p_mod['dias_minimos_evaluacion']}); se conserva la versión {version_vigente}")
        resumen.update(promovido=False, version=version_vigente, motivo=motivo, segundos=round(time.time() - t0, 1))
        registro.publicar_ciclo(resumen, [], None, False)
        return resumen

    en_c = (X["fecha"] >= c_ini) & (X["fecha"] < e_ini)
    en_e = X["fecha"] >= e_ini
    X_e = X[en_e]
    seed = int(p_mod["lgbm"].get("seed", 11))
    candidatos = []
    for tipo in p_mod["candidatos"]:
        kw = dict(columnas=columnas, familias=ins.familias, formatos=ins.formatos, tipos_evento=ins.tipos,
                  params=p_mod["lgbm"])
        tr_c = ocultar_recientes(X[X["fecha"] < c_ini], p_mod["frac_rezagos_faltantes"], seed)
        m_cal = entrenar(tipo, tr_c, **kw)
        esp_c = m_cal.venta_esperada(X[en_c])
        mult = calibrar_multiplicadores(X.loc[en_c, "ventas"], esp_c, X.loc[en_c, "familia_id"], ins.costos,
                                        minimo=p_cant["multiplicador_min"], maximo=p_cant["multiplicador_max"],
                                        min_puntos=p_cant["min_puntos_familia"])
        tr_e = ocultar_recientes(X[X["fecha"] < e_ini], p_mod["frac_rezagos_faltantes"], seed + 1)
        m_eval = entrenar(tipo, tr_e, **kw)
        m_eval.multiplicadores = mult
        met, esp_e = _evaluar(m_eval, X_e, ins.costos)
        candidatos.append({"tipo": tipo, "metricas": met, "multiplicadores": mult,
                           "oos": (pd.concat([X.loc[en_c, ["ventas", "familia_id"]], X_e[["ventas", "familia_id"]]]),
                                   np.concatenate([esp_c, esp_e])),
                           "n_entrenamiento": int(len(tr_e))})
        log.info("candidato %s: %s", tipo, met)

    met_vig = None
    if modelo_vigente is not None:
        Xv = X_e
        if (modelo_vigente.familias, modelo_vigente.formatos, modelo_vigente.tipos_evento) != (
                ins.familias, ins.formatos, ins.tipos):
            Xv = _features(ins, modelo_vigente.familias, modelo_vigente.formatos, modelo_vigente.tipos_evento)
            Xv = Xv.merge(X_e[["tienda_id", "familia_id", "fecha"]], on=["tienda_id", "familia_id", "fecha"])
        met_vig, _ = _evaluar(modelo_vigente, Xv, ins.costos)

    mejor = min(candidatos, key=lambda c: c["metricas"]["costo"])
    promover, motivo = decidir(mejor["metricas"], met_vig, settings.params["reemplazo"])
    resumen.update(candidato=mejor["tipo"], metricas_candidato=mejor["metricas"], metricas_vigente=met_vig,
                   metricas_candidatos={c["tipo"]: c["metricas"] for c in candidatos})

    nuevo = None
    if promover:
        nuevo = entrenar(mejor["tipo"], ocultar_recientes(X, p_mod["frac_rezagos_faltantes"], seed + 2),
                         columnas=columnas, familias=ins.familias, formatos=ins.formatos, tipos_evento=ins.tipos,
                         params=p_mod["lgbm"])
        reales, esperadas = mejor["oos"]
        nuevo.multiplicadores = calibrar_multiplicadores(
            reales["ventas"], esperadas, reales["familia_id"], ins.costos, minimo=p_cant["multiplicador_min"],
            maximo=p_cant["multiplicador_max"], min_puntos=p_cant["min_puntos_familia"])
        nuevo.meta = {"fin_entrenamiento": fin.date().isoformat(), "origen": origen, "ejecucion": ejecucion,
                      "metricas_evaluacion": mejor["metricas"], "evaluacion_inicio": resumen["evaluacion_inicio"],
                      "n_entrenamiento": int(len(X)), "version_previa": version_vigente}
    resumen["segundos"] = round(time.time() - t0, 1)
    version_nueva = registro.publicar_ciclo(resumen, candidatos, nuevo, promover)
    resumen.update(promovido=bool(promover and version_nueva), version=version_nueva or version_vigente,
                   motivo=motivo if (promover and version_nueva) or not promover
                   else "No se pudo registrar el modelo nuevo; se conserva el vigente")
    if resumen["promovido"]:
        resumen["motivo"] = f"{motivo}; promovida la versión {version_nueva}"
    return resumen


def _n_obs(repo) -> int:
    import sqlalchemy as sa

    from novamercado.db import observaciones
    with repo.engine.connect() as c:
        return int(c.execute(sa.select(sa.func.count()).select_from(observaciones)).scalar() or 0)
