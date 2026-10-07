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
   Debajo, la página se divide en tres pestañas: "Buscar pólizas" (Insumos,
   Pólizas encontradas, Endosos), "Comparador" (Configuración de la
   comparación, Casos de prueba) y "Descargar documentos" (ver más abajo).
   Cambiar de pestaña solo oculta/muestra: una comparación o descarga en
   curso sigue corriendo.
2. Para conseguir casos de prueba (pólizas reales) hay dos caminos, según la
   familia de imprimible:
   - **"Buscar casos"** (pestaña "Comparador"): con Familia + Alcance
     (Todos/Autos/Danios, mismo criterio que `_product()` de
     `generar-imprimibles/imprimibles.py`: `insr_type` arranca con "1" =
     autos) + Casos por producto, llama a `/policy_cases_sample`
     (`list_products_sample`), que junta unos pocos casos de CADA producto
     del catálogo reusando `list_policy_cases` producto por producto (sin
     duplicar su lógica) — es rápido gracias al pool de conexión de `db.py`:
     medido contra SIT real, 40 casos de los 20 productos de Car_Ind
     (`scope=all`, `per_product=2`) en 16s; solo daños (11 productos) en
     9.6s. Cada caso trae un campo extra `producto` (de qué producto salió),
     mostrado en su propia columna en "Casos de prueba". Escribe directo en
     "Casos de prueba", sin pasar por "Pólizas encontradas".

     Antes "Buscar casos" tenía un modo por defecto que leía
     `INSOR_GDS.CAR_MUL_VIEW` (`/cases` → `list_mul_cases` en `cases.py`,
     casos multinciso de Car_Mul, filtrando `cant_ubicaciones > 1`) y el
     muestreo era un checkbox opcional con su propio campo "Casos". A pedido
     del usuario se sacaron el checkbox y ese campo: el muestreo es ahora la
     única forma de "Buscar casos". La ruta `/cases` y `list_mul_cases`
     siguen en el backend pero la pantalla ya no las llama; para probar
     Car_Mul con pólizas multinciso, buscarlas en "Buscar pólizas" y usar
     "Usar estas pólizas en el comparador".
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

     Después de resolver, el frontend encadena `/policy_annexes`
     (`list_annexes` en `resolver.py`) con los `policy_id` encontrados y
     muestra la tarjeta "Endosos" (annex_id, annex_no, tipo endoso,
     tipificación) — igual que la carátula de `generar-imprimibles`, que
     ofrece una versión por endoso además de la emisión. Sale de
     `insor_gds.annex_details_view` y no de `CONSUTA_DE_ENDOSOS_VIEW` (la que
     usa `_endosos` en generar-imprimibles): esa es un subset de esta sin
     `NAME` (tipificación, de HT_ANNEX_REASON). Se probó mostrando también
     `TEXTO_ENDOSO` ("qué endoso es"), pero se sacó a pedido del usuario. Como
     la vista, excluye endosos con `annex_state` -3/-30. Es solo informativo
     (no arma casos con ese annex_id) y solo se
     dispara desde "Por identificador": en "Por producto" serían N consultas
     extra por un listado que ahí no se pidió.

   Ambas pestañas de "Insumos" alimentan la misma tabla "Pólizas encontradas"
   y son **independientes de la comparación**: buscan pólizas para
   *conocerlas* (con `policy_id`, `policy_no`, `policy_lot`, `insr_type`,
   `policy_state`, y `quote_id` si aplica) y no tocan "Casos de prueba" por
   sí solas. Solo el botón "Usar estas pólizas en el comparador" (al final de
   los resultados, visible únicamente después de una búsqueda con
   resultados) pasa esa tanda a `cases` y cambia a la pestaña "Comparador".
   Se deshabilita mientras corre una comparación o un "Buscar casos": con
   las pestañas es fácil apretarlo desde "Buscar pólizas" en medio de una
   corrida, y reemplazar `cases` ahí haría que el loop de `ejecutar()` lea
   params de la tanda nueva para las filas viejas. La otra fuente de casos es
   "Buscar casos" en "Configuración de la comparación"
   (`/policy_cases_sample`). El botón se sacó una vez y se volvió a poner a
   pedido del usuario.

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

