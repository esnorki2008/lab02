"""Configuración: parámetros de negocio (config/params.yaml) + conexión a servicios (variables de entorno).

Los parámetros versionados viven en YAML; las credenciales y URLs sólo en el entorno (.env), nunca en Git.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


def project_root() -> Path:
    """Raíz del proyecto: ``NOVAMERCADO_ROOT`` o dos niveles arriba de ``src/novamercado``."""
    env = os.environ.get("NOVAMERCADO_ROOT")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    params: dict[str, Any]
    root: Path
    database_url: str = ""
    mlflow_uri: str = ""
    airflow_url: str = ""
    airflow_user: str = ""
    airflow_password: str = ""
    cache_dir: Path = field(default_factory=lambda: Path("cache"))

    # Accesos cómodos ---------------------------------------------------------
    def section(self, name: str) -> dict[str, Any]:
        return self.params.get(name, {})

    @property
    def data_dir(self) -> Path:
        d = Path(os.environ.get("NOVAMERCADO_DATA_DIR", self.params["datos"]["carpeta"]))
        return d if d.is_absolute() else self.root / d

    @property
    def model_name(self) -> str:
        return self.params["modelo"]["nombre_registrado"]

    @property
    def model_alias(self) -> str:
        return self.params["modelo"]["alias"]

    @property
    def experiment(self) -> str:
        return self.params["modelo"]["experimento"]


def load_params(path: Path | None = None) -> dict[str, Any]:
    path = path or Path(os.environ.get("NOVAMERCADO_PARAMS", project_root() / "config" / "params.yaml"))
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@lru_cache(maxsize=1)
def load_settings() -> Settings:
    root = project_root()
    return Settings(
        params=load_params(),
        root=root,
        database_url=os.environ.get("DATABASE_URL", ""),
        mlflow_uri=os.environ.get("MLFLOW_TRACKING_URI", ""),
        airflow_url=os.environ.get("AIRFLOW_URL", "").rstrip("/"),
        airflow_user=os.environ.get("AIRFLOW_ADMIN_USER", ""),
        airflow_password=os.environ.get("AIRFLOW_ADMIN_PASSWORD", ""),
        cache_dir=Path(os.environ.get("NOVAMERCADO_CACHE_DIR", root / "cache")),
    )
