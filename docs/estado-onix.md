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
  Motor: tipo de audio `Locucion` = talk-over (entra en el cue_outro o la mezcla, música al 30 %, la siguiente entra al
  terminar la voz). `backend/api/voicetrack.py`, `frontend/studio/voicetracker.js`, `audio_engine.py` (_cama).
- Publicidad/Biblioteca: las marcas suenan justo desde donde están; cerrar la ventana corta el audio.

- Locutor IA (pasos 1-3): `backend/locutor/` (guion, validador «cero datos inventados»,
  plantillas de emergencia, noticias RSS, moderación, `almacen.py`, `servicio.py`) + `backend/api/locutor.py`.
  Hilo de fondo que, si está encendido (`POST /api/v1/locutor/activar`), llena huecos del programa con locuciones
  `Locucion` generadas ANTES de salir al aire. Aprobación `auto`/`manual` por tipo (por defecto manual: noticia,
  patrocinio, complacencia). La configuración de AzuraCast ya está importada (turnos, patrocinio, 4 fuentes, reglas).
  Nace APAGADO. Los mensajes de oyentes aún no llegan (no hay canal de entrada): se cargan a mano por API.
  Arreglo de paso: `MockBackend.get_position_sec` perdía precisión en Windows (2 pruebas del talk-over fallaban).

## No verificado todavía
- Locutor IA: nunca ha corrido con la tarjeta real ni con claves reales; la prueba de 72 h debe incluirlo.
- Windows no trae `tzdata`: la hora de la emisora cae a UTC-4 fijo (Caracas) si falta; `pip install tzdata` lo evita.
- Talk-over con la tarjeta real (BASS): volumen al 30 % y salida de 1,5 s pueden necesitar ajuste de oído.
- Fish Audio y Gemini TTS con claves reales (la nube no llegaba a Fish; Gemini solo validó el formato).
- El micrófono solo funciona con el Studio abierto como http://localhost (por IP de red local el navegador lo bloquea).

## Pendiente (en este orden sugerido)
1. Locutor IA: pantallas hechas (Studio › 🎙 VOZ: por aprobar/listas/probar; Admin › 12 Locutor IA: encender, auto/manual, turnos, patrocinios, fuentes, saludos). Falta: probarlo en el aire con claves reales y el canal de entrada de mensajes de oyentes. Su código está en
   `C:\Users\REDESSOCIALES\Desktop\SOLIS FM DIGITAL\SOLISFM\solis fm digital\LOCUTOR IA` (leerlo, sin copiar claves).
   Regla: la voz se genera antes de salir al aire, nunca en el momento; mensajes de oyentes con aprobación previa.
2. Cartuchera como Soho. HECHO (Sonnet): clic derecho en cualquier botón › Cargar efecto (sube un archivo y queda como
   «Efecto» en library/efectos), Editar (título, color, marca in/out con preescucha, volumen) y Vaciar;
   `backend/api/cartuchera.py`, `frontend/studio/cartuchera.js`, columnas nuevas en `cartuchera`.
   FALTA (motor, Opus): `fire_effect` hoy ignora todo eso. El UI ya manda `banco` y `slot` en el comando.
   - Pulsar otra vez el mismo botón mientras suena = DETENER (hoy lo vuelve a lanzar). Avisar con evento
     `effect_stopped {audio_id}` (el UI ya lo escucha) y `effect_fired` con `duracion`.
   - Respetar `mark_in`, `mark_out` y `volumen` (%) del botón al disparar (leerlos de `cartuchera` por banco/slot).
   - Revisar el ducking de la música (`_duck_until`) cuando el efecto se corta antes.
   - Tocar también `engine_client.fire_effect` y la lista de `engine_service` (motor remoto).
3. RDS + encoder propio (falta saber marca/modelo y cómo recibe el texto: red, puerto COM o archivo).
4. Página de salud y prueba de 72 h seguidas (lo último antes de publicar).
5. Voice tracking de días futuros: hoy el programa vive en memoria unas horas; hace falta guardar la programación diaria.
6. Opcionales: análisis por lote en segundo plano; icono propio para Identificadores.

Estimación honesta para publicar: ~60–65 %. Lo que falta es lo que decide si aguanta 24/7 (prueba de 72 h, encoder, salud).
