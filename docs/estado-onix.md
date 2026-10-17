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

Estimación honesta para publicar: ~60–65 %. Lo que falta es lo que decide si aguanta 24/7 (prueba de 72 h, encoder, salud).
