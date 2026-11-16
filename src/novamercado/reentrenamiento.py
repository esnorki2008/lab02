"""Funciones que llama el DAG de reentrenamiento (una por tarea). Toda la lógica vive aquí, no en el DAG."""
from __future__ import annotations

import logging
import os

import requests

from novamercado.config import load_settings
from novamercado.db import Repositorio
from novamercado.entrenamiento import ciclo
from novamercado.registro import registro_desde_settings

log = logging.getLogger(__name__)


def _repo() -> Repositorio:
    return Repositorio.desde_url(load_settings().database_url)


def verificar(run_id: str) -> dict:
    """Registra la ejecución como en curso y verifica que la base de datos y MLflow respondan."""
    settings = load_settings()
    repo = _repo()
    repo.asegurar_reentrenamiento(run_id)
    repo.ping()
    registro = registro_desde_settings(settings)
    registro.ping(timeout=10)
    return {"run_id": run_id, "version_vigente": registro.version_vigente()}


def entrenar(run_id: str) -> dict:
    """Ciclo completo: entrena candidatos con histórico + observaciones, compara y promueve si corresponde."""
    settings = load_settings()
    resumen = ciclo(settings, _repo(), registro_desde_settings(settings), origen="airflow", ejecucion=run_id)
    log.info("Resultado del ciclo: %s", resumen.get("motivo"))
    return {k: resumen.get(k) for k in ("promovido", "version", "motivo", "candidato", "metricas_candidato",
                                         "metricas_vigente", "fin_datos", "evaluacion_inicio", "dias_evaluacion",
                                         "segundos")}


def publicar(run_id: str, resultado: dict) -> dict:
    """Guarda el resultado para ``GET /admin/reentrenar/{id}`` y avisa a la API para que recargue ya."""
    _repo().terminar_reentrenamiento(run_id, "exitoso", bool(resultado.get("promovido")), resultado.get("version"),
                                     resultado.get("motivo") or "", resultado)
    api = os.environ.get("NOVAMERCADO_API_URL")
    if api and resultado.get("promovido"):
        try:
            requests.post(f"{api.rstrip('/')}/admin/recargar", timeout=30)
        except requests.RequestException as e:   # la API también recarga sola por sondeo
            log.warning("No se pudo avisar a la API: %s", e)
    return resultado


def marcar_fallido(run_id: str, motivo: str) -> None:
    """Callback de falla: deja constancia; el alias del modelo vigente no se tocó."""
    version = None
    try:
        version = registro_desde_settings(load_settings()).version_vigente()
    except Exception:
        pass
    try:
        _repo().terminar_reentrenamiento(run_id, "fallido", False, version,
                                         f"{motivo}; el modelo vigente no cambió")
    except Exception:
        log.exception("No se pudo registrar la falla del reentrenamiento %s", run_id)


if __name__ == "__main__":  # usado por el callback de falla del DAG
    import sys

    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) >= 4 and sys.argv[1] == "fallido":
        marcar_fallido(sys.argv[2], sys.argv[3])
    else:
        sys.exit("uso: python -m novamercado.reentrenamiento fallido <run_id> <motivo>")