"B vs A en promedio" (`renderStats` en `index.html`) solo promedia los casos
con diferencia real (`Math.abs(pct) >= FLAT_THRESHOLD`, 5%, el mismo umbral
que decide si una fila se muestra como "≈ igual"). Antes promediaba todos los
casos idénticos por igual, y como en una tanda típica varios casos caen en
ruido de medición (0-3%, no una mejora ni regresión real), ese promedio
mezclado quedaba diluido hacia 0 aunque los casos con diferencia real
mostraran mejoras bastante más grandes (caso real: mezclado daba 8%, solo
sobre los casos con diferencia real daba ~10-11%). Los casos "≈ igual"
excluidos del promedio se cuentan aparte en el badge "Sin diferencia real"
para que no desaparezcan silenciosamente.

## Por qué CAR_MUL_VIEW nunca se consulta sin `WHERE POLICY_ID`

(Aplica a `/cases`/`list_mul_cases`, que siguen en el backend aunque la
pantalla ya no los llama — ver "Flujo".)

Según el README de `Car_Mul_V3`, `CAR_MUL_VIEW` "no tiene filtro propio": el
costo caro de la versión original (`LISTAGG` sobre toda `AGENTS_VIEW`, ~19-30s)
no depende del tamaño de la póliza consultada, es un costo fijo de golpear la
vista. Por eso `cases.py` jamás hace `SELECT ... FROM CAR_MUL_VIEW` sin
`WHERE POLICY_ID = :p` (sería un escaneo carísimo/riesgoso en PROD): siempre
filtra por un candidato concreto, uno por uno, hasta juntar los casos
pedidos (tope duro `_MAX_PROBES = 60` candidatos probados). Como cada golpe a
la vista puede tardar varios segundos, el default de casos es chico (5).

## Pestaña "Descargar documentos"

Port de `generar-imprimibles` (`/resolve` + `compute_imprimibles` /
`compute_for_engagement`, reglas en `generar-imprimibles/docs/imprimibles.md`)
en `documentos.py`: se ingresa un identificador, se listan los imprimibles
que corresponden a la póliza agrupados (Carátula, Cotización, Endosos,
Recibos, Siniestros) y cada uno se descarga vía OIC. Rutas: `/doc_resolve`
(resuelve y, si queda una sola póliza o es un engagement, calcula los
documentos), `/doc_for_policy` (cuando hubo varias pólizas y se elige una) y
`/doc_download` (llama a OIC, guarda el PDF en `_pdf_store` y devuelve token,
páginas, tiempo y si vino en blanco). Las descargas usan el ambiente con el que
se calculó la lista (`docEnv`), no el que tenga el selector después.

Diferencias con generar-imprimibles, a partir de lo aprendido (skills
`imprimibles-oic`/`sql-gds` y memorias):

- **PDF en blanco:** si el PDF no tiene ninguna línea de texto fuera del
  número de página (`pdf_stats` en `compare.py`: Jasper imprime "Pág. 1 de 1"
  en el pie aunque la vista no devuelva filas — así sale la carátula
  `Car_Ind` de un endoso cuyo motivo no cubre `CAR_IND_DANIOS_VIEW`: desde el
  despliegue del 06/10 en PROD solo arma filas de endoso para las razones
  CHNGAGENT/8, 11, 62, 15, 34 y 2; con 55 aumento de suma asegurada o 38
  endoso B libre sale en blanco), se avisa con la causa probable
  (`blank_hint`) y **no se descarga solo**. OIC responde 200 aunque la vista
  no devuelva filas (parámetro que no existe en el ambiente, o lag de réplica
  INSIS→RAWDB de ~3-10 s en pólizas recién emitidas).
- **HTTP 500 sin cuerpo:** se explica que es reporte no desplegado en el
  ambiente o `.jrxml` que explota con esos datos (OIC no da la traza).
