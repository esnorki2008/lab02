# Airflow 3 + entorno virtual aislado con las dependencias del producto (novamercado).
FROM apache/airflow:3.1.3-python3.11

USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 \
 && rm -rf /var/lib/apt/lists/*

# Las tareas del DAG se ejecutan con este intérprete (@task.external_python)
COPY requirements/ /tmp/requirements/
RUN python -m venv /opt/novamercado-venv \
 && /opt/novamercado-venv/bin/pip install --no-cache-dir -r /tmp/requirements/airflow.txt

COPY config/ /opt/novamercado/config/
COPY src/ /opt/novamercado/src/
COPY --chown=airflow:root dags/ /opt/airflow/dags/
ENV PYTHONPATH=/opt/novamercado/src \
    NOVAMERCADO_ROOT=/opt/novamercado \
    NOVAMERCADO_PYTHON=/opt/novamercado-venv/bin/python \
    GIT_PYTHON_REFRESH=quiet
USER airflow
