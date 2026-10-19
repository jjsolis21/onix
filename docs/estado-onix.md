# ONIX FM — estado y reglas de trabajo (traspaso a la sesión local)

Léelo antes de tocar el código. Escrito al pasar de la sesión en la nube a Claude Desktop en la PC de la radio.

## Reglas del dueño (siempre)
- Responder en español, como asesor: la verdad incómoda primero; marcar confianza [Cierto] / [Probable] / [Suposición];
  no empezar dándole la razón si no la tiene; sin «Buena pregunta», «Tienes toda la razón», «Totalmente», «Definitivamente».
- Al empezar cada respuesta, decir qué modelo conviene: Sonnet para pantallas/diseño/API+UI; Opus para el motor de audio,
  pausa, talk-over, RDS/encoder, marcas al aire, bugs difíciles y la prueba de 72 h.
- Referencia funcional: Jazler SoHo (manual en `docs/referencia/`). Reutilizar ventanas y modelos que ya existen; no reinventar.
- Correr `python -m pytest -q -p no:warnings` y que pase completo antes de dar algo por terminado.
- Claves de IA: solo en Admin › IA (base de datos). Nunca en el código, en git ni en el chat.

## Hecho (última sesión en la nube)
- Regla «volver a sonar cada N horas/días/semanas/meses», individual (editor de canción) y por lote (Biblioteca › Restricciones);
  en manual el Studio avisa si ya sonó y el operador decide.
- Admin › 11 IA: varias claves con orden de respaldo (Gemini, OpenAI, Groq, OpenRouter, compatibles, Fish Audio solo voz
  con voces «Nombre=id»: Britny y Dag). `backend/ia/proveedores.py`, `backend/api/ia.py`.
- Voice Tracker (Studio › pestaña 🎙 VOZ): grabar con micrófono (WAV) o voz de IA; se inserta tras la canción elegida.
  Motor: tipo de audio `Locucion` = talk-over (entra en el cue_outro o la mezcla, música al 20 % (configurable en Admin › Motor: «Música bajo la voz»), la siguiente entra al
  terminar la voz). `backend/api/voicetrack.py`, `frontend/studio/voicetracker.js`, `audio_engine.py` (_cama).
- Publicidad/Biblioteca: las marcas suenan justo desde donde están; cerrar la ventana corta el audio.

- Locutor IA (pasos 1-3): `backend/locutor/` (guion, validador «cero datos inventados»,
  plantillas de emergencia, noticias RSS, moderación, `almacen.py`, `servicio.py`) + `backend/api/locutor.py`.
  Hilo de fondo que, si está encendido (`POST /api/v1/locutor/activar`), llena huecos del programa con locuciones
  `Locucion` generadas ANTES de salir al aire. Aprobación `auto`/`manual` por tipo (por defecto manual: noticia,
  patrocinio, complacencia). La configuración de AzuraCast ya está importada (turnos, patrocinio, 4 fuentes, reglas).
  Nace APAGADO. Los mensajes de oyentes aún no llegan (no hay canal de entrada): se cargan a mano por API.
  Arreglo de paso: `MockBackend.get_position_sec` perdía precisión en Windows (2 pruebas del talk-over fallaban).

- Locutor IA con datos: `backend/locutor/datos.py` busca UNA vez por artista (Wikipedia es) y por canción (año en
  MusicBrainz, o `audios.fecha_lanzamiento`), lo guarda en `locutor_datos` y la entrada pasa de «corta» a «contenido»
  (despide con dato, presenta con dato, cierra con la emisora). Sin coincidencia clara no guarda nada y va la corta.
  Editable en Admin › 12 › «Datos de artistas y canciones». «contenido» nace en aprobación manual.
  Gemini gratis: cuota muy corta de texto y de voz (429) y picos de 503; no aguanta hablar tras cada canción.

- Relojes (Admin › 8) al estilo del editor de Soho: paleta de elementos, filtros con contador en vivo
  («N canciones disponibles · duración media»), Agregar / Insertar / Cambiar / Eliminar sobre la fila elegida,
  reloj circular coloreable por tipo, género, rotación o subgénero, y verificador único. La tabla de filas sigue
  siendo la fuente de datos. Falta (bloque 2): pestaña Canciones, filtros por año, BPM y prioridad (tocan el planificador).

