"""Arranque del producto (contenedor ``arranque``): ``python -m novamercado.arranque``.

1. Espera a la base de datos y crea el esquema (idempotente).
2. Carga los archivos publicados si no están cargados o cambiaron (se compara su sha256).
3. Espera a MLflow; si no existe modelo vigente, entrena y registra el primero.

Al volver a levantar el producto no repite trabajo: los datos ya están (mismo hash) y el alias ya existe.
"""
from __future__ import annotations

import logging
import sys
import time

from novamercado import data
from novamercado.config import load_settings
from novamercado.db import Repositorio
from novamercado.entrenamiento import ciclo
from novamercado.registro import registro_desde_settings

log = logging.getLogger("novamercado.arranque")


def _esperar(nombre: str, fn, intentos: int = 90, pausa: float = 2.0):
    for i in range(intentos):
        try:
            return fn()
        except Exception as e:
            if i == intentos - 1:
                raise
            log.info("Esperando %s (%s)...", nombre, type(e).__name__)
            time.sleep(pausa)


def cargar_datos(settings, repo: Repositorio) -> str:
    try:
        rutas = data.localizar(settings)
    except data.DatosNoEncontrados as e:
        if repo.rango_historico()[1] is not None:
            log.warning("%s; se usan los datos ya cargados en la base de datos", e)
            return "sin_cambios"
        raise
    hashes = {k: data.sha256(p) for k, p in rutas.items()}
    if hashes == repo.hashes_cargados():
        log.info("Datos publicados ya cargados (mismo sha256); no se recargan")
        return "sin_cambios"
    datos = data.leer_publicados(settings)
    filas = repo.cargar_publicados(datos)
    log.info("Datos publicados cargados: %s", filas)
    return "cargados"


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = load_settings()
    repo = Repositorio.desde_url(settings.database_url)
    _esperar("base de datos", repo.ping)
    repo.crear_esquema()
    cargar_datos(settings, repo)

    registro = registro_desde_settings(settings)
    _esperar("MLflow", lambda: registro.ping(timeout=5), intentos=150)
    version = _esperar("registro de modelos", registro.version_vigente, intentos=10)
    if version:
        log.info("Ya existe modelo vigente (versión %s); no se entrena", version)
        return 0
    log.info("No hay modelo vigente: entrenando el primero")
    resumen = ciclo(settings, repo, registro, origen="arranque")
    log.info("Resultado: %s", resumen.get("motivo"))
    return 0 if resumen.get("version") else 1


if __name__ == "__main__":
    sys.exit(main())
