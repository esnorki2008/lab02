"""Lectura de los archivos publicados (``datos/``) a tablas con nombres canónicos.

Los encabezados se normalizan (minúsculas, sin acentos) y se reconocen sinónimos razonables, para no
depender de la forma exacta en que el equipo docente nombre cada columna.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from novamercado.config import Settings
from novamercado.textos import normalizar_clave

log = logging.getLogger(__name__)

# columna canónica -> sinónimos (ya normalizados con normalizar_clave)
SINONIMOS: dict[str, dict[str, list[str]]] = {
    "tiendas": {
        "tienda_id": ["tienda_id", "id_tienda", "tienda", "store_id", "store_nbr", "id", "codigo"],
        "nombre": ["nombre", "nombre_tienda", "tienda_nombre", "name"],
        "ciudad": ["ciudad", "city", "municipio", "localidad"],
        "formato": ["formato", "tipo", "format", "type", "tipo_tienda"],
        "fecha_apertura": ["fecha_apertura", "apertura", "fecha_inicio", "opening_date"],
    },
    "familias": {
        "familia_id": ["familia_id", "id_familia", "familia", "family", "id", "codigo"],
        "nombre": ["nombre", "nombre_familia", "familia_nombre", "descripcion", "name"],
    },
    "historico": {
        "fecha": ["fecha", "date", "dia"],
        "tienda_id": ["tienda_id", "id_tienda", "tienda", "store_id", "store_nbr"],
        "familia_id": ["familia_id", "id_familia", "familia", "family"],
        "ventas": ["ventas", "venta", "sales", "unidades", "ventas_unidades"],
        "en_promocion": ["en_promocion", "promocion", "promociones", "onpromotion", "promo"],
    },
    "feriados": {
        "fecha": ["fecha", "date", "dia"],
        "tipo": ["tipo", "type", "tipo_evento", "categoria", "clase"],
        "ambito": ["ambito", "alcance", "locale", "locale_name", "ciudad"],
        "descripcion": ["descripcion", "nombre", "evento", "description", "detalle"],
    },
    "costos": {
        "familia_id": ["familia_id", "id_familia", "familia", "family"],
    },
}
REQUERIDAS = {
    "tiendas": ["tienda_id"],
    "familias": ["familia_id"],
    "historico": ["fecha", "tienda_id", "familia_id", "ventas"],
    "feriados": ["fecha"],
    "costos": ["familia_id", "costo_faltante", "costo_sobrante"],
}


class DatosNoEncontrados(FileNotFoundError):
    """Faltan archivos publicados en la carpeta de datos."""


@dataclass
class DatosPublicados:
    tiendas: pd.DataFrame
    familias: pd.DataFrame
    historico: pd.DataFrame
    feriados: pd.DataFrame
    costos: pd.DataFrame
    hashes: dict[str, str]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def localizar(settings: Settings) -> dict[str, Path]:
    """Busca cada archivo publicado (recursivamente) en la carpeta de datos."""
    base = settings.data_dir
    out: dict[str, Path] = {}
    faltan = []
    for clave, nombre in settings.params["datos"]["archivos"].items():
        candidatos = sorted(p for p in base.rglob("*") if p.is_file() and p.name.lower() == nombre.lower())
        candidatos = [p for p in candidatos if "__macosx" not in str(p).lower()]
        if not candidatos:
            faltan.append(nombre)
        else:
            out[clave] = candidatos[0]
    if faltan:
        raise DatosNoEncontrados(f"No se encontraron {faltan} dentro de {base}")
    return out


def _leer_csv(path: Path) -> pd.DataFrame:
    # sep=None detecta ',' o ';'; utf-8-sig elimina BOM.
    for enc in ("utf-8-sig", "latin-1"):
        try:
            return pd.read_csv(path, sep=None, engine="python", encoding=enc, dtype=str, keep_default_na=True)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"No se pudo leer {path}")


def _renombrar(df: pd.DataFrame, tabla: str) -> pd.DataFrame:
    df = df.rename(columns={c: normalizar_clave(c) for c in df.columns})
    mapping: dict[str, str] = {}
    for canon, sinonimos in SINONIMOS.get(tabla, {}).items():
        if canon in df.columns:
            continue
        for s in sinonimos:
            if s in df.columns and s not in mapping and s not in SINONIMOS[tabla]:
                mapping[s] = canon
                break
    df = df.rename(columns=mapping)
    if tabla == "costos":
        for canon, pistas in (("costo_faltante", ("falt", "under", "quiebre", "short")),
                              ("costo_sobrante", ("sobr", "over", "merma", "exceso"))):
            if canon not in df.columns:
                cand = [c for c in df.columns if any(p in c for p in pistas)]
                if cand:
                    df = df.rename(columns={cand[0]: canon})
    faltan = [c for c in REQUERIDAS[tabla] if c not in df.columns]
    if faltan:
        raise ValueError(f"{tabla}: faltan columnas {faltan}; columnas presentes {list(df.columns)}")
    return df


def _fechas(s: pd.Series) -> pd.Series:
    out = pd.to_datetime(s.str.strip(), format="%Y-%m-%d", errors="coerce")
    resto = out.isna() & s.notna()
    if resto.any():
        out[resto] = pd.to_datetime(s[resto], errors="coerce", dayfirst=True)
    return out.dt.normalize()


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.astype(str).str.strip().str.replace(",", "", regex=False), errors="coerce")


def leer_tiendas(path: Path) -> pd.DataFrame:
    df = _renombrar(_leer_csv(path), "tiendas")
    df["tienda_id"] = _num(df["tienda_id"]).astype("Int64")
    df = df.dropna(subset=["tienda_id"]).copy()
    df["tienda_id"] = df["tienda_id"].astype(int)
    for c in ("nombre", "ciudad", "formato"):
        df[c] = df[c].str.strip() if c in df.columns else None
    if "fecha_apertura" in df.columns:
        df["fecha_apertura"] = _fechas(df["fecha_apertura"]).dt.date
    else:
        df["fecha_apertura"] = None
    cols = ["tienda_id", "nombre", "ciudad", "formato", "fecha_apertura"]
    return df[cols].drop_duplicates("tienda_id").reset_index(drop=True)


def leer_familias(path: Path) -> pd.DataFrame:
    df = _renombrar(_leer_csv(path), "familias")
    df["familia_id"] = df["familia_id"].str.strip()
    df["nombre"] = df["nombre"].str.strip() if "nombre" in df.columns else df["familia_id"]
    return df[["familia_id", "nombre"]].dropna(subset=["familia_id"]).drop_duplicates("familia_id").reset_index(
        drop=True)


def leer_historico(path: Path) -> pd.DataFrame:
    df = _renombrar(_leer_csv(path), "historico")
    df["fecha"] = _fechas(df["fecha"])
    df["tienda_id"] = _num(df["tienda_id"])
    df["familia_id"] = df["familia_id"].str.strip()
    df["ventas"] = _num(df["ventas"])
    df["en_promocion"] = _num(df["en_promocion"]) if "en_promocion" in df.columns else 0.0
    n0 = len(df)
    df = df.dropna(subset=["fecha", "tienda_id", "familia_id"])
    df = df[df["ventas"].isna() | (df["ventas"] >= 0)]
    if len(df) < n0:
        log.warning("historico: %d filas descartadas por llave inválida o ventas negativas", n0 - len(df))
    df["tienda_id"] = df["tienda_id"].astype(int)
    df = df.drop_duplicates(["tienda_id", "familia_id", "fecha"], keep="last")
    return df[["fecha", "tienda_id", "familia_id", "ventas", "en_promocion"]].reset_index(drop=True)


def leer_feriados(path: Path) -> pd.DataFrame:
    df = _renombrar(_leer_csv(path), "feriados")
    df["fecha"] = _fechas(df["fecha"])
    for c, default in (("tipo", "feriado"), ("ambito", "nacional"), ("descripcion", "")):
        df[c] = df[c].fillna(default).str.strip() if c in df.columns else default
    return df.dropna(subset=["fecha"])[["fecha", "tipo", "ambito", "descripcion"]].reset_index(drop=True)


def leer_costos(path: Path) -> pd.DataFrame:
    df = _renombrar(_leer_csv(path), "costos")
    df["familia_id"] = df["familia_id"].str.strip()
    df["costo_faltante"] = _num(df["costo_faltante"])
    df["costo_sobrante"] = _num(df["costo_sobrante"])
    return df[["familia_id", "costo_faltante", "costo_sobrante"]].dropna().reset_index(drop=True)


def leer_publicados(settings: Settings) -> DatosPublicados:
    rutas = localizar(settings)
    return DatosPublicados(
        tiendas=leer_tiendas(rutas["tiendas"]),
        familias=leer_familias(rutas["familias"]),
        historico=leer_historico(rutas["historico"]),
        feriados=leer_feriados(rutas["feriados"]),
        costos=leer_costos(rutas["costos"]),
        hashes={k: sha256(p) for k, p in rutas.items()},
    )
