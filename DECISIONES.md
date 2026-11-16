# DECISIONES.md

Decisiones técnicas y de producto del servicio de pronóstico de NovaMercado: qué se decidió, qué
alternativas había y por qué. Los parámetros citados viven en `config/params.yaml`.

---

## 1. Arquitectura

| Decisión | Alternativas | Justificación |
|---|---|---|
| **Cinco piezas en Docker Compose**: `api` (FastAPI), `db` (PostgreSQL del producto), `mlflow`, Airflow 3 (`airflow-apiserver`, `-scheduler`, `-dag-processor`) con **su propia** base `airflow-db`, y un contenedor de un solo uso `arranque`. | Una sola base para todo; entrenar desde la API. | El enunciado exige separar la base del producto de la de Airflow. Separar `arranque` mantiene la API liviana y hace explícito el trabajo de “primera vez”. |
| **La lógica vive en `src/novamercado/`**; la API y el DAG sólo traducen (HTTP → caso de uso, tarea → función). | Lógica en endpoints/DAG. | Se prueba sin servidores (`pytest`), y el mismo código corre en la API, el arranque y Airflow. Una prueba verifica que el DAG no importe dependencias pesadas a nivel de módulo. |
| **Dependencias del producto aisladas dentro de la imagen de Airflow** (`/opt/novamercado-venv`, tareas con `@task.external_python`). | Instalar todo en el intérprete de Airflow. | Conflicto real: Airflow 3.1.3 exige `packaging>=25` y `mlflow-skinny 2.19` exige `<25`. El entorno aislado evita romper Airflow y deja versiones fijas para el producto. |
| **SQLAlchemy Core con SQL portable**; PostgreSQL en producción y SQLite en las pruebas. | ORM; pruebas con PostgreSQL real. | Las pruebas no necesitan servicios y ejercitan el mismo repositorio (incluido el *upsert* idempotente). |
| **MLflow con *backend* SQLite en un volumen y proxy de artefactos** (`--serve-artifacts`). | MLflow sobre la PostgreSQL del producto; artefactos en volumen compartido. | Mantiene la base del producto sólo con datos de negocio; los clientes (API, Airflow) no comparten disco con MLflow: todo pasa por HTTP. |
| **Versiones fijas** de imágenes (`python:3.11.10-slim-bookworm`, `postgres:16.4-bookworm`, `apache/airflow:3.1.3-python3.11`) y de paquetes (`requirements/*.txt`). | Etiquetas flotantes. | Reproducibilidad. Un SQLAlchemy sin fijar rompió el servidor de MLflow durante el desarrollo; por eso `requirements/mlflow.txt` fija sus dependencias críticas. |

## 2. Defensa del contrato: política ante cada problema en los datos

**Principio:** se *repara* sólo cuando el dato es el mismo escrito de otra forma (corrección inequívoca);
se responde *con advertencia* cuando hay que suponer algo y el supuesto es razonable y explicable; se
*rechaza* (422) cuando responder exigiría inventar el dato central de la solicitud (qué tienda, qué día).
Las reglas son **generales** (por tipo de problema, no por ejemplo), y la tabla única de políticas está en
`src/novamercado/limpieza/politicas.py`; cada observación de la respuesta incluye el campo adicional
`regla` con el código de esta tabla. Una prueba verifica que cada regla del código esté documentada aquí.

### 2.1 Estructura

