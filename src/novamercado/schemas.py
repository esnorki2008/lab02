"""Modelos Pydantic del contrato del servicio (Anexo del enunciado).

* ``*Limpia``: solicitud ya defendida por ``novamercado.limpieza`` (tipos estrictos del contrato).
* ``Respuesta*``: lo que el servicio devuelve; FastAPI valida cada respuesta contra estos modelos.
"""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer


# ------------------------------------------------------------------------------------------ solicitudes
class FamiliaSolicitada(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    familia_id: str = Field(min_length=1)
    en_promocion: int = Field(ge=0)
    completada: bool = False          # no venía en la solicitud; la agregó la limpieza
    promocion_supuesta: bool = False  # en_promocion imputada (no informada por la tienda)


class PrediccionLimpia(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tienda_id: int
    fecha: date
    familias: list[FamiliaSolicitada] = Field(min_length=1)


class VentaObservada(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    familia_id: str = Field(min_length=1)
    ventas: int = Field(ge=0)
    atipico: bool = False


class ObservacionLimpia(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tienda_id: int
    fecha: date
    ventas: list[VentaObservada] = Field(min_length=1)


# ------------------------------------------------------------------------------------------ respuestas
class ObservacionCalidad(BaseModel):
    campo: str
    accion: Literal["reparar", "advertir"]
    detalle: str
    regla: str | None = None   # campo adicional: código de la política aplicada (DECISIONES.md)


class Calidad(BaseModel):
    estado: Literal["ok", "corregido", "advertencia"]
    observaciones: list[ObservacionCalidad] = []


class PrediccionFamilia(BaseModel):
    familia_id: str
    venta_esperada: float
    cantidad_sugerida: int = Field(ge=0)


class RespuestaPrediccion(BaseModel):
    tienda_id: int
    fecha: date
    version_modelo: str
    predicciones: list[PrediccionFamilia]
    calidad: Calidad

    @field_serializer("fecha")
    def _fecha(self, v: date) -> str:
        return v.isoformat()


class RespuestaObservaciones(BaseModel):
    registros_guardados: int = Field(ge=0)
    calidad: Calidad


class RespuestaReentrenarInicio(BaseModel):
    id: str


class RespuestaReentrenarEstado(BaseModel):
    model_config = ConfigDict(extra="allow")
    estado: Literal["en_curso", "exitoso", "fallido"]
    promovido: bool | None = None
    version: str | None = None
    motivo: str | None = None


class RespuestaModelo(BaseModel):
    nombre: str
    alias: str
    version: str


class RespuestaHealth(BaseModel):
    model_config = ConfigDict(extra="allow")
    estado: Literal["ok", "degradado"]
    motivo: str | None = None