- Studio › paletas con muchas paletas: con más de 6 se pasa de pestañas a un selector (▦) con buscador, agrupado por
  nombre («SOLIS 1», «SOLIS 2» → grupo SOLIS), las últimas usadas al lado, flechas ◀ ▶ en la cabecera y atajos `,` y `.`.
  Cada paleta se ve de 9 en 9 (3×3): teclas 1–9 = botones de la página visible, `-` y `+` cambian de página.
  Admin › Cartuchera: 3 columnas como Listas (acciones y filtro · paletas creadas · botones de la paleta elegida, de 9 en 9,
  con el color del efecto dibujado en el botón de play; clic = preescucha, otra vez = detener).
  Tope fijo: 63 botones por paleta (7 páginas de 9). Una paleta nace con 1 página y crece con «＋ Agregar página» en el
  Admin (ya no hay «cantidad de botones»). Para más efectos se crean más paletas (SOLIS 1, 2, 3…), como en Soho.

- Admin › Comerciales como Soho (bloque 1): botón «⚙ Config. de comerciales» (General: tandas; Prioridades: secciones de la
  tanda; Clientes y Agencias: catálogo con contacto, al renombrar se actualizan los spots) y «🩺 Chequeo de integridad»
  (audio perdido o en Papelera, sin horarios, fechas, vencido, sin rubro). `backend/api/comerciales.py`,
  `frontend/admin/sections/03-programacion-pautas/comerciales-config.js`.
- Tandas por día y hora (pestaña «Breaks por hora»): config `tandas_grilla` JSON {"dia-hora": {minutos, inmediatas,
  cierre} | {off: true}}, dia 0=Dom como la matriz. Sin entrada = valores generales. Motor: `tandas_de_hora`,
  `_tandas_de_ahora`; planner `_proxima_tanda` calcula cada hora. API GET/PUT `/api/v1/comerciales/tandas-grilla`.
- «📋 Tandas del día» (solo lectura): GET `/api/v1/comerciales/tandas-dia?fecha=`; muestra por hora y bloque las cuñas en el
  orden en que saldrán (`Planner.lista_tanda`, misma cuenta que el motor). Orden a mano por día y hora: arrastrar
  entre bloques, ✕ quitar ese día, ↺ volver a automático. Tablas `tanda_ajustes`/`tanda_quitadas` (creación perezosa),
  `leer_ajustes`/`aplicar_ajustes` en audio_engine (motor y planner), PUT/DELETE `/api/v1/comerciales/tandas-dia/ajuste`;
  `reload_config` rehace el reparto de la hora. PENDIENTE (Opus): rubros con conflictos.
- Corte inmediato (tandas y eventos): la canción baja en dB los `fade_corte_seg` (5 s por defecto) PREVIOS a la hora y la cuña entra
  a la hora exacta con la canción ya en silencio (no se mezclan). `_check_tanda_inmediata` → `_corte_pend`/`_prefade`/`_ejecutar_corte`,
  `_prefade_iniciar`. Curva configurable `fade_corte_curva` (% en partes iguales; defecto 100,70,40,30,16): termina con la
  canción aún audible y esa cola se va bajo el empalme de la cuña (sin «vacío» antes de entrar). Si no había nada que poner, la canción recupera su volumen. Medido con BASS real.
- Voz de la cuña y regla «mujeres no se pegan»: `audios.genero_vocal` (ventana de edición, tipo Cuña muestra «Voz»); `es_voz_mujer`,
  `ordenar_tanda` (voz pesa más que rubro; cede si no hay cómo; interruptor `regla_voces_mujer` en Config. de comerciales › General);
  chequeo avisa «sin_voz»; Tandas del día marca ♀. Rubros incompatibles (misma tanda sí, seguidos no): tabla `comerciales_conflictos_rubros`,
  `rubros_chocan`/`norm_rubro` en `ordenar_tanda` (sin tildes ni mayúsculas), pestaña «Rubros» en Config. de comerciales,
  GET `/api/v1/comerciales/rubros`, POST/DELETE `.../rubros/conflictos`. PENDIENTE (Opus): secciones Promociones + separador de entrada.
- Tanda «al cierre» robusta: si la canción cruza la hora, la tanda (en cola) NO se descarta al cambiar la hora (gracia 5 min,
  `cierre_hasta`), sale al terminar la canción; el plan la mantiene marcada (`_cierres_hechos`, `_proxima_tanda` mira la hora anterior).
  Studio rotula «entra HH:MM → termina HH:MM». General › vista previa de las próximas tandas.