- **Recibos pagados:** `_receipts` calcula `en_blanco` con el mismo filtro
  que `CALCULUS_V5_VIEW` (versión neteada de `Rec_Pag`, en STST y PROD): una
  transacción entra si no está pagada (`paid_status` N/P) **o** si es
  negativa con saldo 0 y razón de endoso ≠ 11 (cambio de forma de pago). Si
  ninguna entra, el ítem avisa que sale en blanco. Antes se miraba solo
  `paid_status = 'Y'` y las notas de crédito pagadas (PREMIUM-244217 y
  245004 en PROD, póliza 0000201107/0072610/00) se avisaban en blanco aunque
  se generan bien. Validado generando los PDFs: 6/6 recibos de esa póliza en
  PROD y PREMIUM-214305 en STST coinciden. En UAT PREMIUM-244183 (cargo
  pagado) sale con datos aunque la regla dice en blanco: ahí el aviso puede
  fallar (no investigado).
- **Recibos, versión:** `blc_transactions.annex` llega como texto (`"0"`),
  así que se convierte a int antes de compararlo. En generar-imprimibles no
  se convierte y por eso ahí los recibos nunca dicen "Emisión" ni el nombre
  del endoso (bug latente allá, no corregido).
- **Recibos por endoso:** se listan agrupados por versión: "Endoso 0
  (Emisión)" y después cada endoso en orden (`B-105326 · Endoso B`). Una
  versión sin recibo igual aparece, como fila punteada "Sin recibo" sin botón
  (`placeholder: True`, no cuenta en "N documento(s)"): p.ej. los endosos B
  no mueven prima y no generan recibo (01/951/144287 en PROD: un recibo de
  emisión + dos filas vacías por sus dos endosos B). Recibos de un annex que
  no está en `annex_details_view` (endoso excluido por `annex_state`) van al
  final con su `annex_id`. Si el `annex_no` viene sin número (`C-`, visto en
  PROD en varias cancelaciones por falta de pago de la misma póliza,
  100000143673) se le agrega el `annex_id` para poder distinguirlas.
- **Endosos:** salen de `list_annexes` (`annex_details_view`, con
  tipificación), igual que la tarjeta "Endosos" de "Buscar pólizas"; se
  descarta `annex_id = 0` para no duplicar la emisión.
- **Siniestros:** se ofrece `SIN_04` descargable porque su `.jrxml` solo
  pide `claim_id` (`string_param1`); el resto de los `SIN_0X` pide
  `string_param2/3` sin mapear. generar-imprimibles los listaba todos como
  "no disponible".
- Cada ítem muestra el reporte y los parámetros OIC que va a mandar.

No se agregaron `Car_Seg_Oblig`, `Car_RC_USA` ni `Car_Benef`: no hay una
regla conocida de a qué pólizas corresponden.

### "En INSIS": con qué parámetros generó INSIS cada imprimible

Debajo de cada documento se muestra la **última llamada de INSIS a OIC** para
ese imprimible (`_attach_insis` en `documentos.py`), leída de
`insis_gen_v10.doc_documents` + `insis_cust_addon.cust_doc_printout_ctrl`
(`payload_json`), y se comparan sus `reportParams` (no los `storageParams`,
que solo dicen dónde se guarda) con los parámetros del botón:

- **Sin reportParams** → Jasper genera sin filtro y lo que quedó en
  Laserfiche está en blanco, aunque `result_state` sea OK y la pantalla diga
  "Documento listo en Laserfiche". Caso que lo motivó: End_Ind
  100000224652 / 3000104198 en PROD (14 llamadas, ninguna con reportParams;
  el Car_Ind de la misma póliza sí los trae). En PROD, últimos 45 días: 8
  pólizas con End_Ind así, además de Car_Mul_Autos* (bug 326989) y algunos
  Rec_Pag de endoso.