| Regla | Acción | Qué cubre | Justificación |
|---|---|---|---|
| `cuerpo_invalido` | rechazar | El cuerpo no es JSON o no es un objeto. | No hay nada que interpretar. |
| `campo_renombrado` | reparar | Campo con otro nombre o forma: `tienda`, `TiendaId`, `store_id`, `id_tienda`, `Fecha`… (E07). Se normalizan mayúsculas, acentos, *camelCase* y separadores y se compara con una lista de alias por campo. | El valor es el mismo; sólo cambia la etiqueta. |
| `campo_conflicto` | advertir | El mismo campo llega con dos nombres y valores distintos. Se usa el del contrato. | Hay que elegir; se informa cuál. |
| `lista_faltante` | rechazar | No viene la lista `familias` / `ventas`, o viene vacía. | Sin familias no hay qué pronosticar ni qué guardar. |
| `lista_estructura` | reparar | La lista llega como objeto `{"abarrotes": 3, ...}`. | Misma información en otra estructura. |
| `elemento_invalido` | advertir | Un elemento de la lista no es un objeto con familia; se omite. | El resto de la solicitud sigue siendo útil. |
| `sin_registros_validos` | rechazar | Tras limpiar, no queda ninguna familia/venta utilizable. | Responder vacío sería engañoso. |

### 2.2 Tienda y fecha

| Regla | Acción | Qué cubre | Justificación |
|---|---|---|---|
| `tienda_faltante` | rechazar | No viene `tienda_id`. | Es la llave del pronóstico. |
| `tienda_formato` | reparar | `"5"`, `5.0`, `"T05"`, `"tienda 5"`, `"T-05"` (E01). Prefijos conocidos + dígitos y el número debe existir en el catálogo. | Mismo identificador escrito distinto. |
| `tienda_nombre` | reparar | Llega el nombre del catálogo (`"NovaMercado Antigua"`, `"Antigua"`). | Corresponde a una sola tienda. |
| `tienda_desconocida` | rechazar | El id no existe, es negativo o no es interpretable (`42`, `"-5"`, `"Zona 99"`). | Pronosticar otra tienda sería inventar; un signo no es una forma de escribir el id. |
| `tienda_no_abierta` | rechazar | Fecha anterior a `fecha_apertura` (tienda 10 abrió el 2025-03-15). | No hay ventas que pronosticar. |
| `fecha_faltante` | rechazar | No viene la fecha. | Es la llave del pronóstico. |
| `fecha_formato` | reparar | `2025-11-17T00:00:00`, `17/11/2025`, `2025/11/17`, `20251117`, `17 de noviembre de 2025`, `11/17/2025` (E04). | Interpretación única. |
| `fecha_ambigua` | advertir | `05/03/2026`: día y mes intercambiables. Se elige la interpretación **más cercana a la fecha de operación** (día siguiente a la última observación recibida) y, si empatan, día/mes (uso en Guatemala). | Hay un supuesto; se explica cuál se tomó y cuál se descartó. No usa el reloj del sistema. |
| `fecha_invalida` | rechazar | Texto que no es fecha o fecha imposible (`2025-02-30`). | No se puede adivinar. |
| `fecha_fuera_de_rango` | rechazar | Antes del inicio del histórico o más de 45 días después del último feriado publicado (`2071-08-10`). | Fuera del dominio del modelo y del calendario: casi seguro un error de captura. |
| `historia_desactualizada` | advertir | La tienda lleva más de 7 días (`dias_maximos_sin_datos`) sin ventas conocidas antes de la fecha: el pronóstico usa su último nivel conocido. | El servicio responde (el modelo se entrenó ocultando rezagos recientes), pero el cliente debe saber que la calidad baja. |
| `fecha_sin_calendario` | advertir | Posterior al último feriado publicado, dentro de la tolerancia: se supone un día sin eventos especiales. | El modelo usa feriados; se informa que no se conocen. |

**Sin reloj del sistema:** la validez de una fecha depende sólo de los datos (histórico, `feriados.csv`
y la última observación recibida), porque el servicio opera sobre fechas simuladas.

### 2.3 Familias

