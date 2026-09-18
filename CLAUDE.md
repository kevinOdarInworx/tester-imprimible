# tester-imprimible

App web (Flask, un solo usuario, `python app.py` o `run.bat` en
`127.0.0.1:5003`) para demostrar que dos versiones de un mismo imprimible
Jasper (el caso concreto que la motivó: `Car_Mul` vs `Car_Mul_V3`, ver
`INSOR/shared_workspace/PRINTOUTS/FASE_1/Car_Mul_V3/README.md`) producen el
mismo contenido para pólizas reales, aunque una sea mucho más rápida.

## Flujo

1. El usuario elige ambiente (sin default — el selector arranca sin nada
   elegido a propósito, para no operar por error contra un ambiente que no
   se pensó explícitamente; `Car_Mul_V3` hoy solo está desplegado en
   SIT/PREPROD, ver "Limitaciones conocidas"), arriba
   de todo, porque lo usan tanto la búsqueda de insumos como la ejecución.
2. Para conseguir casos de prueba (pólizas reales), la sección "Insumos"
   tiene dos pestañas independientes (más el camino de `Car_Mul`):
   - **Car_Mul / Car_Mul_V3** (multinciso): `/cases` (`list_mul_cases` en
     `cases.py`) arma la tanda leyendo `INSOR_GDS.CAR_MUL_VIEW` — no vive en
     "Insumos", tiene su propio buscador en "Configuración de la comparación"
     porque su elegibilidad depende de esa vista, no de producto/estado.
   - **"Por producto"** (Car_Ind/Cot_Ind u otras familias "individuales"):
     elegís Familia + Producto + Subtipo y llama a `/policy_cases`
     (`list_policy_cases`), que filtra `insis_gen_v10.policy` por
     `policy_state` (según la familia) y `insr_type` (el `product_code`
     elegido). `/products` (`list_products`) trae el catálogo
     producto/subtipo → `product_code` desde `cfg_nl_product`/
     `cfg_nl_product_text` (la query se la pasó el usuario directamente — es
     la que usa el equipo para resolver nombres de producto; ver detalle en
     `cases.py`).
   - **"Por identificador"**: pegás un `policy_id`/`policy_no`/`policy_lot`/
     `engagement_id`/`quote_id` y llama a `/resolve_policy` (`resolver.py`),
     que es el mismo buscador de `generar-imprimibles` (`policy.py` +
     `resolve_engagement`/`resolve_quote` de `imprimibles.py`) copiado sin el
     motor de elegibilidad de imprimibles — acá solo interesa identificar la
     póliza, no calcular qué reportes ofrecerle. Si el valor resuelve a un
     `engagement_id` (autos multinciso), `list_engagement_policies` trae
     **todas** las pólizas dependientes de ese engagement (no solo las
     MASTER en estado 0 que usa `_masters` en `imprimibles.py` para la
     caratula — acá interesa listar lo que hay, no decidir elegibilidad de
     un reporte puntual), con su `eng_pol_type` (`MASTER`/`DEPENDENT`,
     confirmado contra un engagement real en SIT).

   Ambas pestañas de "Insumos" alimentan la misma tabla "Pólizas encontradas"
   y son **semánticamente independientes de la comparación**, aunque
   internamente reusen la misma tanda de datos para no duplicar la consulta:
   buscan pólizas para *conocerlas* (con `policy_id`, `policy_no`,
   `policy_lot`, `insr_type`, `policy_state`, y `quote_id` si aplica), sin
   tocar la sección de comparación. Solo si el usuario aprieta explícitamente
   "Usar estas pólizas como casos de prueba" esa tanda pasa a ser `cases` y
   aparece en "Casos de prueba" — nunca como efecto automático de buscar.

   Cualquiera sea el origen, cada caso trae un campo `params` ya armado con
   la forma exacta que espera OIC para esa familia (`[policy_id, annex_id]`
   para Car_Mul/Car_Ind, `[quote_id]` para Cot_Ind) — el frontend nunca arma
   params a mano, solo reenvía `case.params`.
3. Por cada caso seleccionado, `/run_case` llama al flujo OIC JASPER_INSOR
   (`reports.py`, mismo mecanismo que `generar-imprimibles/config/
   imprimibles.py` pero sin catálogo fijo de reportes — acá el nombre es
   libre porque se prueban reportes que todavía no están catalogados) para
   los dos reportes con los mismos parámetros, mide cuánto tarda cada uno, y
   compara el texto extraído de ambos PDFs (`compare.py`, pdfplumber +
   difflib) para mostrar si son idénticos y, si no, un diff lado a lado.

## Por qué se alterna quién se pide primero

