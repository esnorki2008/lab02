"""Cliente mínimo de la API REST de Airflow 3 (disparar el DAG de reentrenamiento y consultar su estado)."""
from __future__ import annotations

import logging
import threading

import requests

log = logging.getLogger(__name__)


class AirflowNoDisponible(RuntimeError):
    pass


class ClienteAirflow:
    def __init__(self, url: str, usuario: str, clave: str, dag_id: str, timeout: float = 5.0):
        self.url, self.usuario, self.clave, self.dag_id, self.timeout = url.rstrip("/"), usuario, clave, dag_id, timeout
        self._token: str | None = None
        self._lock = threading.Lock()

    def _obtener_token(self) -> str:
        r = requests.post(f"{self.url}/auth/token", json={"username": self.usuario, "password": self.clave},
                          timeout=self.timeout)
        if r.status_code >= 400:
            raise AirflowNoDisponible(f"Airflow rechazó la autenticación ({r.status_code})")
        return r.json()["access_token"]

    def _req(self, metodo: str, ruta: str, **kw) -> requests.Response:
        try:
            for intento in range(2):
                with self._lock:
                    if self._token is None:
                        self._token = self._obtener_token()
                    token = self._token
                r = requests.request(metodo, f"{self.url}{ruta}", headers={"Authorization": f"Bearer {token}"},
                                     timeout=self.timeout, **kw)
                if r.status_code in (401, 403) and intento == 0:
                    with self._lock:
                        self._token = None
                    continue
                return r
        except requests.RequestException as e:
            raise AirflowNoDisponible(f"Airflow no responde: {e}") from e
        return r

    def ping(self) -> None:
        try:
            r = requests.get(f"{self.url}/api/v2/monitor/health", timeout=self.timeout)
        except requests.RequestException as e:
            raise AirflowNoDisponible(f"Airflow no responde: {type(e).__name__}") from e
        if r.status_code != 200:
            raise AirflowNoDisponible(f"Airflow health {r.status_code}")
        estado = (r.json().get("scheduler") or {}).get("status")
        if estado not in (None, "healthy"):
            raise AirflowNoDisponible(f"scheduler de Airflow: {estado}")
        r = self._req("GET", f"/api/v2/dags/{self.dag_id}")
        if r.status_code != 200:
            raise AirflowNoDisponible(f"el DAG {self.dag_id} aún no está publicado ({r.status_code})")

    def disparar(self, run_id: str, conf: dict | None = None) -> None:
        r = self._req("POST", f"/api/v2/dags/{self.dag_id}/dagRuns",
                      json={"dag_run_id": run_id, "logical_date": None, "conf": conf or {}})
        if r.status_code >= 300:
            raise AirflowNoDisponible(f"No se pudo disparar el DAG ({r.status_code}): {r.text[:300]}")

    def estado(self, run_id: str) -> str | None:
        """queued | running | success | failed | None (no existe)."""
        r = self._req("GET", f"/api/v2/dags/{self.dag_id}/dagRuns/{run_id}")
        if r.status_code == 404:
            return None
        if r.status_code >= 300:
            raise AirflowNoDisponible(f"Airflow respondió {r.status_code}")
        return r.json().get("state")