| Regla | Acción | Qué cubre | Justificación |
|---|---|---|---|
| `familia_formato` | reparar | `"LÁCTEOS"`, `"lacteos "`, `"Frutas y Verduras"`, `"frutas-verduras"`, el nombre del catálogo (E02). Se compara sin acentos, sin mayúsculas, sin separadores y sin palabras vacías (“y”, “de”) contra id y nombre del catálogo. | Mismo dato, otra forma. |
| `familia_aproximada` | advertir | Error de escritura con un **único** candidato parecido (similitud ≥ 0.80 y ≥ 0.10 sobre el segundo): `"carnez"` → `carnes`. | Probable pero no seguro: se informa el supuesto. |
| `familia_desconocida` | advertir | No existe en el catálogo: se omite del resultado. | El resto de la solicitud es válido; no se inventa una familia. |
| `familia_repetida` | reparar | Repetida con los mismos valores: se deja una. | Duplicado exacto. |
| `familia_repetida_conflicto` | advertir | Repetida con valores distintos (E06): **se usa la última aparición**. | La última suele ser la corrección del sistema que envía; se informa ambos valores. |
| `familias_incompletas` | advertir | Faltan familias que la tienda vende (E05; las que tuvieron ventas en los últimos 90 días): **se pronostican también**, con la promoción supuesta igual a la mediana de los últimos 28 días. | El objetivo de la mañana es preparar inventario de todas las familias; omitirlas deja al negocio sin cantidad sugerida. Las filas agregadas quedan marcadas en la base (`promocion_supuesta`). Alternativa descartada: responder sólo lo pedido (deja huecos) o rechazar (detiene la operación). |

### 2.4 Números (`en_promocion`, `ventas`)

Regla general de interpretación (`limpieza/numeros.py`): texto con dígitos → número; con `.` y `,` a la vez
el último es decimal (`"1.234,5"`); un separador seguido de exactamente tres dígitos o repetido es de miles
(`"1,234"` → 1234); en otro caso es decimal (`"812,0"` → 812). Las cantidades del contrato son unidades,
por eso `"1,234"` se lee como mil doscientos treinta y cuatro (E10).

| Regla | Acción | Qué cubre | Justificación |
|---|---|---|---|
| `promocion_formato` | reparar | `"3"`, `2.0`, `"1,0"` (E03). | Mismo valor entero. |
| `promocion_faltante` | advertir | Ausente, nulo o no numérico: se usa la mediana de `en_promocion` de la tienda y familia en los 28 días previos. | Las promociones mueven la demanda; suponer 0 sesga a la baja. La mediana reciente es el supuesto más neutral. |
| `promocion_negativa` | advertir | Valor negativo (imposible): mismo tratamiento que faltante. | Es un dato inválido, no un dato. |
| `promocion_decimal` | advertir | `2.5`: se redondea. | Es un conteo de productos. |
| `promocion_atipica` | advertir | Más de 3× el máximo histórico de la familia: se acota a ese máximo. | Evita extrapolar con un valor seguramente mal capturado. |
| `ventas_formato` | reparar | `"1,234"`, `"812,0"`, `"994"` (E10). | Mismo valor. |
| `ventas_faltante` | advertir | Ausente o no numérico: ese registro no se guarda. | No se inventa una venta. |
| `ventas_negativa` | advertir | `-4` (devoluciones, E08): ese registro no se guarda. | Una devolución no es demanda; guardarla como 0 también sería inventar. El resto de la observación se guarda. |
| `ventas_decimal` | advertir | Valor no entero: se redondea. | El histórico está en unidades enteras. |
| `ventas_atipica` | advertir | Más de 8× el p95 del último año de la serie (mínimo 50 u.): **se guarda marcada** (`atipico`) y no se usa ni para features ni para entrenar. | No se descarta el dato reportado (auditoría), pero un ×10 mal digitado no debe contaminar el pronóstico. |

**Envío repetido (E09):** no es un error de datos: las observaciones se guardan con *upsert* por
(tienda, fecha, familia). Reenviar lo mismo responde 200 con `registros_guardados = 0`; si el valor cambió,
se actualiza y cuenta como modificado (la observación más reciente es la corrección).

