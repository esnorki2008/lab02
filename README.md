# NovaMercado — Servicio de pronóstico diario de ventas

Laboratorio 2 · Product Development. Servicio de ML en producción que:

1. **Pronostica** la venta esperada y la **cantidad sugerida** por familia para una tienda y una fecha.
2. **Defiende su contrato**: ante datos defectuosos repara, responde con advertencia o rechaza, y lo informa
   en `calidad` (política por tipo de problema en [DECISIONES.md §2](DECISIONES.md)).
3. **Registra la operación** en su propia PostgreSQL: solicitudes, predicciones (con la versión del modelo),
   correcciones aplicadas y ventas reales (idempotente).
4. **Mejora solo**: un DAG de Airflow reentrena con histórico + observaciones, compara contra el modelo
   vigente con un criterio explícito y, si lo supera, lo promueve en MLflow; la API lo empieza a usar sin
   reiniciarse.

Integra **FastAPI, PostgreSQL, MLflow y Apache Airflow 3**, y se levanta con un comando.

> Decisiones y justificaciones: **[DECISIONES.md](DECISIONES.md)** · Reglas para agentes: **[CLAUDE.md](CLAUDE.md)**
> · Evidencia de funcionamiento: **[evidencia/](evidencia/README.md)**

---

## 1. Requisitos

* Docker Desktop / Docker Engine con Compose v2 (≥ 6 GB de RAM para Docker).
* Puertos libres: `8000` (API), `5000` (MLflow), `8080` (Airflow). Se cambian en `.env`.
* (Opcional, para pruebas locales) Python 3.11.

## 2. Datos publicados

Coloque los archivos del GES **sin renombrar** en la carpeta `raw_data/` en la raíz del repositorio
(se busca recursivamente, así que también sirve descomprimir el `.zip` ahí dentro):

```
raw_data/
├── tiendas.csv
├── familias.csv
├── historico_ventas.csv
├── feriados.csv
├── costos.csv
├── catalogo_errores.md        (documentación, no la usa el servicio)
└── ejemplos/                  (idem)
```

`raw_data/` está en `.gitignore`: los datos no forman parte del repositorio ni de las imágenes (se montan
en modo sólo lectura en el contenedor `arranque`).

## 3. Levantar desde cero

```bash
git clone <repo> && cd lab02
# colocar los archivos en raw_data/ (sección 2)
cp .env.example .env            # credenciales locales (no se versionan)
docker compose up -d --build
```

Eso es todo. Al levantarse por primera vez, sin intervención:

1. `db`, `airflow-db` y `mlflow` arrancan con sus volúmenes.
2. `arranque` crea el esquema, carga los archivos publicados en la base del producto y, como no existe
   modelo, entrena el baseline y LightGBM, registra el mejor en MLflow (`novamercado-ventas`, alias
   `champion`) y termina.
3. La `api` detecta el alias en ≤ 20 s y carga el modelo. Airflow migra su base y publica el DAG.

`GET http://localhost:8000/health` → `{"estado": "ok", ...}` en **~30 s** después de las imágenes
(medido). Al volver a levantar (`docker compose down` + `up`, sin `-v`) se conserva todo: el arranque
compara el sha256 de los archivos con lo cargado y ve que ya hay modelo vigente, así que no repite trabajo.

| Servicio | URL | Notas |
|---|---|---|
| API + Swagger | <http://localhost:8000/docs> | Contrato del Anexo |
| MLflow | <http://localhost:5000> | Experimento `novamercado-pronostico`, modelo `novamercado-ventas` |
| Airflow | <http://localhost:8080> | Usuario/clave: `AIRFLOW_ADMIN_USER` / `AIRFLOW_ADMIN_PASSWORD` de `.env` |

Para borrar todo y empezar de cero: `docker compose down -v`.

## 4. Uso

