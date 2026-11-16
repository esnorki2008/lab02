"""API HTTP (FastAPI). Sólo traduce HTTP <-> casos de uso: la limpieza vive en ``novamercado.limpieza`` y la
lógica de pronóstico en ``novamercado.servicio``."""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import Body, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from novamercado.airflow_cliente import AirflowNoDisponible
from novamercado.gestor_modelo import SinModelo
from novamercado.limpieza import Rechazo
from novamercado.schemas import (
    RespuestaModelo,
    RespuestaObservaciones,
    RespuestaPrediccion,
    RespuestaReentrenarEstado,
    RespuestaReentrenarInicio,
)

log = logging.getLogger(__name__)

EJEMPLO_PREDICCION = {"tienda_id": 5, "fecha": "2025-11-17",
                      "familias": [{"familia_id": "abarrotes", "en_promocion": 0},
                                   {"familia_id": "bebidas", "en_promocion": 8}]}
EJEMPLO_OBSERVACION = {"tienda_id": 5, "fecha": "2025-11-17", "ventas": [{"familia_id": "abarrotes", "ventas": 994}]}
ESTADOS_AIRFLOW_FALLA = {"failed"}
RESPUESTAS_ERROR = {
    422: {"description": "Rechazo: la solicitud no se puede atender sin inventar datos (campo `detail`)",
          "content": {"application/json": {"example": {"detail": "Tienda desconocida: la tienda 42 no existe en el "
                                                                 "catálogo", "regla": "tienda_desconocida",
                                                       "campo": "tienda_id"}}}},
    503: {"description": "Sin modelo cargado o dependencia no disponible (campo `detail`)"},
}


@dataclass
class Servicios:
    servicio: Any            # ServicioPronostico
    gestor: Any              # GestorModelo
    repo: Any                # Repositorio
    airflow: Any             # ClienteAirflow
    salud: Any               # MonitorSalud
    registro: Any = None     # para conocer la versión vigente al terminar un reentrenamiento


def _error(status: int, detalle: str, **extra) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": detalle, **extra})


async def _json(request: Request) -> object:
    cuerpo = await request.body()
    try:
        return json.loads(cuerpo.decode("utf-8-sig") or "null")
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise Rechazo("cuerpo_invalido", "body", f"El cuerpo no es JSON válido: {e}") from e


