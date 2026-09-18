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
2. Para conseguir casos de prueba (pólizas reales) hay dos caminos, según la
   familia de imprimible:
   - **Car_Mul / Car_Mul_V3** (multinciso): `/cases` (`list_mul_cases` en
     `cases.py`) arma la tanda leyendo `INSOR_GDS.CAR_MUL_VIEW` — no vive en
     "Insumos", tiene su propio buscador ("Buscar casos", sin "CAR_MUL_VIEW"
     ni "Car_Mul" ni "multinciso" en el texto visible del botón — se le pidió
     sacarle toda mención específica) en "Configuración de la comparación"
     porque su elegibilidad depende de esa vista puntual, no de
     producto/estado. El botón se probó moviéndolo a "Insumos" como una
     tercera pestaña, pero se revirtió (el pedido era solo cambiarle el
     nombre, no reubicarlo) — queda en su lugar original.

     Ese mismo botón "Buscar casos" tiene un segundo modo: el checkbox
     "Muestrear todos los productos" (con Familia + Alcance
     Todos/Autos/Danios, mismo criterio que `_product()` de
     `generar-imprimibles/imprimibles.py`: `insr_type` arranca con "1" =
     autos + Casos por producto) cambia la llamada de `/cases` a
     `/policy_cases_sample` (`list_products_sample`), que junta unos pocos
     casos de CADA producto del catálogo reusando `list_policy_cases`
     producto por producto (sin duplicar su lógica) — es rápido gracias al
     pool de conexión de `db.py`: medido contra SIT real, 40 casos de los 20
     productos de Car_Ind (`scope=all`, `per_product=2`) en 16s; solo daños
     (11 productos) en 9.6s. Cada caso trae un campo extra `producto` (de
     qué producto salió), mostrado en su propia columna en "Casos de
     prueba". Este control vive en "Configuración de la comparación" (no en
     "Insumos" junto al resto de la búsqueda "Por producto") porque a
     diferencia de esa búsqueda — que es solo para *conocer* pólizas, ver
     más abajo — el muestreo no tiene otro uso que alimentar la comparación:
     escribe directo en "Casos de prueba", sin pasar por "Pólizas
     encontradas" ni por el traspaso explícito.
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
   `policy_lot`, `insr_type`, `policy_state`, y `quote_id` si aplica) sin
   tocar la sección de comparación. Solo si el usuario aprieta explícitamente
   "Usar estas pólizas como casos de prueba" esa tanda pasa a ser `cases` y
   aparece en "Casos de prueba" — nunca como efecto automático de buscar.
   `/cases` (multinciso), en cambio, alimenta `cases`/"Casos de prueba"
   directo — nunca pasó por "Pólizas encontradas" ni tuvo el paso explícito
   de "Usar estas pólizas..." (es el flujo más viejo de la app, de antes de
   que existiera "Insumos"; no se tocó al revertir el intento de moverlo).

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
fila — la columna "B vs A" (diferencia porcentual, no ratio ×, para que quede
claro sin pensarlo si B fue más rápido o más lento) sigue siendo optimista
para cualquier caso donde A salió primero, pero al menos no está sesgada
sistemáticamente a favor de B.

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
  `/policy_cases_sample`, `/resolve_policy`, `/report_families`, `/run_case`,
  `/pdf/<token>`.
