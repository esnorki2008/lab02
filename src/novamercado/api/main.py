"""Punto de entrada de producción: ``uvicorn novamercado.api.main:app --port 8000``."""
from __future__ import annotations

import logging
import os

from novamercado.airflow_cliente import ClienteAirflow
from novamercado.api.app import Servicios, crear_app
from novamercado.config import load_settings
from novamercado.db import Repositorio
from novamercado.gestor_modelo import GestorModelo
from novamercado.registro import registro_desde_settings
from novamercado.salud import MonitorSalud
from novamercado.servicio import ServicioPronostico

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")

# El cliente de MLflow no debe quedarse reintentando: si no responde, se sigue con el modelo cargado.
os.environ.setdefault("MLFLOW_HTTP_REQUEST_MAX_RETRIES", "0")
os.environ.setdefault("MLFLOW_HTTP_REQUEST_TIMEOUT", "10")


def construir() -> tuple[Servicios, GestorModelo, float]:
    settings = load_settings()
    p = settings.params["servicio"]
    timeout = float(p["timeout_dependencias_s"])
    repo = Repositorio.desde_url(settings.database_url)
    registro = registro_desde_settings(settings)
    gestor = GestorModelo(registro, nombre=settings.model_name, alias=settings.model_alias,
                          cache_dir=settings.cache_dir / "modelo")
    airflow = ClienteAirflow(settings.airflow_url, settings.airflow_user, settings.airflow_password,
                             p["dag_reentrenamiento"], timeout=timeout)
    salud = MonitorSalud(
        {"base_de_datos": repo.ping, "mlflow": lambda: registro.ping(timeout), "airflow": airflow.ping},
        modelo_cargado=lambda: gestor.cargado,
        info_modelo=lambda: ({"version_modelo": gestor.actual().version} if gestor.cargado else {}),
        cache_s=float(p["cache_health_s"]),
    )
    servicio = ServicioPronostico(settings, repo, gestor)
    return Servicios(servicio=servicio, gestor=gestor, repo=repo, airflow=airflow, salud=salud,
                     registro=registro), gestor, float(p["intervalo_recarga_modelo_s"])


_servicios, _gestor, _intervalo = construir()


def _al_iniciar() -> None:
    if _gestor.cargar_cache():
        logging.getLogger(__name__).info("Modelo inicial tomado de la caché local")
    _gestor.iniciar(_intervalo)


app = crear_app(_servicios, on_startup=_al_iniciar, on_shutdown=_gestor.detener)
