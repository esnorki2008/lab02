"""Registro de lo que la limpieza hizo con cada solicitud (``calidad`` en la respuesta)."""
from __future__ import annotations

from dataclasses import dataclass, field

from novamercado.limpieza.politicas import ADVERTIR, RECHAZAR, REPARAR, accion


class Rechazo(Exception):
    """La solicitud no se puede atender sin inventar datos -> HTTP 422."""

    def __init__(self, regla: str, campo: str, detalle: str):
        super().__init__(detalle)
        assert accion(regla) == RECHAZAR, f"La regla {regla} no es de rechazo"
        self.regla = regla
        self.campo = campo
        self.detalle = detalle


@dataclass
class Observacion:
    campo: str
    accion: str
    detalle: str
    regla: str

    def publica(self) -> dict:
        return {"campo": self.campo, "accion": self.accion, "detalle": self.detalle, "regla": self.regla}


@dataclass
class ReporteCalidad:
    observaciones: list[Observacion] = field(default_factory=list)

    def agregar(self, regla: str, campo: str, detalle: str) -> None:
        a = accion(regla)
        if a == RECHAZAR:
            raise Rechazo(regla, campo, detalle)
        self.observaciones.append(Observacion(campo=campo, accion=a, detalle=detalle, regla=regla))

    @property
    def estado(self) -> str:
        if any(o.accion == ADVERTIR for o in self.observaciones):
            return "advertencia"
        if any(o.accion == REPARAR for o in self.observaciones):
            return "corregido"
        return "ok"

    def publica(self) -> dict:
        return {"estado": self.estado, "observaciones": [o.publica() for o in self.observaciones]}
