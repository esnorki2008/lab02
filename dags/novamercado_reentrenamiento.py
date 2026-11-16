"""Reentrenamiento del modelo de pronóstico de NovaMercado.

verificar -> entrenar_evaluar_promover -> publicar_resultado

* Se dispara desde ``POST /admin/reentrenar`` (API REST de Airflow); el ``run_id`` es el ``id`` que devuelve la API.
* Usa el histórico y las observaciones registradas en la base de datos del producto.
* El alias del modelo vigente sólo se mueve al final del ciclo y si el criterio de reemplazo se cumple:
  un fallo en cualquier punto deja intacto al vigente (y queda registrado como ``fallido``).
* Las tareas corren en el entorno virtual del producto (``NOVAMERCADO_PYTHON``), aislado de las
  dependencias de Airflow.

Este archivo sólo orquesta: cada tarea importa y llama una función de ``novamercado.reentrenamiento``.
"""
from __future__ import annotations

import os
import subprocess
from datetime import datetime, timedelta

from airflow.sdk import dag, task

PYTHON_PRODUCTO = os.environ.get("NOVAMERCADO_PYTHON", "/opt/novamercado-venv/bin/python")
RUN_ID = "{{ dag_run.run_id }}"


def _fallo(context) -> None:
    """Callback de falla: registra la ejecución como fallida (en el entorno del producto)."""
    dag_run = context.get("dag_run")
    run_id = dag_run.run_id if dag_run is not None else str(context.get("run_id"))
    ti = context.get("task_instance")
    motivo = f"Falló la tarea '{getattr(ti, 'task_id', '?')}': {context.get('exception')}"[:500]
    subprocess.run([PYTHON_PRODUCTO, "-m", "novamercado.reentrenamiento", "fallido", run_id, motivo],
                   check=False, timeout=60)


@dag(
    dag_id="novamercado_reentrenamiento",
    schedule=None,
    start_date=datetime(2026, 1, 1),
    catchup=False,
    max_active_runs=1,
    is_paused_upon_creation=False,
    dagrun_timeout=timedelta(minutes=10),
    default_args={"owner": "novamercado", "retries": 0, "on_failure_callback": _fallo},
    tags=["novamercado", "mlflow"],
    doc_md=__doc__,
)
def novamercado_reentrenamiento():
    @task.external_python(python=PYTHON_PRODUCTO, expect_airflow=False, execution_timeout=timedelta(minutes=2))
    def verificar(ejecucion: str) -> dict:
        from novamercado.reentrenamiento import verificar as fn

        return fn(ejecucion)

    @task.external_python(python=PYTHON_PRODUCTO, expect_airflow=False, execution_timeout=timedelta(minutes=8))
    def entrenar_evaluar_promover(ejecucion: str, verificacion: dict) -> dict:
        from novamercado.reentrenamiento import entrenar

        return entrenar(ejecucion)

    @task.external_python(python=PYTHON_PRODUCTO, expect_airflow=False, execution_timeout=timedelta(minutes=2))
    def publicar_resultado(ejecucion: str, resultado: dict) -> dict:
        from novamercado.reentrenamiento import publicar

        return publicar(ejecucion, resultado)

    v = verificar(RUN_ID)
    r = entrenar_evaluar_promover(RUN_ID, v)
    publicar_resultado(RUN_ID, r)


novamercado_reentrenamiento()
