# Servidor de MLflow (tracking + model registry + proxy de artefactos).
FROM python:3.11.10-slim-bookworm

COPY requirements/mlflow.txt /tmp/mlflow.txt
RUN pip install --no-cache-dir -r /tmp/mlflow.txt
RUN useradd --create-home --uid 1000 mlflow && mkdir -p /mlflow && chown mlflow /mlflow
USER mlflow
WORKDIR /mlflow
EXPOSE 5000
CMD ["mlflow", "server", \
     "--backend-store-uri", "sqlite:////mlflow/mlflow.db", \
     "--artifacts-destination", "/mlflow/artifacts", \
     "--serve-artifacts", \
     "--host", "0.0.0.0", "--port", "5000", "--workers", "2"]