**Campos desconocidos** se ignoran sin observación: no son un problema de los datos del contrato.

**Valores `NaN`/`Infinity`** (aceptados por el parser JSON de Python pero no por PostgreSQL) se tratan como
no interpretables según la regla del campo (`promocion_faltante`, `ventas_faltante`) y el *payload* se
guarda con esos valores convertidos a texto, para no perder la solicitud.

**Observaciones de fechas ya cubiertas por el histórico**: se aceptan y reemplazan al histórico de esa llave
(la observación es el dato más reciente, p. ej. una corrección). Los ejemplos oficiales usan una fecha del
histórico (2025-11-17), así que advertir en ese caso marcaría como problema una solicitud válida.

## 3. Información que usa el modelo al predecir

* **Regla temporal:** la fila del día D usa ventas de fechas **< D** (histórico ∪ observaciones, la
  observación manda sobre el histórico) y, del día D, sólo lo que se conoce por adelantado: calendario,
  feriados y la promoción que llega en la solicitud. La misma función (`features.construir_features`) arma
  entrenamiento y predicción; una prueba verifica que alterar las ventas ≥ D no cambia ningún feature de D.
* **Ventas relativas a una escala**: rezagos (1, 2, 3, 7, 14, 21, 28, 364 días), medias móviles (7, 14, 28,
  56), media del mismo día de la semana (4 semanas), desviación y año anterior, todos divididos por la
  `escala` = media de los últimos 28 valores **conocidos** de la serie. El modelo aprende la *forma* de la
  demanda y la escala lleva el *nivel*: si la demanda cambia (subida o caída de una familia), el pronóstico
  se ajusta en cuanto llegan las observaciones, sin esperar a reentrenar.
* **Datos faltantes ≠ 0**: si una observación no llegó, sus rezagos quedan vacíos (LightGBM los maneja),
  `dias_sin_dato` lo indica y la escala usa los últimos datos conocidos aunque sean antiguos. Para que el
  modelo sepa operar así, al entrenar se ocultan los rezagos 1–3 días en el 10 % de las filas.
* **Promociones**: la del día, la media de 28 días previa y la diferencia. Para los días observados se usa la
  promoción enviada en la última solicitud de pronóstico de esa llave (las observaciones no la traen);
  las promociones *supuestas* por la limpieza no se usan para entrenar.
* **Calendario y eventos**: día de semana, día del mes, mes, día del año, días a fin de mes; banderas por
  tipo de evento de `feriados.csv` (`feriado`, `comercial`, `pago`, `medio_dia`) del día, la víspera y el día
  siguiente, aplicando los nacionales y los de la **ciudad de la tienda**; días al/desde el próximo evento.
* **Categóricas**: tienda, familia y formato de la tienda.

## 4. Modelos y métrica de negocio

| Decisión | Alternativas | Justificación |
|---|---|---|
| **Baseline**: media del mismo día de la semana de las últimas 4 semanas (estacional ingenuo). | Último valor; media móvil. | Simple, fuerte en datos con patrón semanal y sin entrenamiento. |
| **Modelo ML**: LightGBM (API nativa) sobre `ventas / escala`, ponderado por la escala, para todas las series a la vez. | Un modelo por serie; Prophet/ARIMA; objetivo log. | 120 series cortas: un modelo global comparte patrones (feriados, promociones). Ponderar por escala equivale a minimizar el error en unidades. |
| **Métricas**: costo de negocio (principal), WAPE, MAE, RMSE y sesgo. | Sólo técnicas. | El enunciado evalúa el costo de la cantidad sugerida. |

Comparación en tres cortes del histórico (14 días de evaluación fuera de muestra; costo con `costos.csv`):

| Corte | Baseline costo / WAPE | LightGBM costo / WAPE |
|---|---|---|
| 2025-12-31 | 99 677 / 0.286 | **60 691 / 0.181** |
| 2026-01-31 | 66 043 / 0.278 | **42 148 / 0.177** |
| 2026-02-28 | 54 920 / 0.223 | **44 824 / 0.177** |

