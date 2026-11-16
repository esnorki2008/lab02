"""Base de datos propia del producto (PostgreSQL en Docker; SQLite en las pruebas).

Se usa SQLAlchemy Core con SQL portable y sin ``pandas.read_sql``/``to_sql``, para que el mismo código
funcione con SQLAlchemy 1.4 (imagen de Airflow) y 2.0 (imagen de la API).
"""
from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import sqlalchemy as sa

log = logging.getLogger(__name__)

metadata = sa.MetaData()

tiendas = sa.Table(
    "tiendas", metadata,
    sa.Column("tienda_id", sa.Integer, primary_key=True),
    sa.Column("nombre", sa.Text), sa.Column("ciudad", sa.Text), sa.Column("formato", sa.Text),
    sa.Column("fecha_apertura", sa.Date),
)
familias = sa.Table(
    "familias", metadata,
    sa.Column("familia_id", sa.Text, primary_key=True), sa.Column("nombre", sa.Text),
)
feriados = sa.Table(
    "feriados", metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("fecha", sa.Date, nullable=False, index=True),
    sa.Column("tipo", sa.Text), sa.Column("ambito", sa.Text), sa.Column("descripcion", sa.Text),
)
costos = sa.Table(
    "costos", metadata,
    sa.Column("familia_id", sa.Text, primary_key=True),
    sa.Column("costo_faltante", sa.Float, nullable=False), sa.Column("costo_sobrante", sa.Float, nullable=False),
)
historico_ventas = sa.Table(
    "historico_ventas", metadata,
    sa.Column("tienda_id", sa.Integer, primary_key=True),
    sa.Column("familia_id", sa.Text, primary_key=True),
    sa.Column("fecha", sa.Date, primary_key=True),
    sa.Column("ventas", sa.Float), sa.Column("en_promocion", sa.Float),
)
cargas_datos = sa.Table(
    "cargas_datos", metadata,
    sa.Column("archivo", sa.Text, primary_key=True), sa.Column("sha256", sa.Text, nullable=False),
    sa.Column("filas", sa.Integer), sa.Column("cargado_en", sa.DateTime(timezone=True)),
)
solicitudes = sa.Table(
    "solicitudes", metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("tipo", sa.Text, nullable=False),            # prediccion | observaciones
    sa.Column("recibido_en", sa.DateTime(timezone=True), nullable=False),
    sa.Column("tienda_id", sa.Integer), sa.Column("fecha", sa.Date),
    sa.Column("payload", sa.JSON),
    sa.Column("http_status", sa.Integer), sa.Column("estado_calidad", sa.Text),
    sa.Column("detalle", sa.Text), sa.Column("version_modelo", sa.Text),
)
correcciones = sa.Table(
    "correcciones", metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("solicitud_id", sa.Integer, sa.ForeignKey("solicitudes.id"), nullable=False, index=True),
    sa.Column("campo", sa.Text), sa.Column("accion", sa.Text), sa.Column("regla", sa.Text),
    sa.Column("detalle", sa.Text),
)
predicciones = sa.Table(
    "predicciones", metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("solicitud_id", sa.Integer, sa.ForeignKey("solicitudes.id"), nullable=False, index=True),
    sa.Column("tienda_id", sa.Integer, nullable=False), sa.Column("fecha", sa.Date, nullable=False),
    sa.Column("familia_id", sa.Text, nullable=False), sa.Column("en_promocion", sa.Integer),
    sa.Column("promocion_supuesta", sa.Boolean, default=False),
    sa.Column("venta_esperada", sa.Float), sa.Column("cantidad_sugerida", sa.Integer),
    sa.Column("version_modelo", sa.Text, nullable=False),
    sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False),
    sa.Index("ix_predicciones_llave", "tienda_id", "fecha", "familia_id"),
)
observaciones = sa.Table(
    "observaciones", metadata,
    sa.Column("tienda_id", sa.Integer, primary_key=True),
    sa.Column("fecha", sa.Date, primary_key=True),
    sa.Column("familia_id", sa.Text, primary_key=True),
    sa.Column("ventas", sa.Float, nullable=False),
    sa.Column("atipico", sa.Boolean, nullable=False, default=False),
    sa.Column("solicitud_id", sa.Integer, sa.ForeignKey("solicitudes.id")),
    sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False),
    sa.Column("actualizado_en", sa.DateTime(timezone=True), nullable=False),
)
reentrenamientos = sa.Table(
    "reentrenamientos", metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("estado", sa.Text, nullable=False),          # en_curso | exitoso | fallido
    sa.Column("promovido", sa.Boolean), sa.Column("version", sa.Text), sa.Column("motivo", sa.Text),
    sa.Column("metricas", sa.JSON),
    sa.Column("iniciado_en", sa.DateTime(timezone=True)), sa.Column("terminado_en", sa.DateTime(timezone=True)),
)