- **Parámetros distintos**, **Error** (`result_state` ≠ OK, con
  `result_details`), **No llamado** (`doc_state` 1, el NO DISPONIBLE de
  INSIS: INSIS ni siquiera llamó a OIC, no hay filas en
  `cust_doc_printout_ctrl`) o **Sin registro**. "Parámetros OK" solo dice que INSIS pidió lo mismo que el botón:
  si el botón sale en blanco, lo de INSIS también.
- `reportParams` viene como lista o como un solo objeto (Cot_*,
  Car_Mul_Autos) y puede traer `null`.
- Cómo se ubica cada imprimible en INSIS (`_insis_key`): Car_Ind/Car_Mul/
  End_Ind por `policy_id` + `annex_id`; Car_Mul_Autos_Maestra por la maestra;
  Car_Mul_Autos por cualquier MASTER del engagement (en cualquier estado);
  Cot_* por la póliza −4; Rec_Pag por `policy_id` + annex del recibo,
  prefiriendo la llamada cuyo `string_param1` es ese doc_number (un endoso
  puede tener varios recibos). `doc_id` viene truncado (`Car_Mul_Au`,
  `Car_Ma_MA`, `Rec_Pag_Ct`…) y se mapea con `_DOC_ID_REPORT`. SIN_04 no lo
  genera INSIS y no se muestra.
- "ver llamada" muestra el `payload_json` crudo de esa llamada.

## Pestaña "Comparar vistas"

Comparador de vistas extraído de `versiones-vistas-imprimibles` (pestaña
"Comparar vistas"), como matriz vista × fuente. Nació como "Vistas del
pase" (lista fija en `pase.py`); a pedido del usuario quedó **general** y el
pase pasó a ser un release más de la pestaña "Releases".

- **Qué vistas (`vSet`):** "Todas las vistas del repo" (default, ~80, tarda
  ~10 s en cargar; arranca con el filtro "Solo las que difieren" marcado) o
  las de un release (`/releases/<fecha>` → `vistas`), más las que se agregan
  a mano (`vs.extra`, sobreviven al cambio de conjunto). Con "todas", la
  columna "Releases" muestra en qué releases se tocó cada vista; con un
  release, sus ítems (borde punteado = no figuraba en el correo).
- **Filtros:** búsqueda por nombre y "Solo las que difieren" (`vEstadoFila`:
  distinta si hay más de un md5 entre las fuentes marcadas o existe en unas
  y no en otras; las fuentes con error no cuentan; la vista elegida en el
  diff nunca se oculta).

- **Fuentes:** el repo INSOR ("Repo") y los ambientes. Por defecto Repo,
  UAT, STST y PROD; DEV y SIT están disponibles pero sin marcar (suelen
  estar caídos). La selección se recuerda en `localStorage`. El frontend
  pide cada fuente por separado y en paralelo (`/vistas/fuente`), así un
  ambiente caído solo deja su columna en "Sin conexión" sin frenar al
  resto; para eso `db.py` pasó a usar un candado por ambiente (antes uno
  global retenía a todos durante el timeout de connect del caído).
- **Agrupación:** en cada fila, la misma letra es el mismo `md5` de
  `sqltext.comparable_md5` (sin comentarios, espacios ni líneas vacías),
  igual que en `versiones-vistas-imprimibles`. La letra A es de la primera
  columna que trae versión. Debajo va `LAST_DDL_TIME` del ambiente o la
  fecha del último commit del archivo en el repo.
- **"Estado":** compara PROD contra el repo, que es lo que se instala (a
  pedido del usuario el 06/10: había pisado PROD con el repo y seguía
  saliendo "Falta en PROD" porque se comparaba con STST). Si el repo no está
  tildado o la vista no tiene archivo, compara contra STST. Muestra "Falta en
  PROD" / "PROD igual al repo" (o "a STST") / "No existe en PROD"; y avisa
  "STST distinto del repo" cuando no coinciden: o falta instalar el repo en
  STST (p.ej. carind sin los candados de PROD), o STST tiene un cambio que no
  se subió (p.ej. el arreglo de 325671/325715).
