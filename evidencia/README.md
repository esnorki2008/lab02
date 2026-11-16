# Evidencia de funcionamiento

Generada con el producto levantado desde cero (`docker compose down -v && docker compose up -d --build`)
con los datos publicados en `raw_data/`, usando los scripts de `scripts/`.

| Archivo | Qué muestra |
|---|---|
| `health.json`, `modelo.json` | Estado final del servicio (`ok`) y modelo vigente (`novamercado-ventas@champion`). |
| `catalogo/` | Respuesta de la API a cada ejemplo oficial (`raw_data/ejemplos/`, E01–E10) y a tres rechazos (`rechazos.txt`). |
| `simulacion/resumen.json` | 28 días de operación simulada (2026-03-01 → 2026-03-28) con defectos aleatorios, cambio de demanda desde el día 9 y reentrenamiento semanal. |
| `simulacion/respuestas/` | Una respuesta real por tipo de defecto inyectado (incluidos casos fuera del catálogo: error de escritura, promoción negativa o decimal, fecha con hora, tienda desconocida). |
| `simulacion/eventos.csv` | Cada solicitud de la simulación: defecto, código HTTP, estado de calidad, latencia, versión del modelo. |
| `resiliencia.json` | MLflow detenido, reentrenamiento fallido, API reiniciada sin MLflow, regreso de MLflow y reinicio sin borrar volúmenes. |
| `mlflow.json` | Experimento, *runs* (ciclos y candidatos anidados con métricas y decisión), versiones registradas y alias. |
| `airflow_dag_runs.json` | Ejecuciones del DAG `novamercado_reentrenamiento` y el estado de cada tarea. |
| `base_de_datos.txt` | Conteo de registros por tabla y correcciones aplicadas por regla. |
| `capturas/` | Capturas de pantalla de MLflow, Airflow y la API (Swagger) — índice en `capturas/README.md`. |

## Resultados principales

**Arranque desde cero**: `/health` = `ok` 33 s después de `docker compose up` (límite: 10 min). El primer
modelo (LightGBM) se entrena y registra solo como versión 1.

**Operación simulada (28 días, 10 tiendas, reentrenamiento cada 7 días)**

| Política | Costo (costos.csv) |
|---|---|
| Cantidad sugerida del servicio | **91 062** |
| Sugerir la venta esperada (sin newsvendor) | 122 004 |
| Ingenua: lo vendido hace 7 días | 177 465 |

WAPE 0.19 · latencia de `/v1/prediccion` p50 121 ms / p95 174 ms.

| Reentrenamiento | Resultado | Duración |
|---|---|---|
| día 7 | no promovido: costo 16 657 vs 16 707 del vigente (< 1 % de mejora) | 12 s |
| día 14 | no promovido: 39 399 vs 39 772 | 12 s |
| día 21 | **promovido v2**: 48 842 vs 49 580; la API la usó de inmediato (aviso del DAG) | 12 s |

**Resiliencia**

| Escenario | Resultado |
|---|---|
| MLflow detenido | `/health` = `degradado` en 8 s (límite 1 min); `/v1/prediccion` sigue respondiendo 200 con la v2 |
| Reentrenamiento con MLflow caído | `fallido`, `promovido=false`, `version` = 2 (el vigente no cambia) |
| API reiniciada con MLflow caído | carga la v2 desde la caché local en 2 s y responde |
| MLflow regresa | `/health` = `ok` en 6 s (límite 2 min) |
| `docker compose down` + `up` (sin `-v`) | `ok` en 30 s, misma versión, reenvío de una observación → `registros_guardados = 0`; el arranque no recarga datos ni reentrena |