Se probaron también 63 hojas/800 árboles, objetivo Huber y 1000 árboles con `learning_rate` 0.02: todas
dentro de ±1 % de costo de la configuración elegida, por lo que se mantuvo la más simple.

## 5. Cantidad sugerida

* **Newsvendor por familia**: el costo de `costos.csv` es lineal y asimétrico, así que la cantidad óptima es
  el cuantil τ = `costo_faltante / (costo_faltante + costo_sobrante)` de la demanda. Por eso no se sugiere la
  venta esperada: licores (τ = 0.95) y limpieza (τ = 0.91) piden de más, panadería (τ = 0.29) y frutas y
  verduras (τ = 0.33) de menos.
* **Cómo se obtiene el cuantil**: `cantidad = round(venta_esperada × m_f)`. El multiplicador `m_f` minimiza el
  costo en la ventana de calibración (fuera de muestra), lo que equivale al cuantil τ_f de `real / esperada`
  ponderado por la venta esperada. Se acota a [0.5, 3]; con menos de 30 puntos se usa el de todas las familias.
* **Alternativas**: modelos de cuantiles (uno por τ distinto: 9 niveles), distribución paramétrica
  (Poisson/normal). Se eligió la calibración empírica porque usa los costos reales, refleja los errores
  reales del modelo, es explicable por familia y se recalcula en cada reentrenamiento.
* **Efecto medido** (simulación de 28 días, `evidencia/simulacion/resumen.json`): costo 91 062 con la
  cantidad sugerida vs 122 004 si se sugiriera la venta esperada y 177 465 con la política ingenua
  “lo mismo que hace una semana”.

## 6. Ciclo de reentrenamiento y criterio de reemplazo

* **Datos**: histórico + observaciones de la base del producto (las atípicas no).
* **Ventanas** (`entrenamiento.py`): `E` = últimos 14 días, recortada para empezar después del fin de
  entrenamiento del modelo vigente (así ninguno de los dos modelos comparados la vio); `C` = 28 días antes
  de `E`. Cada candidato (baseline y LightGBM) se entrena con datos < `C` para calibrar la cantidad y con datos
  < `E` para medirse en `E`; el vigente se mide en `E` con su propia calibración.
* **Criterio explícito y automático** (`reemplazo` en `params.yaml`): el mejor candidato reemplaza al
  vigente si su **costo de negocio en `E` es al menos 1 % menor** y su **WAPE no empeora más de 5 %**
  relativo. El WAPE es una salvaguarda contra modelos que “ganan” costo sólo inflando la cantidad.
* **Evidencia mínima**: si `E` tiene menos de 5 días (el vigente se entrenó hace muy poco), o si tiene menos del
  50 % de los puntos esperados (series activas × días) o menos de 200 puntos, no se compara y se conserva el
  vigente, con ese motivo. La segunda condición se agregó tras un caso real en desarrollo: unas pocas
  observaciones sueltas de prueba en fechas futuras crearon una ventana `E` con 25 puntos y se promovió un
  baseline con métricas sin sentido (WAPE 2.2). Contar días no basta; hay que contar datos.
* **Si se promueve**, el candidato se re-entrena con todos los datos (≤ fin) y se calibra con sus
  predicciones fuera de muestra de `C` ∪ `E`. Se registra en MLflow y **el alias `champion` se mueve al
  final**: cualquier falla antes (incluida MLflow caído) deja intacto al vigente y la ejecución queda
  `fallido`.
* **Primera vez**: sin vigente, el mejor candidato se promueve directamente (motivo “Primer modelo”).
* **Alternativas descartadas**: promover siempre el último (riesgoso ante datos malos); comparar métricas de
  ventanas distintas (injusto); usar sólo WAPE (no es lo que paga el negocio).