def ahora() -> datetime:
    """Marca de auditoría (cuándo se registró algo). Nunca se usa para validar fechas de negocio."""
    return datetime.now(timezone.utc)


def crear_engine(url: str) -> sa.engine.Engine:
    kwargs: dict = {"pool_pre_ping": True}
    if url.startswith("postgresql"):
        kwargs.update(pool_size=5, max_overflow=5, connect_args={"connect_timeout": 3})
    return sa.create_engine(url, **kwargs)


def _insert(engine: sa.engine.Engine, table: sa.Table):
    if engine.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    return insert(table)


def _df(result) -> pd.DataFrame:
    return pd.DataFrame(result.fetchall(), columns=list(result.keys()))


def _fecha(v) -> date:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def json_seguro(v: object) -> object:
    """Copia del payload apta para una columna JSON: NaN/Infinity (válidos en Python, no en JSON) -> texto."""
    if isinstance(v, float) and (v != v or v in (float("inf"), float("-inf"))):
        return repr(v)
    if isinstance(v, dict):
        return {str(k): json_seguro(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [json_seguro(x) for x in v]
    return v


def _registros(df: pd.DataFrame) -> list[dict]:
    out = df.astype(object).where(pd.notna(df), None).to_dict("records")
    return out


class Repositorio:
    """Acceso a la base de datos del producto."""

    def __init__(self, engine: sa.engine.Engine):
        self.engine = engine

    @classmethod
    def desde_url(cls, url: str) -> Repositorio:
        return cls(crear_engine(url))

    # ------------------------------------------------------------------------------------------ esquema
    def crear_esquema(self) -> None:
        metadata.create_all(self.engine)

    def ping(self) -> None:
        with self.engine.connect() as c:
            c.execute(sa.text("SELECT 1"))

    # ------------------------------------------------------------------------------------------ carga inicial
    def hashes_cargados(self) -> dict[str, str]:
        with self.engine.connect() as c:
            return {r.archivo: r.sha256 for r in c.execute(sa.select(cargas_datos))}

    def cargar_publicados(self, datos) -> dict[str, int]:
        """Reemplaza catálogos e histórico con los archivos publicados (una sola transacción)."""
        filas = {}
        with self.engine.begin() as c:
            for tabla, df in ((historico_ventas, datos.historico), (feriados, datos.feriados),
                              (costos, datos.costos), (familias, datos.familias), (tiendas, datos.tiendas)):
                c.execute(tabla.delete())
                df = df.copy()
                if "fecha" in df.columns:
                    df["fecha"] = pd.to_datetime(df["fecha"]).dt.date
                recs = _registros(df[[col.name for col in tabla.columns if col.name in df.columns]])
                for i in range(0, len(recs), 10_000):
                    c.execute(tabla.insert(), recs[i:i + 10_000])
                filas[tabla.name] = len(recs)
            c.execute(cargas_datos.delete())
            c.execute(cargas_datos.insert(), [
                {"archivo": k, "sha256": v, "filas": filas.get({"historico": "historico_ventas"}.get(k, k)),
                 "cargado_en": ahora()} for k, v in datos.hashes.items()])
        return filas

    # ------------------------------------------------------------------------------------------ catálogos
    def tabla(self, nombre: str) -> pd.DataFrame:
        t = metadata.tables[nombre]
        with self.engine.connect() as c:
            return _df(c.execute(sa.select(t)))

    def rango_historico(self) -> tuple[date | None, date | None]:
        with self.engine.connect() as c:
            r = c.execute(sa.select(sa.func.min(historico_ventas.c.fecha),
                                    sa.func.max(historico_ventas.c.fecha))).one()
        return (_fecha(r[0]) if r[0] else None, _fecha(r[1]) if r[1] else None)

    def ultima_observacion(self) -> date | None:
        with self.engine.connect() as c:
            v = c.execute(sa.select(sa.func.max(observaciones.c.fecha))).scalar()
        return _fecha(v) if v else None

    # ------------------------------------------------------------------------------------------ series
    def ventas(self, tienda_id: int | None = None, desde: date | None = None,
               hasta: date | None = None) -> pd.DataFrame:
        """Ventas conocidas (histórico ∪ observaciones no atípicas; la observación manda) con la promoción.

        La promoción de los días observados se toma de la última solicitud de pronóstico de esa llave
        (las observaciones no traen promociones).
        """
        def filtros(t):
            conds = []
            if tienda_id is not None:
                conds.append(t.c.tienda_id == tienda_id)
            if desde is not None:
                conds.append(t.c.fecha >= desde)
            if hasta is not None:
                conds.append(t.c.fecha <= hasta)
            return conds

        llave = ["tienda_id", "familia_id", "fecha"]
        with self.engine.connect() as c:
            h = _df(c.execute(sa.select(historico_ventas).where(*filtros(historico_ventas))))
            o = _df(c.execute(sa.select(observaciones.c.tienda_id, observaciones.c.familia_id, observaciones.c.fecha,
                                        observaciones.c.ventas, observaciones.c.atipico)
                              .where(*filtros(observaciones))))
            p = _df(c.execute(sa.select(predicciones.c.id, predicciones.c.tienda_id, predicciones.c.familia_id,
                                        predicciones.c.fecha, predicciones.c.en_promocion)
                              .where(*filtros(predicciones), predicciones.c.promocion_supuesta.is_not(True))))
        for d in (h, o, p):
            if len(d):
                d["fecha"] = pd.to_datetime(d["fecha"])
        if len(o):
            o.loc[o["atipico"].astype(bool), "ventas"] = float("nan")   # atípico: dato no confiable
            o = o.drop(columns="atipico")
            if len(p):
                p = p.sort_values("id").drop_duplicates(llave, keep="last")[llave + ["en_promocion"]]
                o = o.merge(p, on=llave, how="left")
            else:
                o["en_promocion"] = float("nan")
            base = pd.concat([h, o], ignore_index=True).drop_duplicates(llave, keep="last")
        else:
            base = h
        if base.empty:
            return pd.DataFrame(columns=llave + ["ventas", "en_promocion"])
        base["ventas"] = base["ventas"].astype(float)
        base["en_promocion"] = base["en_promocion"].astype(float)
        return base.sort_values(llave).reset_index(drop=True)

    # ------------------------------------------------------------------------------------------ operación
    def registrar_solicitud(self, conn, tipo: str, payload: object, http_status: int, *, tienda_id=None,
                            fecha=None, estado_calidad=None, detalle=None, version_modelo=None,
                            observaciones_calidad: Iterable = ()) -> int:
        try:
            payload = json_seguro(payload)
            json.dumps(payload, allow_nan=False)
        except (TypeError, ValueError):
            payload = {"no_serializable": repr(payload)[:2000]}
        sid = conn.execute(solicitudes.insert().values(
            tipo=tipo, recibido_en=ahora(), tienda_id=tienda_id, fecha=fecha, payload=payload,
            http_status=http_status, estado_calidad=estado_calidad, detalle=detalle,
            version_modelo=version_modelo)).inserted_primary_key[0]
        obs = [{"solicitud_id": sid, "campo": o.campo, "accion": o.accion, "regla": o.regla, "detalle": o.detalle}
               for o in observaciones_calidad]
        if obs:
            conn.execute(correcciones.insert(), obs)
        return int(sid)

    def guardar_rechazo(self, tipo: str, payload: object, detalle: str, regla: str | None = None) -> None:
        with self.engine.begin() as c:
            self.registrar_solicitud(c, tipo, payload, 422, estado_calidad="rechazado",
                                     detalle=f"[{regla}] {detalle}" if regla else detalle)

    def guardar_prediccion(self, payload: object, limpia, reporte, version: str, filas: list[dict]) -> int:
        with self.engine.begin() as c:
            sid = self.registrar_solicitud(
                c, "prediccion", payload, 200, tienda_id=limpia.tienda_id, fecha=limpia.fecha,
                estado_calidad=reporte.estado, version_modelo=version, observaciones_calidad=reporte.observaciones)
            ts = ahora()
            c.execute(predicciones.insert(), [{
                "solicitud_id": sid, "tienda_id": limpia.tienda_id, "fecha": limpia.fecha,
                "familia_id": f["familia_id"], "en_promocion": f["en_promocion"],
                "promocion_supuesta": bool(f.get("promocion_supuesta", False)),
                "venta_esperada": f["venta_esperada"], "cantidad_sugerida": f["cantidad_sugerida"],
                "version_modelo": version, "creado_en": ts} for f in filas])
        return sid

    def guardar_observaciones(self, payload: object, limpia, reporte) -> int:
        """Upsert idempotente: devuelve cuántos registros quedaron nuevos o modificados."""
        cambios = 0
        with self.engine.begin() as c:
            sid = self.registrar_solicitud(c, "observaciones", payload, 200, tienda_id=limpia.tienda_id,
                                           fecha=limpia.fecha, estado_calidad=reporte.estado,
                                           observaciones_calidad=reporte.observaciones)
            ts = ahora()
            for v in limpia.ventas:
                ins = _insert(self.engine, observaciones).values(
                    tienda_id=limpia.tienda_id, fecha=limpia.fecha, familia_id=v.familia_id, ventas=float(v.ventas),
                    atipico=v.atipico, solicitud_id=sid, creado_en=ts, actualizado_en=ts)
                exc = ins.excluded
                stmt = ins.on_conflict_do_update(
                    index_elements=["tienda_id", "fecha", "familia_id"],
                    set_={"ventas": exc.ventas, "atipico": exc.atipico, "solicitud_id": exc.solicitud_id,
                          "actualizado_en": exc.actualizado_en},
                    where=sa.or_(observaciones.c.ventas.is_distinct_from(exc.ventas),
                                 observaciones.c.atipico.is_distinct_from(exc.atipico)),
                )
                cambios += max(c.execute(stmt).rowcount, 0)
            c.execute(solicitudes.update().where(solicitudes.c.id == sid)
                      .values(detalle=f"registros_guardados={cambios}"))
        return cambios

    # ------------------------------------------------------------------------------------------ reentrenamiento
    def crear_reentrenamiento(self, rid: str) -> None:
        with self.engine.begin() as c:
            c.execute(reentrenamientos.insert().values(id=rid, estado="en_curso", iniciado_en=ahora()))

    def asegurar_reentrenamiento(self, rid: str) -> None:
        with self.engine.begin() as c:
            existe = c.execute(sa.select(reentrenamientos.c.id).where(reentrenamientos.c.id == rid)).first()
            if not existe:
                c.execute(reentrenamientos.insert().values(id=rid, estado="en_curso", iniciado_en=ahora()))

    def terminar_reentrenamiento(self, rid: str, estado: str, promovido: bool | None, version: str | None,
                                 motivo: str, metricas: dict | None = None) -> None:
        self.asegurar_reentrenamiento(rid)
        with self.engine.begin() as c:
            c.execute(reentrenamientos.update().where(reentrenamientos.c.id == rid).values(
                estado=estado, promovido=promovido, version=version, motivo=motivo,
                metricas=json.loads(json.dumps(metricas or {}, default=str)), terminado_en=ahora()))

    def obtener_reentrenamiento(self, rid: str) -> dict | None:
        with self.engine.connect() as c:
            r = c.execute(sa.select(reentrenamientos).where(reentrenamientos.c.id == rid)).mappings().first()
        return dict(r) if r else None

    def reentrenamientos_en_curso_antiguos(self, minutos: int) -> list[str]:
        limite = ahora() - timedelta(minutes=minutos)
        with self.engine.connect() as c:
            rows = c.execute(sa.select(reentrenamientos.c.id).where(
                reentrenamientos.c.estado == "en_curso", reentrenamientos.c.iniciado_en < limite)).fetchall()
        return [r[0] for r in rows]
