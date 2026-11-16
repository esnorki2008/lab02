"""Casos de uso del servicio: pronosticar y registrar observaciones (sin nada de HTTP)."""
from __future__ import annotations

import logging
import threading
import time
from datetime import date, timedelta
from functools import lru_cache

import numpy as np
import pandas as pd

from novamercado.config import Settings
from novamercado.features import construir_features
from novamercado.gestor_modelo import GestorModelo
from novamercado.limpieza import Catalogo, ContextoLimpieza, limpiar_observaciones, limpiar_prediccion

log = logging.getLogger(__name__)


class ServicioPronostico:
    def __init__(self, settings: Settings, repo, gestor: GestorModelo, ttl_contexto_s: float = 300.0):
        self.settings = settings
        self.repo = repo
        self.gestor = gestor
        self.ttl = ttl_contexto_s
        self._lock = threading.Lock()
        self._estatico: dict | None = None
        self._estatico_ts = 0.0
        self._fecha_ref: date | None = None

    # ------------------------------------------------------------------------------ contexto de limpieza
    def _cargar_estatico(self) -> dict:
        """Catálogos y estadísticas del histórico (cambian sólo al recargar los archivos publicados)."""
        with self._lock:
            if self._estatico is not None and time.time() - self._estatico_ts < self.ttl:
                return self._estatico
            p = self.settings.params["limpieza"]
            tiendas = self.repo.tabla("tiendas")
            familias = self.repo.tabla("familias")
            feriados = self.repo.tabla("feriados")
            hist = self.repo.ventas()
            hist = hist[hist["ventas"].notna()]
            if hist.empty:
                raise RuntimeError("La base de datos aún no tiene el histórico cargado")
            fin = hist["fecha"].max()
            ultimo_anio = hist[hist["fecha"] > fin - pd.Timedelta(days=365)]
            p95 = ultimo_anio.groupby(["tienda_id", "familia_id"])["ventas"].quantile(0.95)
            umbral = {k: max(p["ventas_factor_atipico"] * v, p["ventas_minimo_atipico"]) for k, v in p95.items()}
            reciente = hist[hist["fecha"] > fin - pd.Timedelta(days=90)]
            fam_tienda = reciente.groupby("tienda_id")["familia_id"].apply(lambda s: sorted(set(s))).to_dict()
            self._estatico = {
                "catalogo": Catalogo(tiendas.to_dict("records"), familias.to_dict("records"),
                                     p["similitud_minima_familia"]),
                "tiendas": tiendas, "feriados": feriados,
                "fecha_minima": hist["fecha"].min().date(),
                "fecha_max_cal": pd.to_datetime(feriados["fecha"]).max().date() if len(feriados) else fin.date(),
                "fin_historico": fin.date(),
                "umbral": umbral,
                "promo_max": hist.groupby("familia_id")["en_promocion"].max().to_dict(),
                "familias_tienda": fam_tienda,
                "todas_familias": sorted(familias["familia_id"]),
            }
            self._estatico_ts = time.time()
            self._promo_cache.cache_clear()
            return self._estatico

    def fecha_referencia(self) -> date:
        if self._fecha_ref is None:
            ult = self.repo.ultima_observacion()
            base = ult or self._cargar_estatico()["fin_historico"]
            self._fecha_ref = base + timedelta(days=1)
        return self._fecha_ref

    @lru_cache(maxsize=256)  # noqa: B019 - el servicio vive lo mismo que el proceso
    def _promo_cache(self, tienda: int, fecha: date) -> dict[str, float]:
        dias = int(self.settings.params["limpieza"]["dias_mediana_promocion"])
        v = self.repo.ventas(tienda, fecha - timedelta(days=dias), fecha - timedelta(days=1))
        if v.empty or v["en_promocion"].notna().sum() == 0:
            v = self.repo.ventas(tienda, None, fecha - timedelta(days=1))
            v = v[v["fecha"] >= v["fecha"].max() - pd.Timedelta(days=dias)] if len(v) else v
        return v.groupby("familia_id")["en_promocion"].median().fillna(0).to_dict() if len(v) else {}

    def contexto(self) -> ContextoLimpieza:
        e = self._cargar_estatico()
        p = self.settings.params["limpieza"]
        return ContextoLimpieza(
            catalogo=e["catalogo"],
            fecha_minima=e["fecha_minima"],
            fecha_maxima_calendario=e["fecha_max_cal"],
            tolerancia_dias_sin_calendario=int(p["tolerancia_dias_sin_calendario"]),
            fecha_referencia=self.fecha_referencia(),
            familias_tienda=lambda t: e["familias_tienda"].get(t, e["todas_familias"]),
            promo_referencia=lambda t, f, d: float(self._promo_cache(t, d).get(f, 0.0)),
            promo_maximo=e["promo_max"],
            promocion_factor_atipico=float(p["promocion_factor_atipico"]),
            ventas_umbral_atipico=lambda t, f: float(e["umbral"].get((t, f), float("inf"))),
        )

    # ------------------------------------------------------------------------------ casos de uso
    def predecir(self, payload: object) -> dict:
        cargado = self.gestor.actual()            # SinModelo -> 503
        limpia, reporte = limpiar_prediccion(payload, self.contexto())   # Rechazo -> 422
        modelo = cargado.modelo
        e = self._cargar_estatico()
        d = limpia.fecha
        dias = int(self.settings.params["modelo"]["historia_features_dias"])
        hist = self.repo.ventas(limpia.tienda_id, d - timedelta(days=dias), d - timedelta(days=1))
        conocidas = hist.loc[hist["ventas"].notna(), "fecha"]
        limite = int(self.settings.params["limpieza"].get("dias_maximos_sin_datos", 7))
        ultima = conocidas.max().date() if len(conocidas) else None
        if ultima is None or (d - ultima).days - 1 > limite:
            desde = f"del {ultima.isoformat()}" if ultima else f"de hace más de {dias} días"
            reporte.agregar("historia_desactualizada", "fecha",
                            f"la última venta conocida de la tienda es {desde}; se pronosticó con su último nivel "
                            "conocido (sin las observaciones recientes)")
        objetivo = pd.DataFrame({
            "tienda_id": limpia.tienda_id, "familia_id": [f.familia_id for f in limpia.familias],
            "fecha": pd.Timestamp(d), "ventas": np.nan,
            "en_promocion": [float(f.en_promocion) for f in limpia.familias]})
        panel = pd.concat([hist, objetivo], ignore_index=True)
        X = construir_features(panel, e["tiendas"], e["feriados"], familias=modelo.familias,
                               formatos=modelo.formatos, tipos=modelo.tipos_evento, hasta=pd.Timestamp(d))
        X = X[X["fecha"] == pd.Timestamp(d)].set_index("familia_id").loc[objetivo["familia_id"]].reset_index()
        esperada = np.nan_to_num(modelo.venta_esperada(X), nan=0.0)
        sugerida = modelo.cantidad(esperada, X["familia_id"].to_numpy())
        filas = [{"familia_id": f.familia_id, "en_promocion": f.en_promocion,
                  "promocion_supuesta": f.promocion_supuesta,
                  "venta_esperada": round(float(v), 2), "cantidad_sugerida": int(q)}
                 for f, v, q in zip(limpia.familias, esperada, sugerida, strict=True)]
        self.repo.guardar_prediccion(payload, limpia, reporte, cargado.version, filas)
        return {
            "tienda_id": limpia.tienda_id,
            "fecha": d.isoformat(),
            "version_modelo": cargado.version,
            "predicciones": [{k: f[k] for k in ("familia_id", "venta_esperada", "cantidad_sugerida")} for f in filas],
            "calidad": reporte.publica(),
        }

    def observar(self, payload: object) -> dict:
        limpia, reporte = limpiar_observaciones(payload, self.contexto())
        n = self.repo.guardar_observaciones(payload, limpia, reporte)
        siguiente = limpia.fecha + timedelta(days=1)
        if self._fecha_ref is None or siguiente > self._fecha_ref:
            self._fecha_ref = siguiente
        self._promo_cache.cache_clear()
        return {"registros_guardados": n, "calidad": reporte.publica()}
