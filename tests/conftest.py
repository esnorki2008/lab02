"""Fixtures: datos sintéticos en SQLite, registro de modelos en memoria y la API con dependencias falsas.

Ninguna prueba necesita PostgreSQL, MLflow ni Airflow en ejecución.
"""
from __future__ import annotations

import copy
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
os.environ.setdefault("NOVAMERCADO_ROOT", str(Path(__file__).resolve().parents[1]))

from novamercado.config import Settings, load_params  # noqa: E402
from novamercado.db import Repositorio  # noqa: E402
from sinteticos import generar  # noqa: E402


@pytest.fixture(scope="session")
def settings(tmp_path_factory) -> Settings:
    carpeta = generar(tmp_path_factory.mktemp("datos") / "raw_data")
    params = copy.deepcopy(load_params())
    params["datos"]["carpeta"] = str(carpeta)
    params["modelo"]["lgbm"]["n_estimators"] = 80
    params["modelo"]["lgbm"]["num_threads"] = 2
    return Settings(params=params, root=Path(__file__).resolve().parents[1],
                    cache_dir=tmp_path_factory.mktemp("cache"))


def nuevo_repo(settings: Settings, ruta: Path) -> Repositorio:
    from novamercado import data

    repo = Repositorio.desde_url(f"sqlite:///{ruta.as_posix()}")
    repo.crear_esquema()
    repo.cargar_publicados(data.leer_publicados(settings))
    return repo


@pytest.fixture(scope="session")
def repo_base(settings, tmp_path_factory) -> Repositorio:
    return nuevo_repo(settings, tmp_path_factory.mktemp("db") / "base.db")


@pytest.fixture(scope="session")
def modelo_entrenado(settings, repo_base):
    """Primer ciclo de entrenamiento (sobre un repositorio que no se modifica)."""
    from novamercado.entrenamiento import ciclo
    from novamercado.registro import RegistroMemoria

    reg = RegistroMemoria()
    resumen = ciclo(settings, repo_base, reg, origen="pruebas")
    assert resumen["promovido"] and resumen["version"] == "1"
    return reg.versiones["1"], resumen


class AirflowFalso:
    def __init__(self):
        self.disparos: list[str] = []
        self.estados: dict[str, str] = {}
        self.caido = False

    def ping(self):
        if self.caido:
            raise ConnectionError("airflow caído")

    def disparar(self, run_id, conf=None):
        from novamercado.airflow_cliente import AirflowNoDisponible

        if self.caido:
            raise AirflowNoDisponible("Airflow no responde")
        self.disparos.append(run_id)
        self.estados[run_id] = "queued"

    def estado(self, run_id):
        return self.estados.get(run_id)


@pytest.fixture()
def entorno(settings, modelo_entrenado, tmp_path):
    """API completa sobre una base SQLite nueva por prueba."""
    from fastapi.testclient import TestClient

    from novamercado.api.app import Servicios, crear_app
    from novamercado.gestor_modelo import GestorModelo
    from novamercado.registro import RegistroMemoria
    from novamercado.salud import MonitorSalud
    from novamercado.servicio import ServicioPronostico

    repo = nuevo_repo(settings, tmp_path / "api.db")
    reg = RegistroMemoria()
    reg.versiones["1"] = modelo_entrenado[0]
    reg.vigente_version = "1"
    gestor = GestorModelo(reg, nombre=settings.model_name, alias=settings.model_alias, cache_dir=tmp_path / "cache")
    gestor.refrescar()
    airflow = AirflowFalso()
    salud = MonitorSalud({"base_de_datos": repo.ping, "mlflow": reg.ping, "airflow": airflow.ping},
                         modelo_cargado=lambda: gestor.cargado,
                         info_modelo=lambda: {"version_modelo": gestor.actual().version} if gestor.cargado else {},
                         cache_s=0)
    servicios = Servicios(servicio=ServicioPronostico(settings, repo, gestor), gestor=gestor, repo=repo,
                          airflow=airflow, salud=salud, registro=reg)
    cliente = TestClient(crear_app(servicios))
    return {"cliente": cliente, "repo": repo, "registro": reg, "gestor": gestor, "airflow": airflow,
            "servicios": servicios}
