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
  orden en que saldrán (`Planner.lista_tanda`, misma cuenta que el motor). PENDIENTE (Opus): arrastrar/reordenar/mover
  cuñas entre bloques (el motor aún no sabe de «fija en el bloque X, puesto N») y rubros con conflictos.

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
4. Página de salud y prueba de 72 h seguidas (lo último antes de publicar).
5. Voice tracking de días futuros: hoy el programa vive en memoria unas horas; hace falta guardar la programación diaria.
6. Opcionales: análisis por lote en segundo plano; icono propio para Identificadores.

Estimación honesta para publicar: ~60–65 %. Lo que falta es lo que decide si aguanta 24/7 (prueba de 72 h, encoder, salud).
