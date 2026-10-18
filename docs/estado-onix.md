# ONIX FM — estado, cambios y reglas de trabajo

Léelo completo antes de tocar el código. Actualizado el 18-oct-2026 por la sesión en la nube (la última que trabajó allí).
**Las dos primeras secciones son las más importantes: dónde vive el código y cómo guardarlo sin perder nada.**

## 1. Dónde vive el código (leer primero)

- **La fuente de verdad es la carpeta de la PC de la radio:**
  `C:\Users\REDESSOCIALES\Desktop\SOLIS FM DIGITAL\SOLISFM\ONIX`, rama `fase-0-1-motor-z3bhab`.
- La rama `fase-0-1-motor-z3bhab` **de GitHub quedó abandonada**: la sesión en la nube dejó de subir ahí. Tiene 1 commit
  (`7af8041`, arreglo del auto play) que la PC **no** tiene; la PC tiene 79+ commits que esa rama no tiene.
  Las dos líneas partieron de `fe84d94` (la primera nota de traspaso) y divergieron.
- **Copia de seguridad en GitHub:** rama `respaldo-local` (se sube desde la PC con
  `git push origin fase-0-1-motor-z3bhab:respaldo-local`, sin `--force`).
- **PROHIBIDO** en la PC: `git pull`, `git merge`, `git fetch` de `origin/fase-0-1-motor-z3bhab`, y ejecutar
  **«Actualizar ONIX.bat»** (hace `git pull` y reproduce el conflicto). Para arrancar: «Iniciar ONIX.bat».
- Nunca `git add .` ni `git add -A`: `media/` (los audios) **no está en `.gitignore`** y entraría al historial.
  Nombrar los archivos uno por uno. Nunca `reset --hard`, `rebase`, `amend` ni `push --force` sobre commits ya subidos.
- Si hay un merge a medias: no hacer commit; hacer copia de la carpeta, `git diff HEAD > ..\algo.patch`, y `git merge --abort`.
- Copia de código antes de cambios grandes:
  `robocopy "<ONIX>" "<ONIX>-RESPALDO-<tema>" /E /XD media`
- **Fuera de git** (lo más difícil de reponer): los audios (`media/`) y la base de datos `.db` (biblioteca, pautas, relojes,
  claves de IA). Hacer copia semanal a un disco distinto. La función de respaldos de ONIX (`RADIO_BACKUP_DIR`) existe, pero
  no se verificó que guarde en un disco distinto del de ONIX.

## 2. Reglas del dueño (siempre)

- Responder en español, como asesor, no como asistente: la verdad incómoda primero; marcar confianza
  [Cierto] / [Probable] / [Suposición]; no empezar dándole la razón si no la tiene; sin «Buena pregunta»,
  «Tienes toda la razón», «Eso tiene mucho sentido», «Totalmente», «Definitivamente». Si discrepa: «No estoy de acuerdo porque…
  / Esto haría en su lugar… / El riesgo es…». No ceder si no hay información nueva.
- Al empezar cada respuesta, decir qué modelo conviene: **Sonnet 5.5** (pantallas, diseño, API+UI) u **Opus 5.5** (motor de
  audio, pausa, talk-over, RDS/encoder, marcas y volumen al aire, bugs difíciles, prueba de 72 h). Haiku no se recomienda.
- Referencia funcional: **Jazler SoHo** (manual en `docs/referencia/`). Soho reutiliza el mismo modelo de ventana en todas sus
  secciones: **reutilizar ventanas y componentes que ya existen; no reinventar**.
- `python -m pytest -q -p no:warnings` debe pasar completo antes de dar algo por terminado. Probar también en pantalla.
- Claves de IA: solo en Admin › IA (base de datos). **Nunca en el código, en git, ni pegadas en el chat.**
  (Una clave de Fish Audio se pegó en un chat: debe rotarse.)
- Nada de lo que sale al aire puede depender de que una IA responda: si fallan todas, plantilla sin IA.

## 3. Lo que se hizo (sesión en la nube, hasta `7af8041`)

**Motor y Studio**
- Pausa/reanudar (`_do_pausar`); al pasar a manual el programa de relojes se vacía; cola del operador editable
  (subir/bajar/quitar, insertar tras la canción elegida; comando `cola_insertar`).
- Studio compacto; paletas de cartuchera propias; separadores (identificadores) agrupados; listas (antes playlists).
- Escalado a ~20 000 audios: búsqueda con columna `busq` (sin acentos), paginación en servidor, `/api/v1/biblioteca/*` ligero.

**Biblioteca / Publicidad / Identificadores**
- Modal de Publicidad con el mismo modelo de ventana que Soho; matriz y ondas completas; editor de canción compartido.
- Sección **10 Identificadores** (jingles, sweepers, tips) con categorías propias (`audios.categoria_ident`, `ident_categorias`);
  los relojes filtran por esa categoría (`ident`).
- Análisis de canciones **solo como sugerencia** (BPM, energía, intro/outro aproximados en local; MusicBrainz, AcoustID,
  iTunes y Deezer en el análisis individual). Las fuentes en línea no se probaron con internet real.
- Marcas intro/outro: el ▶ de cada marca suena **justo desde la marca**; cerrar la ventana corta el audio
  (`PautaOnda.destroy()`, `OnixAudio.pausarTodo()` al cambiar de sección).

**Regla «volver a sonar cada N»** (`audios.repeticion_horas`, hasta 3 años)
- Editor de canción: número + unidad (horas, días, semanas, meses de 30 días). Por lote: Biblioteca › ⏱ Restricciones.
- `GET /api/v1/audios/{id}/repeticion`; en modo **manual** el Studio avisa «ya sonó, podrá volver a las HH:MM» y el
  operador decide (`respetaRepeticion` en `studio.js`). El automático ya saltaba esas canciones.

