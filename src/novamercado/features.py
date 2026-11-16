"""Features del pronóstico diario. La MISMA función construye las filas de entrenamiento y la de predicción.

Regla temporal: la fila de la fecha D sólo usa ventas de fechas < D (todo lo de ventas va desplazado al
menos un día) y, del propio día D, sólo lo que se conoce por adelantado: calendario, feriados y la
promoción que llega en la solicitud.

Las magnitudes se expresan relativas a la ``escala`` de la serie (media de los últimos 28 días con dato),
así el modelo se adapta a cambios de nivel de la demanda sin necesidad de haberlos visto al entrenar.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from novamercado.textos import compactar, normalizar_clave

LLAVE = ["tienda_id", "familia_id"]
LAGS = [1, 2, 3, 7, 14, 21, 28]
MEDIAS = [7, 14, 28, 56]
CATEGORICAS = ["tienda_id", "familia_cod", "formato_cod"]
TIPOS_BASE = ["feriado", "comercial", "pago", "medio_dia"]


def tipos_evento(feriados: pd.DataFrame) -> list[str]:
    """Tipos de evento presentes en el calendario (normalizados), en orden estable."""
    vistos = {normalizar_clave(t) for t in feriados.get("tipo", pd.Series(dtype=str)).dropna()}
    return sorted(vistos | set(TIPOS_BASE))


def completar_grilla(panel: pd.DataFrame, hasta: pd.Timestamp | None = None) -> pd.DataFrame:
    """Una fila por serie y día, desde el primer dato de la serie hasta ``hasta`` (faltantes = NaN)."""
    panel = panel.copy()
    panel["fecha"] = pd.to_datetime(panel["fecha"])
    hasta = pd.Timestamp(hasta) if hasta is not None else panel["fecha"].max()
    inicio = panel.groupby(LLAVE)["fecha"].min()
    partes = []
    for (t, f), ini in inicio.items():
        partes.append(pd.DataFrame({"tienda_id": t, "familia_id": f, "fecha": pd.date_range(ini, hasta, freq="D")}))
    grilla = pd.concat(partes, ignore_index=True) if partes else panel[LLAVE + ["fecha"]].iloc[:0]
    out = grilla.merge(panel, on=LLAVE + ["fecha"], how="left")
    return out.sort_values(LLAVE + ["fecha"]).reset_index(drop=True)


def _nivel(x: pd.Series, n: int) -> pd.Series:
    """Media de los últimos ``n`` valores CONOCIDOS antes de cada fila (sin importar cuán atrás estén)."""
    k = x.dropna()
    r = k.rolling(n, min_periods=1).mean()
    return r.reindex(x.index).ffill().shift(1)


def eventos(feriados: pd.DataFrame, tiendas: pd.DataFrame, fechas: pd.DatetimeIndex,
            tipos: list[str]) -> pd.DataFrame:
    """Banderas de evento por tienda y fecha (nacional o de la ciudad de la tienda), del día, la víspera y el día
    siguiente, y días al/desde el próximo feriado."""
    fer = feriados.copy()
    fer["fecha"] = pd.to_datetime(fer["fecha"])
    fer["tipo_n"] = fer["tipo"].map(normalizar_clave)
    fer["ambito_n"] = fer["ambito"].fillna("nacional").map(compactar)
    filas = []
    fechas = pd.DatetimeIndex(fechas)
    for _, t in tiendas.iterrows():
        ciudad = compactar(t.get("ciudad") or "")
        aplica = fer[(fer["ambito_n"] == "nacional") | (fer["ambito_n"] == ciudad)]
        cal = pd.DataFrame({"fecha": fechas})
        cal["tienda_id"] = int(t["tienda_id"])
        for tipo in tipos:
            dias = set(aplica.loc[aplica["tipo_n"] == tipo, "fecha"])
            cal[f"ev_{tipo}"] = cal["fecha"].isin(dias).astype(np.int8)
            cal[f"ev_{tipo}_manana"] = (cal["fecha"] + pd.Timedelta(days=1)).isin(dias).astype(np.int8)
            cal[f"ev_{tipo}_ayer"] = (cal["fecha"] - pd.Timedelta(days=1)).isin(dias).astype(np.int8)
        especiales = np.array(sorted(aplica.loc[aplica["tipo_n"] != "pago", "fecha"].unique()), dtype="datetime64[ns]")
        f = cal["fecha"].values.astype("datetime64[ns]")
        if len(especiales):
            i = np.searchsorted(especiales, f, side="left")
            prox = np.where(i < len(especiales), especiales[np.minimum(i, len(especiales) - 1)], np.datetime64("NaT"))
            j = np.searchsorted(especiales, f, side="right") - 1
            prev = np.where(j >= 0, especiales[np.maximum(j, 0)], np.datetime64("NaT"))
            cal["dias_a_evento"] = np.clip((prox - f).astype("timedelta64[D]").astype(float), 0, 30)
            cal["dias_desde_evento"] = np.clip((f - prev).astype("timedelta64[D]").astype(float), 0, 30)
        else:
            cal["dias_a_evento"] = 30.0
            cal["dias_desde_evento"] = 30.0
        cal[["dias_a_evento", "dias_desde_evento"]] = cal[["dias_a_evento", "dias_desde_evento"]].fillna(30.0)
        filas.append(cal)
    return pd.concat(filas, ignore_index=True)


def construir_features(panel: pd.DataFrame, tiendas: pd.DataFrame, feriados: pd.DataFrame, *,
                       familias: list[str], formatos: list[str], tipos: list[str],
                       hasta: pd.Timestamp | None = None) -> pd.DataFrame:
    """Devuelve una fila por serie y día con ``ventas`` (objetivo, puede ser NaN), ``escala`` y features.

    ``panel``: columnas tienda_id, familia_id, fecha, ventas, en_promocion. Para predecir el día D basta
    incluir una fila de D con ``ventas`` = NaN y la promoción de la solicitud.
    """
    df = completar_grilla(panel, hasta)
    g = df.groupby(LLAVE, sort=False)
    v = df["ventas"]

    for k in LAGS:
        df[f"lag{k}"] = g["ventas"].shift(k)
    s1 = g["ventas"].shift(1)
    for w in MEDIAS:
        df[f"media{w}"] = s1.groupby([df["tienda_id"], df["familia_id"]]).transform(
            lambda x, w=w: x.rolling(w, min_periods=1).mean())
    df["std28"] = s1.groupby([df["tienda_id"], df["familia_id"]]).transform(
        lambda x: x.rolling(28, min_periods=3).std())
    df["n28"] = s1.notna().groupby([df["tienda_id"], df["familia_id"]]).transform(
        lambda x: x.rolling(28, min_periods=1).sum())
    df["nivel28"] = g["ventas"].transform(lambda x: _nivel(x, 28))
    df["nivel7"] = g["ventas"].transform(lambda x: _nivel(x, 7))
    df["lag364"] = g["ventas"].shift(364)
    df["media7_364"] = g["ventas"].transform(lambda x: x.shift(361).rolling(7, min_periods=1).mean())
    ultima = df["fecha"].where(v.notna()).groupby([df["tienda_id"], df["familia_id"]]).transform(
        lambda x: x.ffill().shift(1))
    df["dias_sin_dato"] = (df["fecha"] - ultima).dt.days.astype(float).clip(upper=60)

    escala = df["nivel28"] + 1.0
    df["escala"] = escala
    for c in [f"lag{k}" for k in LAGS] + [f"media{w}" for w in MEDIAS] + ["std28", "nivel7", "lag364", "media7_364"]:
        df[f"r_{c}"] = df[c] / escala
    df["r_dow4"] = df[["lag7", "lag14", "lag21", "lag28"]].mean(axis=1, skipna=True) / escala
    df["log_escala"] = np.log1p(df["nivel28"])
    df["frac_faltante28"] = 1.0 - df["n28"] / 28.0

    # promociones: la del día viene en la solicitud; el resto, lo conocido antes de D
    p1 = g["en_promocion"].shift(1)
    df["promo"] = df["en_promocion"]
    df["promo_media28"] = p1.groupby([df["tienda_id"], df["familia_id"]]).transform(
        lambda x: x.rolling(28, min_periods=1).mean())
    df["promo_dif"] = df["promo"] - df["promo_media28"]
    df["promo_lag7"] = g["en_promocion"].shift(7)

    # calendario
    f = df["fecha"]
    df["dow"] = f.dt.dayofweek
    df["dia"] = f.dt.day
    df["mes"] = f.dt.month
    df["dia_anio"] = f.dt.dayofyear
    df["dias_fin_mes"] = (f.dt.days_in_month - f.dt.day).astype(int)

    # eventos y categóricas
    ev = eventos(feriados, tiendas, pd.DatetimeIndex(sorted(df["fecha"].unique())), tipos)
    df = df.merge(ev, on=["tienda_id", "fecha"], how="left")
    fmt = tiendas.set_index("tienda_id")["formato"].map(lambda x: normalizar_clave(x or ""))
    df["formato_cod"] = df["tienda_id"].map(fmt).map({x: i for i, x in enumerate(formatos)}).fillna(-1).astype(int)
    df["familia_cod"] = df["familia_id"].map({x: i for i, x in enumerate(familias)}).fillna(-1).astype(int)
    return df


def columnas_modelo(tipos: list[str]) -> list[str]:
    cols = [f"r_lag{k}" for k in LAGS] + [f"r_media{w}" for w in MEDIAS]
    cols += ["r_std28", "r_nivel7", "r_lag364", "r_media7_364", "r_dow4", "log_escala", "frac_faltante28",
             "dias_sin_dato", "promo", "promo_media28", "promo_dif", "promo_lag7",
             "dow", "dia", "mes", "dia_anio", "dias_fin_mes", "dias_a_evento", "dias_desde_evento"]
    for t in tipos:
        cols += [f"ev_{t}", f"ev_{t}_manana", f"ev_{t}_ayer"]
    return cols + CATEGORICAS


def ocultar_recientes(df: pd.DataFrame, frac: float, seed: int) -> pd.DataFrame:
    """Simula que las observaciones de los últimos 1-3 días no llegaron (como puede pasar en operación)."""
    if frac <= 0 or df.empty:
        return df
    df = df.copy()
    rng = np.random.default_rng(seed)
    elegidos = rng.random(len(df)) < frac
    k = rng.integers(1, 4, size=len(df))
    for j in (1, 2, 3):
        m = elegidos & (k >= j)
        df.loc[m, f"r_lag{j}"] = np.nan
    df.loc[elegidos, "dias_sin_dato"] = np.maximum(df.loc[elegidos, "dias_sin_dato"].fillna(1), k[elegidos] + 1)
    return df