- `cases.py` — descubrimiento de casos (`list_mul_cases`, `list_products`,
  `list_policy_cases`, `list_products_sample`), ver arriba.
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
- `printouts.py` — `list_families()` (copiado de
  `versiones-vistas-imprimibles/printouts.py`, solo esa función): nombres de
  carpeta bajo `PRINTOUTS_DIR` con `.jrxml` propio, usados para autocompletar
  Reporte A/B (`/report_families`). El nombre de carpeta es exactamente el
  nombre de reporte que espera OIC, así que sirve tanto para catalogados
  (`Car_Mul`) como para experimentales sin desplegar (el caso de uso de esta
  app). Los campos siguen siendo `<input list="reportNames">`, no un
  selector rígido — se puede escribir cualquier nombre a mano aunque no
  tenga carpeta (y de hecho `Car_Mul_V3` hoy no aparece: su carpeta está
  vacía en disco, verificado — puede haber sido reorganizada fuera de esta
  app).
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
- **`Car_Mul_V3` con `policy_id=100000229284` (SIT) da 500.** Esa póliza tiene
  `CANT_UBICACIONES=1` en `CAR_MUL_VIEW` (single inciso) — `Car_Mul` → 200 OK,
  `Car_Mul_V3` → 500 con cuerpo vacío (OIC no expone detalle/stack trace, sin
  acceso a logs de Jasper/OIC para confirmar la causa exacta, y no se pudo
  inspeccionar el JRXML: la carpeta `Car_Mul_V3` en `PRINTOUTS_DIR` está
  vacía en disco desde hace un tiempo — ver la nota de `printouts.py` en
  "Archivos clave"). Semánticamente esto no debería pasar en producción (una
  póliza de 1 ubicación usa `Car_Ind`, no `Car_Mul`) — pero como el buscador
  "Por identificador" no filtra por `CANT_UBICACIONES` (a diferencia de "Por
  producto"/`list_mul_cases`, que sí exige `> 1`), es fácil colar sin querer
  un caso así si se resuelve una póliza puntual y se manda a comparar.
  Descubierto de casualidad probando esta misma póliza para el caso de abajo
  (`Car_Ind_V2`) — no es necesariamente el mismo tipo de bug, solo quedó
  registrado.
- **`Car_Ind_V2` con esa misma póliza (`100000229284`) en STST da 500, pero
  en SIT funciona bien e idéntico a `Car_Ind`.** Diagnosticado con esta app:
  - No es un problema de despliegue de `Car_Ind_V2` en STST en general —
    otras pólizas (`100000231124`, `100000229616`, y otras con
    `policy_state=12` como `100000006755`/`100000006816`) andan bien ahí.
  - No es por `policy_state` (en SIT esa póliza tiene `policy_state=0`, en
    STST `policy_state=12` — pero otras pólizas `state=12` en STST funcionan).
  - Es específico de esta póliza en STST. Revisados `policy_annex` (un solo
    annex 0), `claim` (ninguno) y `policy_eng_policies` (una fila,
    `eng_pol_type='POLICY'`, sin `quote_id`) sin encontrar nada anómalo a
    simple vista.
  - Reproducido de forma consistente (2 reintentos, mismo 500 las dos veces
    — no es transitorio de OIC).
  - **Causa raíz encontrada** (el usuario pasó la traza de Jasper Studio):
    `ClassCastException: Cannot cast java.math.BigDecimal to java.lang.String`
    evaluando `$P{id_poliza}` dentro de un `<jr:list>` (`FillDatasetRun` /
    `BaseFillList.evaluate` en la traza). En
    `INSOR/shared_workspace/PRINTOUTS/FASE_1/Car_Ind_V2/Car_Ind_Autos_v2.jrxml:1641`
    (el `<jr:list>` del subDataset `special_conditions`, sección "CONDICIONES
    ESPECIALES"):
    ```xml
    <datasetParameter name="id_poliza">
      <datasetParameterExpression><![CDATA[$F{POLICY_ID}]]></datasetParameterExpression>
    </datasetParameter>
    ```
    `$F{POLICY_ID}` es `BigDecimal` (columna NUMBER), pero el subreport
    declara `id_poliza` como `class="java.lang.String"` (línea 75, usado en
    `SPECIAL_CONDITIONS_VIEW.POLICY_ID = $P{id_poliza}`) — falta un
    `String.valueOf($F{POLICY_ID})`. Un `<jr:list>` sin filas no evalúa sus
    `datasetParameter`, por eso solo explota con pólizas que tienen ≥1
    condición especial (de ahí que la mayoría de las pólizas de prueba no lo
    disparen). El mismo patrón (`$F{POLICY_ID}` crudo sin `String.valueOf`)
    aparece también en las líneas 1338 y 1439 (subDatasets `coberturas_inciso`
    y `servicios_asistencia_inciso`) — **revisado y descartado como riesgo
    real**: esos dos van dentro de `<jr:table>`, no `<jr:list>` (la traza es
    específica del paquete `net.sf.jasperreports.components.list`), y
    `<jr:table>` evalúa distinto. Se probó `policy_id=100000202489` en STST
    (con filas reales en ambas vistas — `COVERED_RISKS_AUTOS_VIEW` con y sin
    `RIESGOS_CUBIERTOS LIKE '%ASISTENCIA%'`, encontrada con un `GROUP BY`
    sobre esa vista sin filtro por póliza, que puede tardar): `Car_Ind_V2`
    respondió 200 OK, idéntico en tamaño a `Car_Ind`. El mismo patrón de
    código (`$F{POLICY_ID}` crudo) sigue siendo un descuido, pero no crashea
    en la práctica — solo el `<jr:list>` de `special_conditions` lo hace.
