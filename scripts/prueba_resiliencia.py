"""Pruebas de operación contra el producto levantado con Docker Compose (genera evidencia en JSON).

1. MLflow se detiene: /health pasa a ``degradado`` (< 1 min) y /v1/prediccion sigue respondiendo.
2. Reentrenamiento con MLflow caído: termina ``fallido`` y el modelo vigente no cambia.
3. MLflow vuelve: /health regresa a ``ok`` (< 2 min).
4. API reiniciada con MLflow caído: arranca con el modelo de la caché local.
5. ``docker compose down`` (sin -v) + ``up``: conserva datos, modelo y observaciones; no reentrena desde cero.

Uso:  python scripts/prueba_resiliencia.py --salida evidencia/resiliencia.json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

import requests

API = "http://localhost:8000"
PRED = {"tienda_id": 5, "fecha": "2026-03-29",
        "familias": [{"familia_id": "abarrotes", "en_promocion": 0}, {"familia_id": "bebidas", "en_promocion": 8}]}


def compose(*args: str) -> str:
    r = subprocess.run(["docker", "compose", *args], capture_output=True, text=True, encoding="utf-8")
    return (r.stdout + r.stderr).strip()


def esperar(cond, limite: float, paso: float = 2.0) -> float | None:
    t0 = time.time()
    while time.time() - t0 < limite:
        try:
            if cond():
                return round(time.time() - t0, 1)
        except requests.RequestException:
            pass
        time.sleep(paso)
    return None


def health() -> dict:
    return requests.get(f"{API}/health", timeout=10).json()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--salida", default="evidencia/resiliencia.json")
    a = ap.parse_args()
    ev: dict = {}

    ev["inicio"] = {"health": health(), "modelo": requests.get(f"{API}/modelo").json()}
    version0 = ev["inicio"]["modelo"]["version"]

    compose("stop", "mlflow")
    t = esperar(lambda: health()["estado"] == "degradado", 60)
    r = requests.post(f"{API}/v1/prediccion", json=PRED, timeout=30)
    ev["mlflow_detenido"] = {"segundos_hasta_degradado": t, "health": health(), "prediccion_status": r.status_code,
                             "prediccion_version": r.json().get("version_modelo")}

    rid = requests.post(f"{API}/admin/reentrenar", timeout=30).json().get("id")
    esperar(lambda: requests.get(f"{API}/admin/reentrenar/{rid}").json()["estado"] != "en_curso", 300, 5)
    ev["reentrenamiento_sin_mlflow"] = {"id": rid, "estado": requests.get(f"{API}/admin/reentrenar/{rid}").json(),
                                        "modelo_despues": requests.get(f"{API}/modelo").json()}

    compose("restart", "api")
    t = esperar(lambda: requests.get(f"{API}/modelo", timeout=5).status_code == 200, 120)
    ev["api_reiniciada_sin_mlflow"] = {"segundos_hasta_modelo": t, "modelo": requests.get(f"{API}/modelo").json(),
                                       "prediccion_status": requests.post(f"{API}/v1/prediccion", json=PRED,
                                                                          timeout=30).status_code}

    compose("start", "mlflow")
    t = esperar(lambda: health()["estado"] == "ok", 120)
    ev["mlflow_regresa"] = {"segundos_hasta_ok": t, "health": health()}

    obs = {"tienda_id": 5, "fecha": "2026-03-28", "ventas": [{"familia_id": "abarrotes", "ventas": 900}]}
    requests.post(f"{API}/v1/observaciones", json=obs, timeout=30)
    compose("down")
    t0 = time.time()
    compose("up", "-d")
    t = esperar(lambda: health()["estado"] == "ok", 600, 5)
    reenvio = requests.post(f"{API}/v1/observaciones", json=obs, timeout=30).json()
    ev["reinicio_sin_borrar_volumenes"] = {
        "segundos_hasta_ok": t if t is None else round(time.time() - t0, 1),
        "modelo": requests.get(f"{API}/modelo").json(), "version_antes": version0,
        "reenvio_observacion_registros_guardados": reenvio.get("registros_guardados"),
        "log_arranque": [x for x in compose("logs", "arranque").splitlines() if "novamercado.arranque" in x][-4:],
    }
    Path(a.salida).parent.mkdir(parents=True, exist_ok=True)
    Path(a.salida).write_text(json.dumps(ev, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(ev, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