- Cartuchera aparte: `audios.ambito = 'cartuchera'` (migración 41; lo de library/efectos pasa a ese ámbito). Lo cargado desde la
  cartuchera NO sale en Identificadores, Biblioteca/Studio (`/api/v1/audios` lo excluye salvo `ids=` o `ambito=cartuchera`), conteos ni relojes.
  Colores de la cartuchera: por defecto #31FF1F (Lime) + #1447E6, #E61876, #FFDF20, #4D179A; el texto del botón pasa a negro/blanco según el fondo.
- Lista de spots: «☰ Columnas» (como la Biblioteca Musical): `data-col` en cada celda, estilo generado, preferencia en localStorage `cm_cols`.
  Paso «Audio y versiones» del spot: panel lateral (Elegir de la biblioteca / Subir archivo / Agregar versión), zona para soltar archivo,
  y el selector de cuñas con panel de filtros (anunciante, con/sin pauta) y subida directa. UI sin verificar a ojo (panel rechazó la página de prueba).
- Cargar un comercial = la MISMA ventana de canción (como Soho): `CunasBiblioteca.cargarConEditor({archivo, anunciante})` abre
  `EditorCancion.abrir(null, {tipo:'Cuña', tipos:['Cuña'], archivo, artista, onGuardar, onCerrar})` con marcas inicio/intro/outro/mezcla,
  fundidos, volumen, AutoCue y datos. Se usa en «Subir archivo…» del spot, en el selector de cuñas y en «✎ Cargar cuña…».
  UI sin verificar a ojo.
- «🧩 Bloques publicitarios»: elegir spots activos (con duración), orden, y programar «empieza / empieza inmediato / termina a las HH:MM»
  + días. Se guarda como reloj «Bloque: X» (filas con audio fijo) + `reloj_eventos`, así sale en Relojes › Eventos.
  API GET/POST/DELETE `/api/v1/comerciales/bloques`, GET `/cunas-activas`. UI sin verificar a ojo (el panel rechazó abrir la página de prueba).

## No verificado todavía
- Locutor IA: nunca ha corrido con la tarjeta real ni con claves reales; la prueba de 72 h debe incluirlo.
- Windows no trae `tzdata`: la hora de la emisora cae a UTC-4 fijo (Caracas) si falta; `pip install tzdata` lo evita.
- Talk-over con la tarjeta real (BASS): volumen de fondo (20 %) y salida de 1,5 s pueden necesitar ajuste de oído.
- Fish Audio y Gemini TTS con claves reales (la nube no llegaba a Fish; Gemini solo validó el formato).
- El micrófono solo funciona con el Studio abierto como http://localhost (por IP de red local el navegador lo bloquea).

## Pendiente (en este orden sugerido)
1. Locutor IA: pantallas hechas (Studio › 🎙 VOZ: por aprobar/listas/probar; Admin › 12 Locutor IA: encender, auto/manual, turnos, patrocinios, fuentes, saludos). Falta: probarlo en el aire con claves reales y el canal de entrada de mensajes de oyentes. Su código está en
   `C:\Users\REDESSOCIALES\Desktop\SOLIS FM DIGITAL\SOLISFM\solis fm digital\LOCUTOR IA` (leerlo, sin copiar claves).
   Regla: la voz se genera antes de salir al aire, nunca en el momento; mensajes de oyentes con aprobación previa.
2. Cartuchera como Soho: HECHO. Clic derecho en cualquier botón › Cargar efecto (sube un archivo y queda como
   «Efecto» en library/efectos), Editar (título, color, marca in/out con preescucha, volumen) y Vaciar.
   Motor: pulsar otra vez el mismo botón mientras suena lo DETIENE; se respetan marca in, marca out y volumen;
   la música recupera su volumen al cortar el efecto (`_do_effect`, `_stop_effect`, `_vigilar_efectos`,
   eventos `effect_fired` con `duracion` y `effect_stopped`). `backend/api/cartuchera.py`, `frontend/studio/cartuchera.js`.
   Sin verificar con la tarjeta real: el corte en la marca out es seco (sin fundido) y se revisa cada tick (~50 ms).
   Límite conocido: hay 6 canales de efectos; el botón 7 comparte canal con el 1 (y lo corta).
3. RDS + encoder propio (falta saber marca/modelo y cómo recibe el texto: red, puerto COM o archivo).
   Hecho de ese punto: «Publicar metadata» por audio (migración 39, audios.meta_*; editor de canción; `backend/audio/publicar.py`;
   GET `/api/v1/nowplaying` = texto que se publicaría, manteniendo el de la última que actualizó). FALTA: enviarlo al RDS/encoder.
