# Capturas de evidencia de funcionamiento

Tomadas el 2026-11-15 (hora local), después del reinicio limpio del entorno. Se usó Playwright 1.48
(Chromium headless, viewport 1600×1000; algunas a 1900 de ancho o 1900 de alto para que entraran todas las
columnas y métricas) contra los servicios levantados con `docker compose`: API `localhost:8000`, MLflow
`localhost:5000` y Airflow `localhost:8081`.

- **MLflow y Airflow**: capturas de la interfaz web real (Airflow con login de formulario).
- **API**: capturas de la interfaz Swagger (`/docs`) usando "Try it out" → cuerpo pegado → "Execute". Se ven
  la solicitud (cuerpo JSON), el código HTTP y la respuesta real del servidor, además de las respuestas
  documentadas (200, 422 y 503). Se ocultó con CSS el bloque `curl` y se quitó el límite de alto del cuerpo de
  la respuesta para que se viera completo. Los cuerpos son los archivos de `raw_data/ejemplos/` sin cambios,
  salvo lo que se indica en la tabla.

| Archivo | Qué muestra |
|---|---|
| `01_mlflow_experimento_runs.png` | Experimento `novamercado-pronostico` (12 runs): 4 runs de ciclo con sus runs anidados `candidato-lgbm`/`candidato-baseline` expandidos; columnas `candidato.costo/wape`, `eval.costo/wape`, `vigente.costo/wape` y tags `decision`, `version_registrada`. |
| `02_mlflow_run_ciclo_metricas.png` | Run de ciclo `airflow-2026-03-20` (decision=promovido, versión registrada 2): tags, parámetros y métricas candidato vs vigente. |
| `03_mlflow_modelo_versiones.png` | Modelo registrado `novamercado-ventas` con versiones 1 y 2, tags de origen y alias `@champion` en la versión 2. |
| `04_mlflow_version_linaje.png` | Versión vigente (2, alias champion, LightGBM): run de origen `airflow-2026-03-20`, descripción y tags (linaje). |
| `04b_mlflow_version1_linaje.png` | Versión 1: run de origen `arranque-2026-02-28` y sin alias (ya no es champion). |
| `05_airflow_dag_ejecuciones.png` | Pestaña Runs del DAG `novamercado_reentrenamiento`: 3 ejecuciones success y 1 failed, y el grid de las 3 tareas. |
| `05b_airflow_dag_grid.png` | Overview del DAG: grid, duración de las 4 ejecuciones y "Recent Failed Task Logs" (verificar). |
| `06_airflow_ejecucion_exitosa_tareas.png` | Ejecución exitosa `reentrenamiento_51fd62420754` (la que promovió la v2): verificar → entrenar_evaluar_promover → publicar_resultado, todas Success. |
| `07_airflow_ejecucion_fallida.png` | Ejecución fallida `reentrenamiento_0b574c0257ed`: `verificar` Failed y las otras dos Upstream Failed. |
| `07b_airflow_log_error_verificar.png` | Log de `verificar` en la ejecución fallida (inicio): traceback de conexión a `mlflow:5000` (nombre no resuelto). |
| `07c_airflow_log_error_final.png` | Final del mismo log: `registro.ping` → `requests.exceptions.ConnectionError` (MLflow caído) y `Task failed with exception`. |
| `08_api_swagger.png` | Swagger `/docs` con todos los endpoints (servicio, admin, estado) y esquemas. |
| `09_api_prediccion_valida.png` | `POST /v1/prediccion` con `prediccion_valida.json` → 200, 12 familias, `calidad.estado = ok`, `version_modelo = 2`. |
| `10_api_prediccion_corregida_E02.png` | `POST /v1/prediccion` con `E02_familia_otra_forma.json` → 200, `estado = corregido` (regla `familia_formato`: "Frutas y Verduras", "LÁCTEOS"). |
| `11_api_prediccion_advertencia_E05.png` | `POST /v1/prediccion` con `E05_faltan_familias.json` → 200, `estado = advertencia` (regla `familias_incompletas`: bebe y mascotas). |
| `12_api_rechazo_422.png` | `POST /v1/prediccion` con `prediccion_valida.json` cambiando `tienda_id` a 42 → 422, regla `tienda_desconocida`; abajo se ven las respuestas 422/503 documentadas. |
| `12b_api_prediccion_corregida_E01_tienda_texto.png` | `POST /v1/prediccion` con `E01_tienda_texto.json` ("T05") → 200, `estado = corregido` (regla `tienda_formato`). |
| `13_api_observacion_valida.png` | `POST /v1/observaciones` con `observacion_valida.json` cambiando la fecha a 2026-04-15 → 200, `registros_guardados = 12`, `estado = ok`. |
| `13b_api_observacion_valida_archivo_oficial_ya_registrada.png` | `POST /v1/observaciones` con `observacion_valida.json` tal cual (2025-11-17) → 200, `registros_guardados = 0` (ya estaba cargada: idempotencia). |
| `14_api_observacion_repetida_E09.png` | `POST /v1/observaciones` con `E09_envio_repetido.json` → 200, `registros_guardados = 0` (upsert idempotente). |
| `14b_api_observacion_reenvio_2026-04-15.png` | Reenvío idéntico de la observación de la captura 13 (2026-04-15) → 200, `registros_guardados = 0`. |
| `15_api_observacion_negativa_E08.png` | `POST /v1/observaciones` con `E08_ventas_negativas.json` → 200, `estado = advertencia` (regla `ventas_negativa`: el −4 no se guardó); `registros_guardados = 0` porque las demás familias de esa fecha ya existían. |
| `16_api_health_y_modelo.png` | `GET /modelo` (novamercado-ventas, alias champion, versión 2) y `GET /health` (base_de_datos, mlflow, airflow y modelo en "ok", versión 2). |