El reporte que se pide primero dentro de un mismo caso deja tibio el buffer
cache de Oracle (misma póliza, mismos bloques) para el segundo, que entonces
sale artificialmente más rápido — se confirmó en la práctica: la misma
consulta a `Car_Mul_V3` tardó 34.6s aislada (Postman) contra ~10s corrida en
la app justo después de `Car_Mul` sobre la misma póliza. Por eso el frontend
alterna `swap_order` caso por caso (par/impar) y el backend arma el orden de
llamada en base a ese flag; `/run_case` devuelve `order` (p.ej. `["b","a"]`)
para que la columna "Orden" en la tabla muestre qué se pidió primero en cada
fila — la columna "Mejora" sigue siendo optimista para cualquier caso donde A
salió primero, pero al menos no está sesgada sistemáticamente a favor de B.

## Por qué CAR_MUL_VIEW nunca se consulta sin `WHERE POLICY_ID`

Según el README de `Car_Mul_V3`, `CAR_MUL_VIEW` "no tiene filtro propio": el
costo caro de la versión original (`LISTAGG` sobre toda `AGENTS_VIEW`, ~19-30s)
no depende del tamaño de la póliza consultada, es un costo fijo de golpear la
vista. Por eso `cases.py` jamás hace `SELECT ... FROM CAR_MUL_VIEW` sin
`WHERE POLICY_ID = :p` (sería un escaneo carísimo/riesgoso en PROD): siempre
filtra por un candidato concreto, uno por uno, hasta juntar los casos
pedidos (tope duro `_MAX_PROBES = 60` candidatos probados). Como cada golpe a
la vista puede tardar varios segundos, el default de casos es chico (5).

## Archivos clave

- `app.py` — rutas Flask: `/`, `/cases`, `/products`, `/policy_cases`,
  `/resolve_policy`, `/run_case`, `/pdf/<token>`.
- `cases.py` — descubrimiento de casos (`list_mul_cases`, `list_products`,
  `list_policy_cases`), ver arriba.
- `resolver.py` — buscador por identificador (`resolve`, con `lookup_policy`
  + `resolve_engagement`/`resolve_quote`), copiado de
  `generar-imprimibles/policy.py` + las dos funciones homónimas de
  `imprimibles.py` (sin el resto del motor de elegibilidad). Ojo: a
  diferencia del original, acá `_serialize` sí convierte `Decimal` a
  `int`/`float` — el original de `generar-imprimibles` no lo hace, así que
  si esa app alguna vez devuelve `Decimal` desde Oracle para estas columnas,
  tiene el mismo bug latente de `jsonify` (no se tocó ese proyecto, pero
  vale la pena avisar si se retoca).
- `reports.py` — llamada al flujo OIC JASPER_INSOR (`fetch_report`), nombre
  de reporte libre (sin catálogo).
- `compare.py` — extrae texto de cada PDF (pdfplumber) y arma el diff
  (difflib.HtmlDiff), normalizando espacios como en
  `versiones-vistas-imprimibles/views.py` (incluidas líneas en blanco, que
  pdfplumber no siempre reproduce igual entre renders).
- `db.py` / `config/environments.py` — túnel SSH + conexión Oracle, copiado
  de `generar-imprimibles` con `LOCAL_PORT` propios (15221-15225) para poder
  correr las tres apps a la vez. No trae copia propia de la clave SSH ni del
  Instant Client: `.env` apunta con rutas relativas a los de
  `generar-imprimibles` (`SSH_KEY_PATH`, `ORACLE_CLIENT_LIB`).

  **A diferencia del original de `generar-imprimibles`**, acá `get_connection`
  también cachea y reusa la conexión Oracle en sí (no solo el túnel SSH) por
  ambiente, para no pagar el handshake de Native Network Encryption en cada
  pedido HTTP — medido: ~4s la primera vez, ~0.3s las siguientes. Devuelve un
  `_PooledConnection` cuyo `__exit__` NO cierra la conexión real (a
  diferencia de una `oracledb.Connection` normal) — todo el código llamador
  sigue escribiendo `with get_connection(env) as conn:` exactamente igual,
  no hace falta que lo sepa. Antes de reusarla hace `conn.ping()`; si está
  rota (idle timeout, corte de red) la descarta y reconecta sola. El cierre
  real solo pasa si `ping()` falla o al terminar el proceso (`_close_all`).
  Si se vuelve a copiar este `db.py` a otra app hermana, decidir a
  propósito si también quiere este pooling o el original sin cachear.
