"""Modelos de pronóstico (baseline y LightGBM), cantidad sugerida y métricas."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from novamercado.features import CATEGORICAS


# ------------------------------------------------------------------------------------------ métricas
def costo(real: np.ndarray, sugerido: np.ndarray, cf: np.ndarray, cs: np.ndarray) -> np.ndarray:
    """costo = cf·max(real − sugerido, 0) + cs·max(sugerido − real, 0), por fila."""
    real = np.asarray(real, float)
    sugerido = np.asarray(sugerido, float)
    return cf * np.maximum(real - sugerido, 0) + cs * np.maximum(sugerido - real, 0)


def metricas(real: np.ndarray, esperada: np.ndarray, sugerida: np.ndarray, cf: np.ndarray,
             cs: np.ndarray) -> dict[str, float]:
    real = np.asarray(real, float)
    esperada = np.asarray(esperada, float)
    err = esperada - real
    total = max(real.sum(), 1e-9)
    return {
        "costo": float(costo(real, sugerida, cf, cs).sum()),
        "costo_por_dia_serie": float(costo(real, sugerida, cf, cs).mean()),
        "wape": float(np.abs(err).sum() / total),
        "mae": float(np.abs(err).mean()),
        "rmse": float(np.sqrt((err ** 2).mean())),
        "sesgo": float(err.sum() / total),
        "n": int(len(real)),
    }


# ------------------------------------------------------------------------------------------ newsvendor
def nivel_servicio(cf: float, cs: float) -> float:
    """Cuantil óptimo del newsvendor: τ = cf / (cf + cs)."""
    return float(cf / (cf + cs))


def cuantil_ponderado(x: np.ndarray, w: np.ndarray, q: float) -> float:
    orden = np.argsort(x)
    x, w = x[orden], w[orden]
    acum = np.cumsum(w)
    if acum[-1] <= 0:
        return 1.0
    return float(x[np.searchsorted(acum, q * acum[-1], side="left").clip(0, len(x) - 1)])


def calibrar_multiplicadores(real: np.ndarray, esperada: np.ndarray, familias: np.ndarray,
                             costos: dict[str, tuple[float, float]], *, minimo: float, maximo: float,
                             min_puntos: int) -> dict[str, float]:
    """Multiplicador m_f que minimiza el costo de ``m_f · venta_esperada`` en validación para cada familia.

    Minimizar Σ cf·(y − m·ŷ)⁺ + cs·(m·ŷ − y)⁺ en m equivale al cuantil τ_f = cf/(cf+cs) de y/ŷ ponderado
    por ŷ. Con pocos puntos se usa el de todas las familias al mismo τ.
    """
    real = np.asarray(real, float)
    esperada = np.asarray(esperada, float)
    familias = np.asarray(familias)
    ok = esperada > 0.5
    ratio = np.where(ok, real / np.maximum(esperada, 1e-9), np.nan)
    out: dict[str, float] = {}
    for fam, (cf, cs) in costos.items():
        tau = nivel_servicio(cf, cs)
        m = ok & (familias == fam)
        if m.sum() < min_puntos:
            m = ok
        if m.sum() == 0:
            out[fam] = 1.0
            continue
        out[fam] = float(np.clip(cuantil_ponderado(ratio[m], esperada[m], tau), minimo, maximo))
    return out


def cantidad_sugerida(esperada: np.ndarray, familias: np.ndarray, multiplicadores: dict[str, float]) -> np.ndarray:
    m = np.array([multiplicadores.get(f, 1.0) for f in familias], float)
    return np.maximum(np.rint(np.asarray(esperada, float) * m), 0).astype(int)


# ------------------------------------------------------------------------------------------ modelos
@dataclass
class ModeloVentas:
    """Paquete completo de un modelo: estimador + todo lo necesario para construir features y la cantidad."""

    tipo: str                                  # "baseline" | "lgbm"
    columnas: list[str]
    familias: list[str]
    formatos: list[str]
    tipos_evento: list[str]
    estimador: object | None = None
    multiplicadores: dict[str, float] = field(default_factory=dict)
    meta: dict = field(default_factory=dict)

    def venta_esperada(self, X: pd.DataFrame) -> np.ndarray:
        escala = X["escala"].to_numpy(float)
        if self.tipo == "baseline":
            r = X["r_dow4"].to_numpy(float)
            r = np.where(np.isnan(r), X["r_media28"].to_numpy(float), r)
            r = np.where(np.isnan(r), 1.0, r)
        else:
            r = self.estimador.predict(X[self.columnas], num_threads=1)
        return np.maximum(r * escala - 0.0, 0.0)

    def cantidad(self, esperada: np.ndarray, familias: np.ndarray) -> np.ndarray:
        return cantidad_sugerida(esperada, familias, self.multiplicadores)


def entrenar(tipo: str, X: pd.DataFrame, *, columnas: list[str], familias: list[str], formatos: list[str],
             tipos_evento: list[str], params: dict) -> ModeloVentas:
    modelo = ModeloVentas(tipo=tipo, columnas=columnas, familias=familias, formatos=formatos,
                          tipos_evento=tipos_evento)
    if tipo == "baseline":
        return modelo
    if tipo != "lgbm":
        raise ValueError(f"Tipo de modelo desconocido: {tipo}")
    import lightgbm as lgb

    p = dict(params)
    rondas = int(p.pop("n_estimators", 500))
    p.setdefault("objective", "regression")
    p.update(verbose=p.get("verbose", -1), deterministic=True, force_row_wise=True)
    y = (X["ventas"] / X["escala"]).to_numpy(float)
    ds = lgb.Dataset(X[columnas], y, weight=X["escala"].to_numpy(float),
                     categorical_feature=[c for c in CATEGORICAS if c in columnas], free_raw_data=True)
    modelo.estimador = lgb.train(p, ds, num_boost_round=rondas)
    return modelo