## 7. MLflow: experimentos, linaje y alias

* Experimento `novamercado-pronostico`: un *run* por ciclo (`arranque-…` o `airflow-…`) con parámetros
  (ventanas, fin de datos, número de observaciones), métricas del candidato y del vigente, tags de
  `origen`, `ejecucion` (id del reentrenamiento), `decision` y `version_registrada`, y como artefactos el
  resumen del ciclo; cada candidato es un *run* anidado con sus métricas y multiplicadores.
* Modelo registrado `novamercado-ventas`: cada versión apunta al *run* que la produjo (linaje), con
  descripción y tags (fin de entrenamiento, origen). Artefactos: `modelo.joblib`, `meta.json`,
  importancia de features. **El vigente es el alias `champion`**.
* Sólo se registran versiones promovidas; los candidatos rechazados quedan como *runs* (con su motivo).

## 8. El servicio en operación

* **Recarga sin reinicio**: un hilo de la API consulta el alias cada 20 s y, si cambió, descarga e
  intercambia el modelo de forma atómica. Además, el DAG llama `POST /admin/recargar` al promover, por lo
  que en la práctica el cambio es inmediato (máximo garantizado: 20 s + descarga, muy por debajo de 2 min).
* **MLflow caído**: la API sigue con el modelo en memoria; `/health` responde `degradado` con el motivo
  (chequeo en vivo con timeout de 2 s y caché de 5 s). Cada versión cargada se guarda en un volumen
  (`api-cache`), así la API puede **arrancar** aunque MLflow no esté. Medido: degradado en 8 s, `ok` 6 s
  después de que MLflow vuelve (`evidencia/resiliencia.json`).
* **`/health`**: `ok` sólo si base de datos, MLflow, Airflow y modelo están bien; `degradado` + `motivo` si el
  servicio responde pero falla alguna dependencia; 503 mientras no hay modelo.
* **Reentrenamiento por API**: `POST /admin/reentrenar` registra la ejecución en la base y dispara el DAG con
  la API REST de Airflow (`dag_run_id` = `id` devuelto); 503 si Airflow no puede iniciarlo. El estado se lee
  de la tabla `reentrenamientos` (la escribe el DAG); si la ejecución falló en Airflow sin alcanzar a
  escribir, la API lo detecta consultando Airflow y la marca `fallido`.
* **Persistencia**: volúmenes para ambas bases, MLflow y la caché. El arranque compara el sha256 de los
  archivos publicados con lo cargado y no repite la carga ni el entrenamiento si ya existen.
* **Registro de la operación**: tablas `solicitudes` (incluidos los rechazos, con el *payload* original),
  `correcciones` (cada observación de calidad con su regla), `predicciones` (con `version_modelo`) y
  `observaciones` (con la solicitud que las creó o modificó por última vez).

## 9. Limitaciones conocidas

* **Ruido del criterio**: la comparación usa una sola ventana de hasta 14 días; una mejora de 1–2 % puede
  estar dentro del ruido. Se aceptó porque una promoción espuria tiene costo bajo (el nuevo modelo es igual
  de bueno) y el ciclo se repite en cada reentrenamiento. Alternativa para más rigor: varios cortes o una
  prueba pareada por *bootstrap* de series.
* **Endpoints de administración sin autenticación** (`/admin/*`): aceptable en el laboratorio porque el
  contrato no define credenciales; en producción irían detrás de un token o una red interna.
* **Más familias que las pedidas** (E05): por diseño (§2.3); la respuesta lo explica en `calidad`.

* Una familia nueva sin historia en una tienda no tiene escala: se pronostica 0 (no ocurre en los datos).
* El criterio de 1 % puede rechazar mejoras pequeñas reales; se prefirió estabilidad del vigente.
* Las ventas atípicas se detectan con el p95 del histórico publicado; un cambio de nivel extremo (> 8×)
  quedaría marcado como atípico hasta revisarse el umbral.