**Admin › 11 IA** (`backend/ia/proveedores.py`, `backend/api/ia.py`, migraciones 35 y 36)
- Varias claves con orden de respaldo; si una falla entra la siguiente; la fallida se salta 5 min.
- Proveedores: Gemini, OpenAI, Groq, OpenRouter, «otro compatible» y **Fish Audio (solo voz)** con voces
  `Nombre=id` (Britny y Dag). La clave nunca se devuelve por la API; solo el administrador entra (`/api/v1/ia/`).

**Voice Tracker** (Studio › pestaña 🎙 VOZ; `backend/api/voicetrack.py`, `frontend/studio/voicetracker.js`)
- Grabar con el micrófono (WAV) o crear la voz con IA (Fish → Gemini TTS → OpenAI); texto escrito con IA o plantilla.
- Motor: tipo `Locucion` = talk-over. Entra en el `cue_outro` (o la mezcla), la canción baja al 30 % y se despide 1,5 s antes
  del fin de la voz; la siguiente entra al terminar la voz; en manual sin nada después la canción vuelve y termina completa.

**Corrección del auto play de la Biblioteca (commit `7af8041`, SOLO en la nube; pendiente en la PC)**
- Síntoma: con auto play, una canción suena y la siguiente no, alternando, **sin error**.
- Causa [Cierto, demostrada en navegador]: `WaveSurfer.create({ media: audio })` compartía el `<audio>` del reproductor; al
  terminar de bajar el archivo le cambiaba el `src` por una copia interna con la canción ya sonando.
- Arreglo: crear la onda con `url:` (sin `media:`), copiarle el progreso en `timeupdate` (`ondaP.setTime`), clic en la onda =
  `audio.currentTime`, y aviso con el motivo si un archivo no puede sonar (`audio.addEventListener('error')`). Igual en la
  previa de Publicidad (`pautas-controller.js`, `_elegirPrevia`). Subir los `?v=` de los scripts. Ver `git show 7af8041`
  (solo lectura) y **aplicarlo a mano**, sin merge.

## 4. Lo que se hizo en la PC (lo sé solo por los mensajes de commit; **no lo revisé**)

[Suposición] a partir de `git log` y `git status` que pegó el dueño:
- Géneros solo los que crea el usuario (Comercial deja de ser género); carga por lote exige género y rotación;
  categorías en mayúsculas/minúsculas; «reproductor sin falsos errores y pantalla siempre al día».
- La categoría **Propiedades** se mantiene (pendiente construirla).
- **Tips sobre el intro y en puente**: funcionan en el reloj, en el Studio y en el motor según el chat local
  (suite: 392 pruebas, 0 fallos; no la vi). Commits `d1dff62` («WIP local») y `50e18d0` («Pruebas de Tips»), ya en `respaldo-local`.
- Archivos de MariaDB (`backend/esquema_maria.sql`, `backend/migraciones_maria.py`): no sé su propósito ni su estado.
- Sin seguimiento, a decidir: `PANTALLAS/`, `icons/`, `patch_planner.py`.

**Cada chat debe agregar aquí, al terminar una mejora, qué hizo, qué archivos tocó y cómo la probó.**

## 5. No verificado todavía

- Talk-over con la tarjeta real (BASS): el 30 % y la salida de 1,5 s pueden necesitar ajuste de oído. Los Tips sobre el intro
  también deben oírse con BASS: la suite usa el motor simulado.
- Fish Audio y Gemini TTS con claves reales (en la nube Fish estaba bloqueado; Gemini solo validó el formato de la llamada).
  «Gratuita» para Fish es [Probable] falso: se cobra por uso o por créditos.
- Micrófono: solo funciona con el Studio abierto como `http://localhost` (por IP de red local el navegador lo bloquea).
- Auto play de la Biblioteca en la PC: el arreglo `7af8041` aún no está ahí.
- Exactitud del BPM/intro/outro automático: probada solo con pistas sintéticas. Idea pendiente: marcar a mano 20 canciones
  reales y comparar; solo si falla en más de 5, evaluar `pleco-xa` (librería JS de navegador, un solo mantenedor) como sugerencia.

## 6. Pendiente (orden sugerido)

1. **Auto play de la Biblioteca** en la PC: aplicar a mano lo de `7af8041` (sección 3).
2. **Locutor IA**: integrar el panel que hoy solo funciona en AzuraCast. Código en
   `C:\Users\REDESSOCIALES\Desktop\SOLIS FM DIGITAL\SOLISFM\solis fm digital\LOCUTOR IA` (leerlo; no copiar claves).
   La voz se genera **antes** de salir al aire, nunca en el momento; mensajes de oyentes con aprobación previa.
3. Cartuchera: botón «Editar» como Soho (color y título = Sonnet; marca in/out y volumen al disparar = Opus).
4. RDS + encoder propio (falta marca/modelo y cómo recibe el texto: red, puerto COM o archivo).
5. Página de salud y **prueba de 72 h seguidas** (lo último antes de publicar).
6. Reporte de pautas pasadas al aire para el anunciante [Suposición: puede existir en `backend/api/reportes.py`; verificar].
7. Seguridad para acceso remoto (contraseñas, https) y restauración de respaldo probada de punta a punta.
8. Voice tracking de días futuros: hoy el programa vive en memoria unas horas; haría falta guardar la programación diaria.
9. Opcionales: análisis por lote en segundo plano; icono propio para Identificadores.

Estimación honesta para publicar: **~60–65 %** [Suposición]. Lo que falta es lo que decide si aguanta 24/7: prueba de 72 h,
encoder/RDS, salud, seguridad y copias.
