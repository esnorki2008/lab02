# CLAUDE.md — reglas del proyecto para agentes de programación

Producto: servicio de pronóstico diario de ventas y cantidad sugerida por tienda × familia para
NovaMercado. FastAPI + PostgreSQL + MLflow + Airflow 3, todo con `docker compose up`.

## Estructura
- `src/novamercado/` — TODA la lógica. `limpieza/` defiende el contrato (sin HTTP); `servicio.py` son los
  casos de uso; `entrenamiento.py` el ciclo champion/challenger; `registro.py` el acceso a MLflow.
- `src/novamercado/api/` — FastAPI. **Sólo traduce HTTP ↔ casos de uso**: nada de limpieza ni de modelo en
  los endpoints.
- `dags/` — **sólo orquestan**: cada tarea importa una función de `novamercado.reentrenamiento` dentro de la
  tarea. Prohibido importar pandas/numpy/lightgbm/mlflow/sqlalchemy/novamercado a nivel de módulo
  (lo verifica `tests/test_estructura.py`). Las tareas corren en `/opt/novamercado-venv`
  (`@task.external_python`): Airflow y el producto tienen dependencias incompatibles.
- `config/params.yaml` — parámetros de negocio y modelo. Credenciales/URLs sólo por entorno (`.env`).
- `DECISIONES.md` — registrar toda decisión nueva (alternativas + justificación).

## Reglas no negociables
1. **Contrato del Anexo**: no omitir ni renombrar campos de las respuestas (se pueden agregar).
   Los modelos Pydantic de `schemas.py` son la referencia.
2. **Política de datos**: toda regla nueva de limpieza va en `limpieza/politicas.py` con UNA acción
   (`reparar`/`advertir`/`rechazar`) y se documenta en `DECISIONES.md §2` con el código entre backticks
   (`tests/test_estructura.py` falla si falta). Reglas generales, nunca casos particulares.
   Reparar = mismo dato escrito distinto; advertir = hubo un supuesto; rechazar = habría que inventar.
3. **Sin reloj del sistema** para validar fechas de negocio: sólo histórico, `feriados.csv` y la última
   observación. `db.ahora()` es sólo para auditoría.
4. **Disponibilidad temporal**: el pronóstico del día D sólo usa ventas < D. Entrenamiento y predicción usan
   la misma función `features.construir_features`.
5. **El alias `champion` se mueve al final del ciclo** y sólo si se cumple el criterio de `reemplazo`.
   Nada que falle antes puede afectar al modelo vigente.
6. **Observaciones idempotentes** (upsert por tienda, fecha, familia). `registros_guardados` = nuevos o
   modificados.
7. **Nada de datos ni credenciales en Git ni en imágenes**: `raw_data/` y `.env` están ignorados.
8. Versiones fijas en `requirements/*.txt` y en las imágenes base.

## Comandos
```bash
pip install -r requirements/dev.txt
pytest -q                          # sin servicios (SQLite + dobles de MLflow/Airflow + datos sintéticos)
ruff check src dags tests scripts
cp .env.example .env && docker compose up -d --build
python scripts/simular_operacion.py --dias 28 --cada 7
python scripts/prueba_resiliencia.py
```

## Convenciones
- Python 3.11, líneas ≤ 120, tipos en firmas públicas, docstrings y mensajes en español.
- Fechas `AAAA-MM-DD`. Nombres de dominio en español (`tienda_id`, `familia_id`, `en_promocion`, `ventas`).
