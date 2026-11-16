"""Exporta evidencia de funcionamiento desde los servicios levantados: experimentos/versiones de MLflow,
ejecuciones del DAG en Airflow y conteos de la base de datos del producto.

Uso:  python scripts/exportar_evidencia.py --salida evidencia   (lee usuario/clave de Airflow de .env)
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import requests


def leer_env(ruta: str = ".env") -> dict:
    env = {}
    for linea in Path(ruta).read_text(encoding="utf-8").splitlines():
        if "=" in linea and not linea.startswith("#"):
            k, v = linea.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--salida", default="evidencia")
    a = ap.parse_args()
    env = leer_env()
    out = Path(a.salida)
    out.mkdir(parents=True, exist_ok=True)
    mlf = f"http://localhost:{env.get('MLFLOW_PORT', '5000')}/api/2.0/mlflow"
    af = f"http://localhost:{env.get('AIRFLOW_PORT', '8080')}"

    exp = requests.get(f"{mlf}/experiments/get-by-name", params={"experiment_name": "novamercado-pronostico"}).json()
    runs = requests.post(f"{mlf}/runs/search", json={"experiment_ids": [exp["experiment"]["experiment_id"]],
                                                     "max_results": 200,
                                                     "order_by": ["attributes.start_time ASC"]}).json()
    filas = []
    for r in runs.get("runs", []):
        info, data = r["info"], r["data"]
        tags = {t["key"]: t["value"] for t in data.get("tags", [])}
        filas.append({"run_id": info["run_id"], "nombre": info.get("run_name"), "estado": info["status"],
                      "padre": tags.get("mlflow.parentRunId"), "decision": tags.get("decision"),
                      "version_registrada": tags.get("version_registrada"), "origen": tags.get("origen"),
                      "fin_datos": tags.get("fin_datos"),
                      "metricas": {m["key"]: m["value"] for m in data.get("metrics", [])}})
    versiones = requests.get(f"{mlf}/model-versions/search",
                             params={"filter": "name='novamercado-ventas'"}).json().get("model_versions", [])
    modelo = requests.get(f"{mlf}/registered-models/get", params={"name": "novamercado-ventas"}).json()
    (out / "mlflow.json").write_text(json.dumps({"experimento": exp, "runs": filas, "versiones": versiones,
                                                 "modelo_registrado": modelo}, indent=2, ensure_ascii=False),
                                     encoding="utf-8")

    tok = requests.post(f"{af}/auth/token", json={"username": env["AIRFLOW_ADMIN_USER"],
                                                  "password": env["AIRFLOW_ADMIN_PASSWORD"]}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    corridas = requests.get(f"{af}/api/v2/dags/novamercado_reentrenamiento/dagRuns", headers=h,
                            params={"limit": 100, "order_by": "start_date"}).json()
    detalle = []
    for c in corridas.get("dag_runs", []):
        tis = requests.get(f"{af}/api/v2/dags/novamercado_reentrenamiento/dagRuns/{c['dag_run_id']}/taskInstances",
                           headers=h).json()
        detalle.append({"dag_run_id": c["dag_run_id"], "estado": c["state"], "inicio": c.get("start_date"),
                        "fin": c.get("end_date"),
                        "tareas": {t["task_id"]: t["state"] for t in tis.get("task_instances", [])}})
    (out / "airflow_dag_runs.json").write_text(json.dumps(detalle, indent=2, ensure_ascii=False), encoding="utf-8")

    sql = ("select 'solicitudes' t, count(*) from solicitudes union all select 'predicciones', count(*) from "
           "predicciones union all select 'correcciones', count(*) from correcciones union all select "
           "'observaciones', count(*) from observaciones union all select 'reentrenamientos', count(*) from "
           "reentrenamientos union all select 'historico_ventas', count(*) from historico_ventas;")
    conteos = subprocess.run(["docker", "compose", "exec", "-T", "db", "psql", "-U", env["POSTGRES_USER"], "-d",
                              env["POSTGRES_DB"], "-c", sql], capture_output=True, text=True).stdout
    reglas = subprocess.run(["docker", "compose", "exec", "-T", "db", "psql", "-U", env["POSTGRES_USER"], "-d",
                             env["POSTGRES_DB"], "-c",
                             "select regla, accion, count(*) from correcciones group by 1,2 order by 3 desc;"],
                            capture_output=True, text=True).stdout
    (out / "base_de_datos.txt").write_text(conteos + "\n" + reglas, encoding="utf-8")
    print(f"{len(filas)} runs, {len(versiones)} versiones, {len(detalle)} ejecuciones del DAG")


if __name__ == "__main__":
    main()
