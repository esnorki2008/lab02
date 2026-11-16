"""Estado del servicio y de sus dependencias para ``GET /health``."""
from __future__ import annotations

import threading
import time
from collections.abc import Callable


class MonitorSalud:
    """Ejecuta los chequeos de cada dependencia (con timeout corto) y reutiliza el resultado unos segundos."""

    def __init__(self, chequeos: dict[str, Callable[[], None]], modelo_cargado: Callable[[], bool],
                 info_modelo: Callable[[], dict], cache_s: float = 5.0):
        self.chequeos = chequeos
        self.modelo_cargado = modelo_cargado
        self.info_modelo = info_modelo
        self.cache_s = cache_s
        self._ultimo: tuple[float, int, dict] | None = None
        self._lock = threading.Lock()

    def evaluar(self) -> tuple[int, dict]:
        with self._lock:
            if self._ultimo and time.time() - self._ultimo[0] < self.cache_s:
                return self._ultimo[1], self._ultimo[2]
        componentes: dict[str, str] = {}
        fallas: list[str] = []
        for nombre, chequeo in self.chequeos.items():
            try:
                chequeo()
                componentes[nombre] = "ok"
            except Exception as e:  # cualquier error = dependencia no disponible
                componentes[nombre] = "no_disponible"
                fallas.append(f"{nombre} no disponible ({type(e).__name__})")
        cargado = self.modelo_cargado()
        componentes["modelo"] = "ok" if cargado else "sin_modelo"
        cuerpo: dict = {"componentes": componentes, **self.info_modelo()}
        if not cargado:
            codigo = 503
            cuerpo.update(estado="degradado", motivo="; ".join(["no hay un modelo cargado todavía"] + fallas))
        elif fallas:
            codigo = 200
            cuerpo.update(estado="degradado", motivo="; ".join(fallas))
        else:
            codigo = 200
            cuerpo.update(estado="ok")
        cuerpo = {"estado": cuerpo.pop("estado"), **({"motivo": cuerpo.pop("motivo")} if "motivo" in cuerpo else {}),
                  **cuerpo}
        with self._lock:
            self._ultimo = (time.time(), codigo, cuerpo)
        return codigo, cuerpo