def crear_app(s: Servicios, on_startup=None, on_shutdown=None) -> FastAPI:
    app = FastAPI(
        title="NovaMercado — Pronóstico diario de ventas",
        version="1.0.0",
        description="Contrato del Laboratorio 2. Ver DECISIONES.md para las políticas de calidad de datos.",
        on_startup=[on_startup] if on_startup else None,
        on_shutdown=[on_shutdown] if on_shutdown else None,
    )
    app.state.servicios = s

    @app.exception_handler(Rechazo)
    async def _rechazo(request: Request, exc: Rechazo):
        return _error(422, exc.detalle, regla=exc.regla, campo=exc.campo)

    @app.exception_handler(RequestValidationError)
    async def _validacion(request: Request, exc: RequestValidationError):
        return _error(422, "Solicitud inválida: " + "; ".join(str(e.get("msg")) for e in exc.errors()),
                      errores=jsonable_encoder(exc.errors()))

    @app.exception_handler(SinModelo)
    async def _sin_modelo(request: Request, exc: SinModelo):
        return _error(503, "El servicio aún no tiene un modelo cargado; intente en unos segundos")

    def _registrar_rechazo(tipo: str, payload: object, exc: Rechazo) -> None:
        try:
            s.repo.guardar_rechazo(tipo, payload, exc.detalle, exc.regla)
        except Exception:  # el rechazo se responde aunque no se pueda registrar
            log.warning("No se pudo registrar el rechazo en la base de datos")

    # -------------------------------------------------------------------------------------- pronóstico
    @app.post("/v1/prediccion", response_model=RespuestaPrediccion, tags=["servicio"], responses=RESPUESTAS_ERROR,
              openapi_extra={"requestBody": {"content": {"application/json": {"example": EJEMPLO_PREDICCION}}}})
    async def prediccion(request: Request):
        payload = None
        try:
            payload = await _json(request)
            return await run_in_threadpool(s.servicio.predecir, payload)
        except Rechazo as exc:
            _registrar_rechazo("prediccion", payload, exc)
            raise
        except SinModelo:
            raise
        except Exception as exc:
            log.exception("Error al pronosticar")
            return _error(503, f"No se pudo atender la solicitud: {type(exc).__name__}")

    @app.post("/v1/observaciones", response_model=RespuestaObservaciones, tags=["servicio"],
              responses=RESPUESTAS_ERROR,
              openapi_extra={"requestBody": {"content": {"application/json": {"example": EJEMPLO_OBSERVACION}}}})
    async def observaciones(request: Request):
        payload = None
        try:
            payload = await _json(request)
            return await run_in_threadpool(s.servicio.observar, payload)
        except Rechazo as exc:
            _registrar_rechazo("observaciones", payload, exc)
            raise
        except Exception as exc:
            log.exception("Error al registrar observaciones")
            return _error(503, f"No se pudo registrar la observación: {type(exc).__name__}")

    # -------------------------------------------------------------------------------------- administración
    @app.post("/admin/reentrenar", status_code=202, response_model=RespuestaReentrenarInicio, tags=["admin"],
              responses={503: {"description": "No se puede iniciar el reentrenamiento"}})
    def reentrenar(_: Any = Body(None)):
        rid = f"reentrenamiento_{uuid.uuid4().hex[:12]}"
        try:
            s.repo.crear_reentrenamiento(rid)
        except Exception as exc:
            return _error(503, f"No se pudo registrar el reentrenamiento: {type(exc).__name__}")
        try:
            s.airflow.disparar(rid, {"origen": "api"})
        except Exception as exc:
            s.repo.terminar_reentrenamiento(rid, "fallido", False, _version_vigente(), f"No se pudo iniciar: {exc}")
            return _error(503, f"No se puede iniciar el reentrenamiento: {exc}")
        return {"id": rid}

    def _version_vigente() -> str | None:
        try:
            if s.registro is not None:
                v = s.registro.version_vigente()
                if v:
                    return v
        except Exception:
            pass
        return s.gestor.actual().version if s.gestor.cargado else None

    @app.get("/admin/reentrenar/{rid}", response_model=RespuestaReentrenarEstado,
             response_model_exclude_none=True, tags=["admin"],
             responses={404: {"description": "Id de ejecución desconocido"}})
    def estado_reentrenamiento(rid: str):
        try:
            fila = s.repo.obtener_reentrenamiento(rid)
        except Exception as exc:
            return _error(503, f"Base de datos no disponible: {type(exc).__name__}")
        if fila is None:
            return _error(404, f"No existe la ejecución de reentrenamiento '{rid}'")
        if fila["estado"] == "en_curso":
            try:
                estado_af = s.airflow.estado(rid)
            except AirflowNoDisponible:
                estado_af = "desconocido"
            if estado_af in ESTADOS_AIRFLOW_FALLA:
                s.repo.terminar_reentrenamiento(rid, "fallido", False, _version_vigente(),
                                                "La ejecución falló en Airflow; el modelo vigente no cambió")
                fila = s.repo.obtener_reentrenamiento(rid)
            else:
                return {"estado": "en_curso", "id": rid, "estado_airflow": estado_af}
        version = fila["version"] or _version_vigente() or ""
        return {"estado": fila["estado"], "promovido": bool(fila["promovido"]), "version": version,
                "motivo": fila["motivo"] or "", "id": rid,
                "iniciado_en": str(fila["iniciado_en"]), "terminado_en": str(fila["terminado_en"])}

    @app.post("/admin/recargar", tags=["admin"])
    def recargar():
        """Fuerza la sincronización del modelo con el alias del registro (la usa el DAG al promover)."""
        version = s.gestor.refrescar()
        return {"version": version, "error_registro": s.gestor.ultimo_error}

    # -------------------------------------------------------------------------------------- estado
    @app.get("/modelo", response_model=RespuestaModelo, tags=["estado"])
    def modelo():
        m = s.gestor.actual()
        return {"nombre": m.nombre, "alias": m.alias, "version": m.version}

    @app.get("/health", tags=["estado"])
    def health():
        codigo, cuerpo = s.salud.evaluar()
        return JSONResponse(status_code=codigo, content=cuerpo)

    return app