```bash
# pronóstico (también acepta los ejemplos de raw_data/ejemplos/)
curl -X POST localhost:8000/v1/prediccion -H 'content-type: application/json' \
     -d '{"tienda_id": 5, "fecha": "2026-03-02", "familias": [{"familia_id": "bebidas", "en_promocion": 8}]}'

# ventas reales del día anterior (reenviar lo mismo responde registros_guardados = 0)
curl -X POST localhost:8000/v1/observaciones -H 'content-type: application/json' \
     -d '{"tienda_id": 5, "fecha": "2026-03-01", "ventas": [{"familia_id": "bebidas", "ventas": 1292}]}'

# reentrenamiento: devuelve {"id": ...}; el estado se consulta con ese id
curl -X POST localhost:8000/admin/reentrenar
curl localhost:8000/admin/reentrenar/<id>     # en_curso | exitoso | fallido (+ promovido, version, motivo)

curl localhost:8000/modelo                    # {"nombre", "alias", "version"}
curl localhost:8000/health                    # ok | degradado (+ motivo) | 503 sin modelo
```

Endpoints adicionales: `POST /admin/recargar` (el DAG la usa para que la API tome un modelo promovido de
inmediato). Las respuestas pueden traer campos adicionales al contrato (p. ej. `regla` en cada observación
de calidad, `componentes` en `/health`).

## 5. Estructura

```
src/novamercado/
  limpieza/          defensa del contrato: politicas.py (tabla única), numeros.py, fechas.py,
                     catalogo.py, solicitudes.py, reporte.py
  api/               FastAPI: app.py (endpoints), main.py (cableado de producción)
  schemas.py         modelos Pydantic del contrato
  servicio.py        casos de uso: pronosticar, registrar observaciones
  features.py        features (la misma función para entrenar y predecir)
  modelos.py         baseline, LightGBM, newsvendor y métricas
  entrenamiento.py   ciclo champion/challenger y criterio de reemplazo
  registro.py        MLflow (y registro en memoria para pruebas)
  gestor_modelo.py   modelo vigente en la API: recarga en caliente + caché local
  db.py              esquema y repositorio (PostgreSQL / SQLite)
  data.py            lectura de los archivos publicados
  arranque.py        primer arranque: datos + primer modelo
  reentrenamiento.py funciones que llama el DAG
  airflow_cliente.py, salud.py, config.py
dags/                novamercado_reentrenamiento.py (sólo orquesta)
docker/              api.Dockerfile, airflow.Dockerfile, mlflow.Dockerfile
config/params.yaml   parámetros de negocio y del modelo
requirements/        base, api, airflow (entorno aislado), mlflow, dev
tests/               pytest (sin servicios: SQLite + dobles de MLflow/Airflow + datos sintéticos)
scripts/             simular_operacion.py, prueba_resiliencia.py, exportar_evidencia.py
evidencia/           resultados de la simulación, resiliencia, MLflow, Airflow y ejemplos de la API
```

## 6. Pruebas y desarrollo local

```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements/dev.txt
pytest -q                                          # no necesita MLflow, Airflow ni PostgreSQL
ruff check src dags tests scripts
```

Con el producto levantado:

```bash
python scripts/simular_operacion.py --dias 28 --cada 7   # operación diaria + reentrenamientos + costo
python scripts/prueba_resiliencia.py                     # MLflow caído, reentreno fallido, reinicio
python scripts/exportar_evidencia.py                     # runs/versiones MLflow, DAG runs, conteos BD
```

## 7. Operación

* **MLflow caído**: la API sigue respondiendo con el modelo cargado; `/health` = `degradado`. Al volver,
  `ok` en segundos. Un reentrenamiento durante la caída termina `fallido` y no cambia el modelo vigente.
* **Reentrenamiento manual desde Airflow**: también puede dispararse el DAG `novamercado_reentrenamiento`
  desde la UI; su estado se consulta igual con `GET /admin/reentrenar/<run_id>`.
* **Datos publicados nuevos**: reemplazar los archivos en `raw_data/` y `docker compose up -d arranque`
  (recarga porque cambia el sha256; no reentrena si ya hay modelo: para eso, `POST /admin/reentrenar`).
* **Logs**: `docker compose logs -f api arranque airflow-scheduler`.