- **Diff:** a pedido del usuario, el repo va siempre a la izquierda (A); a la
  derecha, el primero de STST, PROD, UAT, SIT, DEV que tenga otra versión
  (`vDefaultPair`). `/vistas/diff` usa `build_diff` copiado de
  `versiones-vistas-imprimibles/views.py`: el diff **no muestra
  comentarios**, así que para saber de qué ítem viene un cambio conviene
  mirar el texto completo ("Copiar SQL"): p.ej. el arreglo de 325671/325715
  en `CAR_IND_AUTOS_VIEW` de STST solo se identificó por sus comentarios
  `-- WI 325671/325715`.
- **Pisar con el repo (`instalar.py`, `/vistas/pisar`):** en el diff, un
  botón por ambiente tildado que difiere del repo ("Pisar X", o "Crear en X"
  si no existe; PROD en rojo). Abre una confirmación con lo que se instala
  (archivo, commit), lo que se reemplaza y el CREATE que se ejecuta
  (`/vistas/pisar_preview`). En PROD: alerta roja, aviso si STST no tiene
  esa versión, y hay que escribir `PROD`; el servidor también lo exige. El
  DDL es el CREATE que el .sql trae comentado, descomentado (también las
  columnas comentadas si ocupa varias líneas), **sin FORCE** (si la query
  falla, Oracle no la crea y la vista queda como estaba) + la primera query
  del archivo. Antes de ejecutar, el servidor vuelve a leer el repo y el
  ambiente y no pisa si el md5 no es el que vio el usuario (409). Si el
  CREATE no dice esquema o el esquema no es el dueño actual, se niega.
  Guarda la versión anterior con `DBMS_METADATA.GET_DDL` en
  `backups/<AMBIENTE>/<VISTA>_<fecha>.sql` (CP1252) y cada intento en
  `backups/instalaciones.jsonl` (`backups/` está en .gitignore). Se conecta
  como DM_DBA, que tiene CREATE ANY VIEW en los ambientes. **Ojo al
  probarlo:** `cursor.parse()` de un DDL lo ejecuta; para validar sin
  instalar, parsear solo el SELECT.
- **`LAST_DDL_TIME` no es la fecha del cambio:** se actualiza también cuando
  la vista se recompila sola porque cambió algo de lo que depende. El
  25/09 en STST se reemplazó `POLICY_DETAILS_VIEW` y ~25 vistas quedaron con
  esa fecha (entre ellas `CAR_IND_AUTOS_VIEW`, cuyo contenido es del 23/09).
- **Repo (`repo_views.py`):** lee la referencia `REPO_REF` (default
  `origin/develop`) con `git ls-tree` + `git cat-file --batch`, **no el
  working tree** (el clon suele tener cambios sin commitear o estar en otra
  rama). Indexa `REPO_VIEW_DIRS` (`GDS/FASE 1` y `GDS/Complementarias`, donde
  viven las `CALCULUS_V*_VIEW`); las subcarpetas `Deprecadas`, `Versiones en
  prod` y `no estables` solo cuentan si no hay otro archivo. La referencia se
  mueve con el botón "Refrescar" (`vRefrescar`: primero `git fetch origin`,
  que no toca la rama local ni el working tree, y después vuelve a leer todas
  las fuentes; si el fetch falla, igual compara con lo que hay). El par que el
  usuario eligió en el diff (`vs.pair`) sobrevive al refresco. Los mensajes
  de commit se decodifican UTF-8 con fallback a Windows-1252.
  **Del archivo solo cuenta la primera sentencia** (`_first_statement`: hasta
  el primer `;` o línea con solo `/`, fuera de literales y comentarios).
  Varios .sql traen después queries de validación que nunca están en la
  base (p.ej. `rec-pag_nota-cred-acumulativo.sql`); antes se comparaban y
  marcaban "Repo distinto de STST" en falso. `versiones-vistas-imprimibles`
  todavía compara el archivo entero (solo saca un `;` final).
