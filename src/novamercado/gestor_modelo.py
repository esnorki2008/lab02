"""Modelo vigente en memoria del servicio, con recarga en caliente y caché local.

* Un hilo consulta cada ``intervalo`` segundos qué versión tiene el alias en el registro; si cambió, la
  descarga y la intercambia de forma atómica (las solicitudes en curso terminan con la anterior).
* Cada versión cargada se guarda en ``cache_dir``: si al arrancar MLflow no está disponible, el servicio
  responde con la última versión conocida.
* Si el registro no responde se sigue usando el modelo cargado (nunca se descarga un modelo por un fallo).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import joblib

from novamercado.modelos import ModeloVentas

log = logging.getLogger(__name__)


class SinModelo(RuntimeError):
    """Todavía no hay un modelo cargado."""


@dataclass(frozen=True)
class ModeloCargado:
    modelo: ModeloVentas
    version: str
    nombre: str
    alias: str
    origen: str          # "registro" | "cache"


class GestorModelo:
    def __init__(self, registro, *, nombre: str, alias: str, cache_dir: Path | None = None):
        self.registro = registro
        self.nombre, self.alias = nombre, alias
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self._actual: ModeloCargado | None = None
        self._lock = threading.Lock()
        self._hilo: threading.Thread | None = None
        self._detener = threading.Event()
        self.ultimo_error: str | None = None
        self.ultima_consulta_ok: float | None = None

    # ------------------------------------------------------------------ lectura
    def actual(self) -> ModeloCargado:
        m = self._actual
        if m is None:
            raise SinModelo("No hay un modelo cargado todavía")
        return m

    @property
    def cargado(self) -> bool:
        return self._actual is not None

    # ------------------------------------------------------------------ carga
    def establecer(self, modelo: ModeloVentas, version: str, origen: str = "registro") -> None:
        with self._lock:
            self._actual = ModeloCargado(modelo, str(version), self.nombre, self.alias, origen)
        log.info("Modelo %s versión %s activo (origen: %s)", self.nombre, version, origen)

    def refrescar(self) -> str | None:
        """Sincroniza con el alias del registro. Devuelve la versión activa."""
        try:
            version = self.registro.version_vigente()
            self.ultima_consulta_ok = time.time()
            self.ultimo_error = None
        except Exception as e:  # registro caído: se conserva el modelo actual
            self.ultimo_error = f"{type(e).__name__}: {e}"[:300]
            log.warning("No se pudo consultar el registro de modelos: %s", self.ultimo_error)
            return self._actual.version if self._actual else None
        if version is None:
            return self._actual.version if self._actual else None
        if self._actual is not None and self._actual.version == version and self._actual.origen == "registro":
            return version
        if self._actual is not None and self._actual.version == version:
            # venía de la caché y es la misma versión: basta con marcarla como confirmada
            self.establecer(self._actual.modelo, version, "registro")
            return version
        try:
            modelo = self.registro.cargar(version)
        except Exception as e:
            self.ultimo_error = f"No se pudo descargar la versión {version}: {e}"[:300]
            log.warning(self.ultimo_error)
            return self._actual.version if self._actual else None
        self.establecer(modelo, version, "registro")
        self._guardar_cache(modelo, version)
        return version

    # ------------------------------------------------------------------ caché local
    def _guardar_cache(self, modelo: ModeloVentas, version: str) -> None:
        if not self.cache_dir:
            return
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            tmp = self.cache_dir / f".modelo.{os.getpid()}.tmp"
            joblib.dump(modelo, tmp)
            os.replace(tmp, self.cache_dir / "modelo.joblib")
            (self.cache_dir / "version.json").write_text(json.dumps({"version": version, "nombre": self.nombre}),
                                                         encoding="utf-8")
        except Exception as e:
            log.warning("No se pudo guardar la caché del modelo: %s", e)

    def cargar_cache(self) -> bool:
        if not self.cache_dir:
            return False
        try:
            meta = json.loads((self.cache_dir / "version.json").read_text(encoding="utf-8"))
            modelo = joblib.load(self.cache_dir / "modelo.joblib")
        except Exception:
            return False
        self.establecer(modelo, meta["version"], "cache")
        return True

    # ------------------------------------------------------------------ hilo de recarga
    def iniciar(self, intervalo: float) -> None:
        if self._hilo and self._hilo.is_alive():
            return

        def bucle():
            while not self._detener.is_set():
                try:
                    self.refrescar()
                except Exception:  # pragma: no cover - el hilo nunca debe morir
                    log.exception("Error inesperado al refrescar el modelo")
                self._detener.wait(intervalo)

        self._hilo = threading.Thread(target=bucle, name="recarga-modelo", daemon=True)
        self._hilo.start()

    def detener(self) -> None:
        self._detener.set()
