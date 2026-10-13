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

## No verificado todavía
- Talk-over con la tarjeta real (BASS): volumen al 30 % y salida de 1,5 s pueden necesitar ajuste de oído.
- Fish Audio y Gemini TTS con claves reales (la nube no llegaba a Fish; Gemini solo validó el formato).
- El micrófono solo funciona con el Studio abierto como http://localhost (por IP de red local el navegador lo bloquea).

## Pendiente (en este orden sugerido)
1. Locutor IA: integrar el panel que hoy funciona en AzuraCast. Su código está en
   `C:\Users\REDESSOCIALES\Desktop\SOLIS FM DIGITAL\SOLISFM\solis fm digital\LOCUTOR IA` (leerlo, sin copiar claves).
   Regla: la voz se genera antes de salir al aire, nunca en el momento; mensajes de oyentes con aprobación previa.
2. Cartuchera: botón «Editar» como Soho (color y título; marca in/out y volumen respetados al disparar = motor).
3. RDS + encoder propio (falta saber marca/modelo y cómo recibe el texto: red, puerto COM o archivo).
4. Página de salud y prueba de 72 h seguidas (lo último antes de publicar).
5. Voice tracking de días futuros: hoy el programa vive en memoria unas horas; hace falta guardar la programación diaria.
6. Opcionales: análisis por lote en segundo plano; icono propio para Identificadores.

Estimación honesta para publicar: ~60–65 %. Lo que falta es lo que decide si aguanta 24/7 (prueba de 72 h, encoder, salud).
