"""Registro de modelos: experimentos, versiones y alias del modelo vigente.

* ``RegistroMLflow``: MLflow Tracking + Model Registry (producción). El vigente es la versión con el alias
  configurado (``champion``) del modelo registrado ``novamercado-ventas``.
* ``RegistroMemoria``: misma interfaz en memoria, para pruebas sin servidor de MLflow.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

import joblib

from novamercado.modelos import ModeloVentas

log = logging.getLogger(__name__)
ARCHIVO_MODELO = "modelo.joblib"


def _plano(d: dict, prefijo: str = "") -> dict:
    out = {}
    for k, v in (d or {}).items():
        if isinstance(v, dict):
            out.update(_plano(v, f"{prefijo}{k}."))
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            out[f"{prefijo}{k}"] = float(v)
    return out


class RegistroMemoria:
    def __init__(self, nombre: str = "novamercado-ventas", alias: str = "champion"):
        self.nombre, self.alias = nombre, alias
        self.versiones: dict[str, ModeloVentas] = {}
        self.vigente_version: str | None = None
        self.ciclos: list[dict] = []
        self.disponible = True

    def ping(self) -> None:
        if not self.disponible:
            raise ConnectionError("registro no disponible")

    def version_vigente(self) -> str | None:
        self.ping()
        return self.vigente_version

    def cargar(self, version: str) -> ModeloVentas:
        self.ping()
        return self.versiones[version]

    def vigente(self) -> tuple[str, ModeloVentas] | None:
        v = self.version_vigente()
        return (v, self.cargar(v)) if v else None

    def publicar_ciclo(self, resumen: dict, candidatos: list[dict], nuevo: ModeloVentas | None,
                       promover: bool) -> str | None:
        self.ping()
        self.ciclos.append(resumen)
        if nuevo is None or not promover:
            return None
        v = str(len(self.versiones) + 1)
        self.versiones[v] = nuevo
        self.vigente_version = v
        return v


class RegistroMLflow:
    def __init__(self, uri: str, nombre: str, alias: str, experimento: str):
        from mlflow.tracking import MlflowClient

        self.uri, self.nombre, self.alias, self.experimento = uri.rstrip("/"), nombre, alias, experimento
        self.client = MlflowClient(tracking_uri=self.uri, registry_uri=self.uri)

    def ping(self, timeout: float = 2.0) -> None:
        import requests

        r = requests.get(f"{self.uri}/health", timeout=timeout)
        r.raise_for_status()

    def version_vigente(self) -> str | None:
        from mlflow.exceptions import MlflowException

        try:
            return str(self.client.get_model_version_by_alias(self.nombre, self.alias).version)
        except MlflowException as e:
            if "RESOURCE_DOES_NOT_EXIST" in str(e.error_code) or "not found" in str(e).lower():
                return None
            raise

    def cargar(self, version: str) -> ModeloVentas:
        import mlflow

        mv = self.client.get_model_version(self.nombre, version)
        with tempfile.TemporaryDirectory() as tmp:
            ruta = mlflow.artifacts.download_artifacts(run_id=mv.run_id, artifact_path="modelo", dst_path=tmp,
                                                       tracking_uri=self.uri)
            return joblib.load(Path(ruta) / ARCHIVO_MODELO)

    def vigente(self) -> tuple[str, ModeloVentas] | None:
        v = self.version_vigente()
        return (v, self.cargar(v)) if v else None

    def publicar_ciclo(self, resumen: dict, candidatos: list[dict], nuevo: ModeloVentas | None,
                       promover: bool) -> str | None:
        """Registra el ciclo como run de MLflow (candidatos como runs anidados). Si ``promover`` registra
        la versión y mueve el alias AL FINAL: cualquier error antes deja intacto al vigente."""
        import mlflow

        mlflow.set_tracking_uri(self.uri)
        mlflow.set_registry_uri(self.uri)
        mlflow.set_experiment(self.experimento)
        version = None
        nombre_run = f"{resumen.get('origen')}-{resumen.get('fin_datos')}"
        tags = {"origen": str(resumen.get("origen")), "ejecucion": str(resumen.get("ejecucion")),
                "fin_datos": str(resumen.get("fin_datos")),
                "version_vigente_previa": str(resumen.get("version_vigente_previa"))}
        with mlflow.start_run(run_name=nombre_run, tags=tags) as run:
            mlflow.log_params({k: resumen[k] for k in ("fin_datos", "evaluacion_inicio", "dias_evaluacion",
                                                       "calibracion_inicio", "n_observaciones_operacion")
                               if k in resumen})
            if resumen.get("metricas_vigente"):
                mlflow.log_metrics(_plano(resumen["metricas_vigente"], "vigente."))
            if resumen.get("metricas_candidato"):
                mlflow.log_metrics(_plano(resumen["metricas_candidato"], "candidato."))
            for c in candidatos:
                with mlflow.start_run(run_name=f"candidato-{c['tipo']}", nested=True):
                    mlflow.log_params({"tipo": c["tipo"], "n_entrenamiento": c.get("n_entrenamiento")})
                    mlflow.log_metrics(_plano(c["metricas"], "eval."))
                    mlflow.log_dict(c["multiplicadores"], "multiplicadores.json")
            mlflow.log_dict(json.loads(json.dumps(resumen, default=str)), "resumen_ciclo.json")
            if nuevo is not None:
                with tempfile.TemporaryDirectory() as tmp:
                    d = Path(tmp) / "modelo"
                    d.mkdir()
                    joblib.dump(nuevo, d / ARCHIVO_MODELO)
                    (d / "meta.json").write_text(json.dumps(
                        {"tipo": nuevo.tipo, "columnas": nuevo.columnas, "familias": nuevo.familias,
                         "multiplicadores": nuevo.multiplicadores, **nuevo.meta}, default=str, indent=2),
                        encoding="utf-8")
                    if nuevo.tipo == "lgbm":
                        gain = nuevo.estimador.feature_importance("gain").tolist()
                        imp = sorted(zip(nuevo.columnas, gain, strict=True), key=lambda kv: -kv[1])
                        (d / "importancia_features.json").write_text(json.dumps(imp, indent=1), encoding="utf-8")
                    mlflow.log_artifacts(str(d), artifact_path="modelo")
                mlflow.log_params({"tipo_modelo": nuevo.tipo})
                if promover:
                    mv = mlflow.register_model(f"runs:/{run.info.run_id}/modelo", self.nombre,
                                               tags={"origen": tags["origen"], "fin_entrenamiento":
                                                     str(nuevo.meta.get("fin_entrenamiento"))})
                    version = str(mv.version)
                    costo = (resumen.get("metricas_candidato") or {}).get("costo")
                    self.client.update_model_version(
                        self.nombre, version,
                        description=f"{nuevo.tipo}; datos hasta {nuevo.meta.get('fin_entrenamiento')}; "
                                    f"costo en evaluación {costo}")
                    self.client.set_registered_model_alias(self.nombre, self.alias, version)
            mlflow.set_tag("decision", "promovido" if version else "no_promovido")
            if version:
                mlflow.set_tag("version_registrada", version)
        return version


def registro_desde_settings(settings) -> RegistroMLflow:
    os.environ.setdefault("MLFLOW_HTTP_REQUEST_MAX_RETRIES", "2")
    os.environ.setdefault("MLFLOW_HTTP_REQUEST_TIMEOUT", "20")
    return RegistroMLflow(settings.mlflow_uri, settings.model_name, settings.model_alias, settings.experiment)