- **Base (`vistas.py`):** una sola consulta por ambiente con todas las vistas
  (`DBA_VIEWS` + `DBA_OBJECTS`, fallback `ALL_*`). Si el nombre existe en más
  de un esquema prefiere `INSOR_GDS` (en `versiones-vistas-imprimibles` gana
  el primero alfabético, `INSOR_DM`).
- No compara Jasper (`.jrxml`): solo vistas.

## Pestaña "Releases"

Registro de qué tocamos de nuestro lado (vistas de INSOR y reportes Jasper)
en cada pase a PROD, pedido por el usuario para que quede guardado release
por release. No consulta la base: lee `releases/AAAA-MM-DD.json`
(`releases.py`), uno por pase, versionados con la app.

- **Cómo se arma un release:** a mano o pidiéndoselo a Claude, a partir del
  correo de VoBo del pase ("GDS - MX - VoBo liberación a Prod …"), del
  contenido a liberar de cada ítem en Azure DevOps y de los commits del repo
  INSOR que lo nombran (`git log --all --grep=<id>`). Cada ítem lleva `lado`:
  `imprimibles` (con `vistas`, `jasper`, `commits`), `insis`, `no_aplica` o
  `sin_registro` (p.ej. incidentes de L2 sin commits ni ítems en ADO). Los
  ítems que van pero no figuraban en el correo llevan `en_correo: false` (y
  `incluido_por` si son hijos de una historia que sí figura); los que no se
  sabe si van, `confirmar: true`. Ojo: los commits que nombran una US pueden
  incluir cosas que no van (237275 nombra RC USA y Vidauto, fuera de alcance)
  y un arreglo puede no tener commit (325671/325715 se hicieron directo en
  STST): por eso se cura a mano, no se genera solo.
- **Foto:** `py -3.10 releases.py foto AAAA-MM-DD` guarda en el JSON, por cada
  vista del release, el archivo y commit del repo y el md5 de UAT, STST y
  PROD en ese momento: queda registrado qué versión iba en el pase. Tomarla
  cuando el release está listo para salir (y repetirla si cambia el repo).
- **Detalle en pantalla:** notas, "Lo que tocamos" (vistas como botones que
  abren "Comparar vistas" con ese release y esa vista, Jasper, commits con
  link a GitHub), la foto y "El resto del pase". "Comparar estas vistas
  ahora" abre "Comparar vistas" con el conjunto del release (`vIrA`).
- Primer release registrado: 2026-10-06 (17 ítems del correo + 9 que no
  figuraban en él; 10 con cambios nuestros, 5 vistas).

## Archivos clave

- `app.py` — rutas Flask: `/`, `/cases`, `/products`, `/policy_cases`,
  `/policy_cases_sample`, `/resolve_policy`, `/policy_annexes`,
  `/report_families`, `/run_case`, `/doc_resolve`, `/doc_for_policy`,
  `/doc_download`, `/pdf/<token>`, `/vistas/repo_info`, `/vistas/repo_index`,
  `/vistas/fuente`, `/vistas/diff`, `/vistas/repo_fetch`, `/releases`,
  `/releases/<fecha>`.
- `releases.py` + `releases/*.json` — registro de releases y foto de sus
  vistas, ver "Pestaña Releases".
- `vistas.py` / `repo_views.py` / `sqltext.py` — texto de las vistas en la
  base y en el repo, normalización y diff (ver "Pestaña Comparar vistas").
- `instalar.py` — pisar una vista de un ambiente con la versión del repo
  (backup + log en `backups/`), ver "Pisar con el repo".
- `documentos.py` — qué documentos tiene una póliza y catálogo `REPORTS`
  (label + grupo), ver "Pestaña Descargar documentos".
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

- Familias soportadas por "Buscar casos": `Car_Ind`/`Cot_Ind` (vía
  `insis_gen_v10.policy` + catálogo de producto). `Car_Mul` vía
  `CAR_MUL_VIEW` existe en el backend (`/cases`) pero la pantalla ya no lo
  ofrece; para multinciso, llevar las pólizas desde "Buscar pólizas".
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
