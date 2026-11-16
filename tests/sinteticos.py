"""Datos sintéticos con el mismo formato que los archivos publicados (para pruebas sin los datos reales)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

FAMILIAS = [("abarrotes", "Abarrotes", 1.0, 0.2), ("bebidas", "Bebidas", 1.0, 0.2), ("lacteos", "Lácteos", 1.0, 1.2),
            ("panaderia", "Panadería", 1.0, 2.5), ("frutas_verduras", "Frutas y verduras", 1.0, 2.0),
            ("licores", "Licores", 2.0, 0.1)]
TIENDAS = [(1, "NovaMercado Zona 10", "Guatemala", "hipermercado", "2019-01-01"),
           (2, "NovaMercado Mixco", "Mixco", "supermercado", "2019-01-01"),
           (3, "NovaMercado Express Zona 1", "Guatemala", "express", "2019-01-01"),
           (4, "NovaMercado Antigua", "Antigua Guatemala", "supermercado", "2024-09-01")]
INICIO, FIN = "2024-03-01", "2025-02-28"


def generar(carpeta: Path, seed: int = 0) -> Path:
    rng = np.random.default_rng(seed)
    carpeta.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(TIENDAS, columns=["tienda_id", "nombre", "ciudad", "tipo", "fecha_apertura"]).assign(
        departamento="x").to_csv(carpeta / "tiendas.csv", index=False)
    pd.DataFrame([(f, n) for f, n, _, _ in FAMILIAS], columns=["familia_id", "nombre"]).to_csv(
        carpeta / "familias.csv", index=False)
    pd.DataFrame([(f, cf, cs) for f, _, cf, cs in FAMILIAS],
                 columns=["familia_id", "costo_faltante", "costo_sobrante"]).to_csv(carpeta / "costos.csv", index=False)
    fer = []
    for y in (2024, 2025, 2026):
        fer += [(f"{y}-05-01", "Día del Trabajo", "feriado", "nacional"),
                (f"{y}-07-15", "Pago del Bono 14", "pago", "nacional"),
                (f"{y}-07-25", "Santiago Apóstol", "feriado", "Antigua Guatemala"),
                (f"{y}-12-24", "Nochebuena", "medio_dia", "nacional"),
                (f"{y}-12-25", "Navidad", "feriado", "nacional")]
    pd.DataFrame(fer, columns=["fecha", "nombre", "tipo", "ambito"]).to_csv(carpeta / "feriados.csv", index=False)

    fechas = pd.date_range(INICIO, FIN, freq="D")
    filas = []
    escala_formato = {"hipermercado": 1.6, "supermercado": 1.0, "express": 0.4}
    base_fam = {"abarrotes": 500, "bebidas": 400, "lacteos": 250, "panaderia": 180, "frutas_verduras": 220,
                "licores": 70}
    dow = np.array([0.9, 0.85, 0.9, 0.95, 1.1, 1.3, 1.2])
    for tid, _, _, formato, apertura in TIENDAS:
        for fam, *_ in FAMILIAS:
            nivel = base_fam[fam] * escala_formato[formato]
            for d in fechas:
                if d < pd.Timestamp(apertura):
                    continue
                promo = int(rng.random() < 0.3) * int(rng.integers(1, 8))
                mu = nivel * dow[d.dayofweek] * (1 + 0.04 * promo) * (1.3 if d.month == 12 else 1.0)
                filas.append((d.date().isoformat(), tid, fam, int(rng.poisson(mu)), promo))
    pd.DataFrame(filas, columns=["fecha", "tienda_id", "familia_id", "ventas", "en_promocion"]).to_csv(
        carpeta / "historico_ventas.csv", index=False)
    return carpeta
