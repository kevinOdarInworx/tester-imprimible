# tester-imprimible

App web (Flask, un solo usuario, `python app.py` o `run.bat` en
`127.0.0.1:5003`) para demostrar que dos versiones de un mismo imprimible
Jasper (el caso concreto: `Car_Mul` vs `Car_Mul_V3`, ver
`INSOR/shared_workspace/PRINTOUTS/FASE_1/Car_Mul_V3/README.md`) producen el
mismo contenido para pólizas reales, aunque una sea mucho más rápida.

## Flujo

1. El usuario elige ambiente (default SIT/PREPROD — ahí está desplegado
   `Car_Mul_V3` hoy; en PROD todavía no, ver "Limitaciones conocidas") y los
   nombres de los dos reportes a comparar (default `Car_Mul` / `Car_Mul_V3`)
   y cuántos casos quiere probar.
2. `/cases` (`cases.py`) arma la tanda de casos: candidatos de `POLICY_ID`
   salen de `insis_gen_v10.policy` (tabla base, filtro barato por
   `policy_state`/`insr_type`), y **solo** `INSOR_GDS.CAR_MUL_VIEW` decide si
   un candidato es un caso valido — nunca se corre nada distinto de esa
   vista para determinar elegibilidad, a pedido explícito.
3. Por cada caso seleccionado, `/run_case` llama al flujo OIC JASPER_INSOR
   (`reports.py`, mismo mecanismo que `generar-imprimibles/config/
   imprimibles.py` pero sin catálogo fijo de reportes — acá el nombre es
   libre porque se prueban reportes que todavía no están catalogados) para
   los dos reportes con los mismos `string_param1/2` (`POLICY_ID`,
   `ANNEX_ID`), mide cuánto tarda cada uno, y compara el texto extraído de
   ambos PDFs (`compare.py`, pdfplumber + difflib) para mostrar si son
   idénticos y, si no, un diff lado a lado.

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

- `app.py` — rutas Flask: `/`, `/cases`, `/run_case`, `/pdf/<token>`.
- `cases.py` — descubrimiento de casos (`list_cases`), ver arriba.
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
- `templates/index.html` — frontend único, reutiliza los tokens de diseño
  (colores, pills, tabla de diff) de `versiones-vistas-imprimibles`.

## Convenciones

- Comentarios y docstrings en español, estilo conciso.
- Nunca tocar bases INSIS, solo RAWDB (INSOR).
- Secretos en `.env` (no se commitea); ver `.env.example`.
- No hay tests ni build step; para probar cambios correr `python app.py` (o
  `run.bat`) contra un ambiente real.

## Limitaciones conocidas

- Solo sabe armar casos para la familia `Car_Mul` (vía `CAR_MUL_VIEW`). Para
  testear otro par de imprimibles (p.ej. `Cot_Mul` vs `Cot_Mul_V3`) hay que
  adaptar `cases.py` a la vista/parámetros de esa familia — `reports.py` y
  `compare.py` ya son genéricos.
- `Car_Mul_V3` NO está desplegado en PROD todavía (probado: OIC devuelve
  500). Sí está desplegado y responde bien en SIT/PREPROD (probado: PDF
  idéntico al de `Car_Mul` para una póliza real), que por eso es el default.
  Si `/run_case` devuelve error en el lado B en otro ambiente, probablemente
  sea por esto.