4. Página de salud y prueba de 72 h seguidas (lo último antes de publicar).
5. Voice tracking de días futuros: hoy el programa vive en memoria unas horas; hace falta guardar la programación diaria.
6. Opcionales: análisis por lote en segundo plano; icono propio para Identificadores.
7. **Chequeo de integridad de los archivos (PENDIENTE, lo pidió el dueño):** revisar los audios y decir cuál está
   corrupto, dañado, no suena o se borró. Debe notificar cada caso (no solo contar). Pendiente de diseñar y construir.
9. **RESPALDOS (PRIORIDAD ALTA):** `backend/api/respaldos.py` copia con `sqlite3.connect(DB_PATH)` el archivo SQLite VIEJO, no MariaDB.
   El respaldo diario hoy no protege los datos reales. Hace falta respaldar `onix_db` (volcado en Python o `mysqldump`) y
   su restauración (`scripts/restaurar_respaldo.py`, `backend/api/locutor.py:528` exportación). Hasta entonces: respaldo manual de MariaDB.
10. Migración SQLite→MariaDB, lo que faltaba (corregido 2026-10-18): ids sin AUTO_INCREMENT, triggers de `audios.busq`,
    `julianday`/`rowid`, `datetime('now', ?)` del Locutor, `%` y `ESCAPE '\\'` en el adaptador. Todo nuevo esquema va en
    `backend/migraciones_maria.py` (corre al arrancar la API), NO en `scripts/init_db.py` (solo migra el SQLite histórico).
    Conexiones ahora en READ COMMITTED (antes REPEATABLE READ: lecturas viejas entre PCs).
11. **Aprobación de canciones (HECHO 2026-10-18):** la Bandeja pasó a llamarse «⏳ Por completar — no suenan (n)». Una canción que llega con
    género y rotación suena sola; una que falta se completa y se aprueba sola cuando una edición (individual o por lote) le pone lo que faltaba
    (una completa que alguien retuvo a propósito NO se aprueba sola). El aviso de carga dice qué pasó («lista para sonar» o «falta género…, no suena»).
    La vista «Por completar» explica cuántas faltan de qué y tiene «✓ Completar y aprobar» (pone género/rotación solo a las que no lo tienen).
    Botón «A la Bandeja» → «⏸ Retener». El diálogo de carga por lote (paso 3, «Género y rotación») exige género y rotación para continuar; la única
    salida es marcar «Cargar sin clasificar» (quedan por completar); el resumen final dice cuántas quedaron listas para sonar y cuántas por completar.
12. **Géneros y categorías solo de la lista (HECHO 2026-10-18):** la causa eran duplicados en la propia lista (Pop/POP, Balada/BALADA, Salsa/SALSA,
    Hombre/HOMBRE…) y que la validación nunca rechazaba un valor. Ahora: crear un valor repetido (sin distinguir mayúsculas ni tildes) da 409; poner un
    género que no está en la lista da 422 (en edición y lote); una carga cuyo género de las etiquetas no está en la lista lo deja vacío con aviso;
    la columna no distingue mayúsculas (el UNIQUE lo impide); la pantalla ya no pasa los géneros a mayúsculas. Migración 49 aplicada en `onix_db`
    (respaldo previo `onix-2026-10-18_1446.sql.gz`): Balada 24, Pop 23, Salsa 27, Hombre 51 + Mujer 17 + Grupo 7 + Dúo 1; voces de sistema
    Hombre/Mujer/Genérico aseguradas. DECISIÓN DEL DUEÑO pendiente sobre valores que quedan en la lista y no son repetidos: `DANCE`, `URBANO`,
    `COMERCIAL` (género interno de las cuñas), voz `MALE` y `FT`, y una canción con género `GEN_ALPHA` que no está en la lista.
    RESUELTO el mismo día: GEN_ALPHA quitado; MALE = Hombre; voces oficiales Hombre, Mujer, Dúo, Grupo, FT (+ Genérico para cuñas); Dance y Urbano
    con la forma «Dance», «Urbano». COMERCIAL ya NO es un género: las cuñas se identifican por su tipo (Cuña) y viven en la sección Comerciales, no en
    la lista de géneros de la Biblioteca Musical. REGLA: géneros, rotaciones y subgéneros son solo los que el usuario crea; una instalación nueva no
    trae ninguno de adorno (solo las voces). En `onix_db` se retiraron los sin uso (géneros: Clásica, Cumbia, Electrónica, Jazz, Ranchera, Reggaetón,
    Rock, Romántica, Tropical; rotación: Desconocido; subgéneros: Acústico, Cover, En Vivo, Remix). Respaldo previo: `onix-2026-10-18_1500.sql.gz`.
