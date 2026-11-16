"""Simula la operación diaria de las tiendas contra el servicio levantado (como el programa de evaluación).

Cada día D, en orden: observaciones reales de D-1  ->  solicitudes de pronóstico de D (algunas defectuosas).
Cada ``--cada`` días dispara un reentrenamiento y espera su resultado. Al final calcula el costo de las
cantidades sugeridas con ``costos.csv`` y lo compara con una política ingenua (venta de hace 7 días).

Las ventas "reales" del periodo simulado se generan a partir del mismo día de la semana del año anterior,
con ruido y un CAMBIO DE DEMANDA a partir de ``--dia-cambio`` (algunas familias suben o bajan), para
ejercitar el ciclo de reentrenamiento.

Uso:  python scripts/simular_operacion.py --dias 28 --cada 7 --salida evidencia/simulacion
"""
from __future__ import annotations

import argparse
import json
import random
import time
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

CAMBIO = {"bebidas": 1.35, "frutas_verduras": 0.75, "licores": 1.25}


def defectuar_prediccion(p: dict, rng: random.Random) -> tuple[dict, str]:
    """Aplica (a veces) un defecto realista a una solicitud de pronóstico."""
    q = json.loads(json.dumps(p))
    tipo = rng.choice(["ninguno"] * 6 + ["tienda_texto", "familia_mayus", "promo_texto", "fecha_formato",
                                         "faltan_familias", "repetida", "campo_renombrado", "familia_typo",
                                         "promo_negativa", "fecha_hora", "tienda_desconocida", "promo_decimal"])
    f = q["familias"]
    if tipo == "tienda_texto":
        q["tienda_id"] = f"T{q['tienda_id']:02d}"
    elif tipo == "familia_mayus":
        f[0]["familia_id"] = f[0]["familia_id"].upper().replace("_", " ")
    elif tipo == "promo_texto":
        f[1]["en_promocion"] = str(f[1]["en_promocion"])
    elif tipo == "fecha_formato":
        d = q["fecha"]
        q["fecha"] = f"{d[8:10]}/{d[5:7]}/{d[0:4]}"
    elif tipo == "fecha_hora":
        q["fecha"] = q["fecha"] + "T00:00:00"
    elif tipo == "faltan_familias":
        q["familias"] = f[:-2]
    elif tipo == "repetida":
        f.append({"familia_id": f[0]["familia_id"], "en_promocion": f[0]["en_promocion"] + 2})
    elif tipo == "campo_renombrado":
        q["tienda"] = q.pop("tienda_id")
    elif tipo == "familia_typo":
        f[2]["familia_id"] = f[2]["familia_id"][:-1] + "z"
    elif tipo == "promo_negativa":
        f[3]["en_promocion"] = -1
    elif tipo == "promo_decimal":
        f[3]["en_promocion"] = 2.5
    elif tipo == "tienda_desconocida":
        q["tienda_id"] = 77
    return q, tipo


