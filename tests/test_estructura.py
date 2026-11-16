"""Reglas de estructura: el DAG sólo orquesta y cada política de datos está documentada."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from novamercado.limpieza.politicas import ADVERTIR, POLITICAS, RECHAZAR, REPARAR

RAIZ = Path(__file__).resolve().parents[1]
PESADOS = {"pandas", "numpy", "lightgbm", "mlflow", "sqlalchemy", "novamercado"}


def test_dag_no_importa_dependencias_pesadas_a_nivel_de_modulo():
    for dag in (RAIZ / "dags").glob("*.py"):
        arbol = ast.parse(dag.read_text(encoding="utf-8"))
        for nodo in arbol.body:
            if isinstance(nodo, (ast.Import, ast.ImportFrom)):
                nombres = [a.name for a in nodo.names] if isinstance(nodo, ast.Import) else [nodo.module or ""]
                assert not any(n.split(".")[0] in PESADOS for n in nombres), f"{dag.name}: {nombres}"


def test_dag_carga_en_airflow():
    pytest.importorskip("airflow")
    from airflow.models import DagBag

    bag = DagBag(dag_folder=str(RAIZ / "dags"), include_examples=False)
    assert not bag.import_errors
    assert "novamercado_reentrenamiento" in bag.dags


def test_cada_politica_tiene_accion_valida_y_esta_documentada():
    decisiones = (RAIZ / "DECISIONES.md").read_text(encoding="utf-8")
    for regla, politica in POLITICAS.items():
        assert politica.accion in (REPARAR, ADVERTIR, RECHAZAR)
        assert f"`{regla}`" in decisiones, f"La regla {regla} no está documentada en DECISIONES.md"