13. **Categoría «Propiedades» (DECISIÓN DEL DUEÑO: SE MANTIENE):** existe en Configuración › Categorías pero hoy está vacía y sin conexión (sin respaldo
    en el servidor ni en la base). Se queda. Pendiente construirla de verdad: etiquetas libres por canción (el dueño da el ejemplo «MAE»; confirmar su
    significado) que los relojes, playlists y reglas puedan filtrar, creadas solo por el usuario (misma regla que géneros y rotaciones).
14. **Pantalla siempre al día (HECHO 2026-10-18):** el navegador guardaba copias del JavaScript viejo (`?v=16`) y los cambios no se veían sin
    forzar la recarga. El servidor ahora manda `Cache-Control: no-cache` a JS/CSS/HTML (revalida; 304 si no cambió) y se subieron los `?v=`.
    Reproductor de la Biblioteca: el «No se pudo reproducir» intermitente era un AbortError del navegador al cambiar de canción rápido (no un
    fallo del archivo); ahora se ignora y los fallos reales (archivo borrado o movido) dicen la causa.
8. **Tips sobre el intro y puente entre canciones (estilo SoHo) — HECHO 2026-10-18 (sin probar con BASS real):**
   - Reloj: la fila de Tips tiene «Suena solo / Inicio del intro / Final del intro / Puente» (`reloj_filas.tip_modo`, migración 50).
     Con modo, la fila no ocupa tiempo: de intro va sobre la canción siguiente; de puente, entre la anterior y la siguiente.
   - Studio (manual): en Separadores, filtro fijo «💡 Tips: …» junto a Tipo; con un modo elegido, «💡 COLOCAR» / doble clic en un Tips lo monta;
     aviso ámbar con qué tip entra, cómo y a qué segundo, y botón QUITAR TIP. Comando `tip` / `tip_quitar` (WS, API y motor remoto).
   - Motor: canal propio (9), entra a pleno; la música baja al 45 % (≈ −7 dB, `tip_fondo_pct` en Admin › Motor de audio; las voces siguen en `voz_fondo_pct`) en 250 ms y empieza a subir ~1,3 s ANTES de que el tip termine, para estar a pleno justo al callar.
     Final del intro = intro − duración (20 − 7 ⇒ 13 s). Puente = cruza el cambio: arranca (mezcla − mitad del tip) y sigue encima de la siguiente (tip 6 ⇒ 3 s antes y 3 s después de que entre la siguiente; con crossfade, «a 3 s del final» ya sería tarde). En el puente el cambio de canción dura 1,5 s (`TIP_XFADE`, no los 4 s del crossfade normal): medido con BASS real (salida «sin sonido»), con 4 s la siguiente casi no se oía bajo el tip y parecía un tip suelto en los últimos segundos. El tip NO es una fila «siguiente»: en el programa y la cola va como insignia 💡 sobre la canción que pisa (intro → la que entra, puente → la que sale). Si el puente no puede salir (nada después), el Studio avisa. Sin intro ⇒ al inicio.
     Tip más largo que el intro ⇒ fundido de 0,8 s que termina justo en la voz (en el puente, también la voz de la canción que entra).
   - Entrada de canciones (2026-10-18): la que entra en un crossfade ya NO sube en 4 s (medido con BASS: 0,13 a los 0,5 s); llega a su volumen en 0,5 s (`ENTRADA_CANCION`) salvo que tenga fade_in propio. La que sale sigue bajando en todo el crossfade.
   - Casos: el puente no se monta si lo que sigue es tanda, voz o nada; una cuña, voz, salto o stop cortan el tip con fundido corto;
     la pausa lo congela; si el operador adelanta más allá del intro, no sale; el tip de intro espera a la primera canción tras la tanda.
   - Pruebas: `tests/test_tips.py` (20) y una de API en `test_relojes_api.py`.

Estimación honesta para publicar: ~60–65 %. Lo que falta es lo que decide si aguanta 24/7 (prueba de 72 h, encoder, salud).