- `templates/index.html` — frontend único, reutiliza los tokens de diseño
  (colores, pills, tabla de diff) de `versiones-vistas-imprimibles`.

  **Única dependencia externa de toda la app**: en la pestaña "Por
  identificador" se puede pegar (Ctrl+V), arrastrar o elegir una imagen
  (p.ej. una captura de un imprimible ya generado) para extraerle el
  identificador de póliza por OCR. Carga Tesseract.js perezosamente desde
  `cdn.jsdelivr.net` recién al primer uso (nunca al cargar la página) — a
  diferencia de todo lo demás en esta app y sus hermanas, esto **requiere
  internet** (sin conexión, el dropzone tira el error "No se pudo cargar
  Tesseract.js"). El texto reconocido se busca con dos regex, en orden de
  prioridad: `\d+/\d+/\d+` (formato policy_no/policy_lot, más específico)
  y si no aparece, una tira de 9+ dígitos seguidos (policy_id). El match se
  precarga en el campo de identificador pero **no dispara la búsqueda
  sola** — el usuario revisa y aprieta "Resolver póliza" a propósito, igual
  que el resto de "Insumos". No se pudo probar el OCR de punta a punta en
  esta sesión (necesita un navegador real con canvas/clipboard, no
  disponible en las pruebas por Bash) — sí se verificó que la página
  renderiza y que el cableado JS/CSS está completo.

## Convenciones

- Comentarios y docstrings en español, estilo conciso.
- Nunca tocar bases INSIS, solo RAWDB (INSOR).
- Secretos en `.env` (no se commitea); ver `.env.example`.
- No hay tests ni build step; para probar cambios correr `python app.py` (o
  `run.bat`) contra un ambiente real.

## Limitaciones conocidas

- Familias soportadas para conseguir casos: `Car_Mul` (vía `CAR_MUL_VIEW`) y
  `Car_Ind`/`Cot_Ind` (vía `insis_gen_v10.policy` + catálogo de producto).
  Para otra familia (p.ej. `Cot_Mul`, `End_Ind`) hay que agregar su regla en
  `cases.py` — `reports.py` y `compare.py` ya son genéricos, no hace falta
  tocarlos.
- El filtro de `policy_state` por familia (`FAMILIES` en `cases.py`) lo dio
  el usuario de memoria (`Car_Ind`: `policy_state >= 1`; `Cot_Ind`:
  `policy_state = -4`, igual al `cotizacion = estado == -4` que ya usa
  `generar-imprimibles/imprimibles.py`) — no está re-derivado de ningún otro
  doc, así que si en la práctica aparecen falsos negativos/positivos, el
  primer sospechoso es ese filtro.
- El par (Producto, Subtipo) en el buscador de insumos resuelve un
  `product_code` (=`policy.INSR_TYPE`) exacto contra el catálogo
  `cfg_nl_product`/`cfg_nl_product_text`/`hst_object_type`. Un mismo
  `product_text` puede tener más de una fila de `subtipo` — en la práctica
  (verificado contra SIT real), lo común es que TODAS esas filas compartan
  el mismo `product_code` (p.ej. "RC Obligatoria" tiene ~10 subtipos, los 10
  con código 1108; "Fronterizos y legalizados" tiene 2, ambos 1035), no que
  cada subtipo traiga un código distinto. El selector de Subtipo se habilita
  cuando hay más de una fila (no más de un código); si hay una sola, se usa
  directo sin mostrar el selector.
  La interpretación de qué es "subtipo" (a qué objeto de negocio —
  póliza/cotización/endoso — está ligada esa configuración de producto) es
  una lectura del research, no algo que el usuario confirmó explícitamente;
  si el subtipo mostrado no tiene sentido para algún producto, avisar.
- **Bug ya corregido, ojo si se retoca el dropdown de Subtipo**: como varias
  filas de subtipo suelen compartir el mismo `product_code`, usar ese código
  como `value` de cada `<div class="dropdown-item">` los dejaba con el mismo
  `data-value` — al clickear, `items.find(x => x.value === el.dataset.value)`
  siempre devolvía la PRIMERA fila que matcheaba ese código, sin importar
  cuál subtipo se clickeara (el usuario lo vio como "no me deja elegir el de
  más abajo"). El fix (`fillSubtipos` en `templates/index.html`) usa el
  índice de la fila dentro de `filas` como `value`, que siempre es único, y
  resuelve el `product_code` real recién en el handler de `change` indexando
  `filas[idx]`.
- `Car_Mul_V3` NO está desplegado en PROD todavía (probado: OIC devuelve
  500). Sí está desplegado y responde bien en SIT/PREPROD (probado: PDF
  idéntico al de `Car_Mul` para una póliza real), que por eso es el default.
  Si `/run_case` devuelve error en el lado B en otro ambiente, probablemente
  sea por esto.