def defectuar_observacion(o: dict, rng: random.Random) -> tuple[dict, str]:
    q = json.loads(json.dumps(o))
    tipo = rng.choice(["ninguno"] * 7 + ["miles", "coma_decimal", "negativa", "texto", "familia_otra_forma"])
    v = q["ventas"]
    if tipo == "miles" and v[0]["ventas"] >= 1000:
        v[0]["ventas"] = f"{v[0]['ventas']:,}"
    elif tipo == "coma_decimal":
        v[1]["ventas"] = f"{v[1]['ventas']},0"
    elif tipo == "negativa":
        v[2]["ventas"] = -3
    elif tipo == "texto":
        v[3]["ventas"] = str(v[3]["ventas"])
    elif tipo == "familia_otra_forma":
        v[4]["familia_id"] = v[4]["familia_id"].upper()
    return q, tipo


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--datos", default="raw_data")
    ap.add_argument("--dias", type=int, default=28)
    ap.add_argument("--cada", type=int, default=7, help="reentrenar cada N días (0 = nunca)")
    ap.add_argument("--dia-cambio", type=int, default=8)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--salida", default="evidencia/simulacion")
    a = ap.parse_args()
    rng = random.Random(a.seed)
    nrng = np.random.default_rng(a.seed)
    salida = Path(a.salida)
    (salida / "respuestas").mkdir(parents=True, exist_ok=True)

    hist = pd.read_csv(Path(a.datos) / "historico_ventas.csv", parse_dates=["fecha"])
    costos = pd.read_csv(Path(a.datos) / "costos.csv").set_index("familia_id")
    inicio = hist["fecha"].max() + timedelta(days=1)
    fechas = [inicio + timedelta(days=i) for i in range(a.dias)]
    por_llave = hist.set_index(["fecha", "tienda_id", "familia_id"])

    # "verdad" del periodo simulado: mismo día de la semana del año anterior, con ruido y cambio de demanda
    verdad: dict = {}
    promos: dict = {}
    for i, d in enumerate(fechas):
        ref = d - timedelta(days=364)
        filas = hist[hist["fecha"] == ref]
        for r in filas.itertuples():
            factor = CAMBIO.get(r.familia_id, 1.0) if i >= a.dia_cambio else 1.0
            verdad[(d, r.tienda_id, r.familia_id)] = int(nrng.poisson(max(r.ventas * factor, 0.1)))
            promos[(d, r.tienda_id, r.familia_id)] = int(r.en_promocion)
    tiendas = sorted(hist["tienda_id"].unique())
    familias = sorted(hist["familia_id"].unique())

    s = requests.Session()
    registros, eventos, reentrenos = [], [], []
    for i, d in enumerate(fechas):
        ayer = d - timedelta(days=1)
        for t in tiendas:   # 1) observaciones de ayer
            vs = [{"familia_id": f, "ventas": verdad.get((ayer, t, f), por_llave["ventas"].get((ayer, t, f)))}
                  for f in familias]
            vs = [v for v in vs if v["ventas"] is not None and not pd.isna(v["ventas"])]
            vs = [{"familia_id": v["familia_id"], "ventas": int(v["ventas"])} for v in vs]
            if not vs:
                continue
            o, tipo = defectuar_observacion({"tienda_id": int(t), "fecha": ayer.date().isoformat(), "ventas": vs}, rng)
            r = s.post(f"{a.api}/v1/observaciones", json=o, timeout=30)
            eventos.append({"dia": str(d.date()), "endpoint": "observaciones", "defecto": tipo, "status": r.status_code,
                            "estado": r.json().get("calidad", {}).get("estado") if r.ok else None,
                            "registros_guardados": r.json().get("registros_guardados") if r.ok else None})
            if rng.random() < 0.1:   # envío repetido (E09)
                r2 = s.post(f"{a.api}/v1/observaciones", json=o, timeout=30)
                eventos.append({"dia": str(d.date()), "endpoint": "observaciones", "defecto": "envio_repetido",
                                "status": r2.status_code, "registros_guardados": r2.json().get("registros_guardados")})
        for t in tiendas:   # 2) pronóstico de hoy
            p = {"tienda_id": int(t), "fecha": d.date().isoformat(),
                 "familias": [{"familia_id": f, "en_promocion": promos.get((d, t, f), 0)} for f in familias]}
            q, tipo = defectuar_prediccion(p, rng)
            t0 = time.time()
            r = s.post(f"{a.api}/v1/prediccion", json=q, timeout=60)
            ms = (time.time() - t0) * 1000
            b = r.json()
            eventos.append({"dia": str(d.date()), "endpoint": "prediccion", "defecto": tipo, "status": r.status_code,
                            "estado": b.get("calidad", {}).get("estado"), "ms": round(ms),
                            "version_modelo": b.get("version_modelo")})
            if tipo != "ninguno" and not (salida / "respuestas" / f"prediccion_{tipo}.json").exists():
                (salida / "respuestas" / f"prediccion_{tipo}.json").write_text(
                    json.dumps({"solicitud": q, "status": r.status_code, "respuesta": b}, ensure_ascii=False,
                               indent=2), encoding="utf-8")
            if r.status_code == 422:   # la tienda corrige y reenvía la solicitud válida
                r = s.post(f"{a.api}/v1/prediccion", json=p, timeout=60)
                b = r.json()
            for pr in b.get("predicciones", []):
                real = verdad.get((d, t, pr["familia_id"]))
                if real is None:
                    continue
                ingenua = verdad.get((d - timedelta(days=7), t, pr["familia_id"]),
                                     por_llave["ventas"].get((d - timedelta(days=7), t, pr["familia_id"]), real))
                registros.append({"fecha": d, "tienda_id": t, "familia_id": pr["familia_id"], "real": real,
                                  "esperada": pr["venta_esperada"], "sugerida": pr["cantidad_sugerida"],
                                  "ingenua": ingenua, "version": b["version_modelo"]})
        if a.cada and (i + 1) % a.cada == 0 and i + 1 < len(fechas):
            rid = s.post(f"{a.api}/admin/reentrenar", timeout=30).json()["id"]
            t0 = time.time()
            while True:
                st = s.get(f"{a.api}/admin/reentrenar/{rid}", timeout=30).json()
                if st["estado"] != "en_curso" or time.time() - t0 > 600:
                    break
                time.sleep(3)
            st["segundos"] = round(time.time() - t0, 1)
            st["dia"] = str(d.date())
            reentrenos.append(st)
            print(f"{d.date()} reentrenamiento: {st['estado']} promovido={st.get('promovido')} v{st.get('version')} "
                  f"({st['segundos']} s) — {st.get('motivo')}")
            # espera a que la API use la versión vigente (recarga en caliente, máx. 2 min)
            t1 = time.time()
            while st.get("version") and s.get(f"{a.api}/modelo").json()["version"] != st["version"]:
                if time.time() - t1 > 150:
                    break
                time.sleep(2)
            reentrenos[-1]["segundos_hasta_api"] = round(time.time() - t1, 1)
        print(f"{d.date()} listo")

    df = pd.DataFrame(registros)
    cf = df["familia_id"].map(costos["costo_faltante"])
    cs = df["familia_id"].map(costos["costo_sobrante"])

    def costo(x):
        return float((cf * np.maximum(df["real"] - x, 0) + cs * np.maximum(x - df["real"], 0)).sum())

    ev = pd.DataFrame(eventos)
    resumen = {
        "periodo": [str(fechas[0].date()), str(fechas[-1].date())],
        "dias": a.dias, "cambio_de_demanda_desde": str(fechas[min(a.dia_cambio, a.dias - 1)].date()),
        "costo_servicio": round(costo(df["sugerida"])), "costo_venta_esperada_sin_ajuste": round(costo(df["esperada"])),
        "costo_ingenuo_lag7": round(costo(df["ingenua"])),
        "wape": round(float(np.abs(df["esperada"] - df["real"]).sum() / df["real"].sum()), 4),
        "costo_por_semana": {str(k): round(v) for k, v in df.assign(
            c=cf * np.maximum(df["real"] - df["sugerida"], 0) + cs * np.maximum(df["sugerida"] - df["real"], 0))
            .groupby(df["fecha"].dt.isocalendar().week)["c"].sum().items()},
        "solicitudes_por_status": ev.groupby(["endpoint", "status"]).size().rename("n").reset_index()
        .to_dict("records"),
        "calidad_por_defecto": ev[ev["endpoint"] == "prediccion"].groupby(["defecto", "status", "estado"],
                                                                          dropna=False).size().rename("n")
        .reset_index().to_dict("records"),
        "latencia_prediccion_ms_p50_p95": [float(ev["ms"].quantile(0.5)), float(ev["ms"].quantile(0.95))],
        "versiones_usadas": df["version"].value_counts().to_dict(),
        "reentrenamientos": reentrenos,
    }
    (salida / "resumen.json").write_text(json.dumps(resumen, ensure_ascii=False, indent=2, default=str),
                                         encoding="utf-8")
    ev.to_csv(salida / "eventos.csv", index=False)
    print(json.dumps({k: resumen[k] for k in ("costo_servicio", "costo_venta_esperada_sin_ajuste",
                                              "costo_ingenuo_lag7", "wape", "versiones_usadas")}, indent=1))


if __name__ == "__main__":
    main()
