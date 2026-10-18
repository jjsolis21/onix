# Tarea para Opus: «Tips» sobre el intro y puente entre canciones (estilo Jazler SoHo)

> Modelo recomendado: **Opus** (es motor de audio). Responde SIEMPRE en español, como asesor: primero la verdad incómoda, y marca cada afirmación
> como [Cierto] / [Probable] / [Suposición]. El dueño es de Solís FM Digital y pide: «copia el estilo de Jazler SoHo, no inventes nada».

## 0. Reglas que NO se negocian
1. **No hagas `git push`.** Commits solo locales, con esta línea al final del mensaje: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
2. **No cierres la tarea sin la suite completa en verde** (comando en §2). Hoy: **371 pruebas, 0 fallan**.
3. **NUNCA arranques `python backend/api/api_server.py` con el entorno normal**: apunta a la base REAL (`onix_db`) y al audio REAL (BASS) y puede
   sonar en el aire del dueño. Para probar pantallas usa la instancia aislada de §2. (Ya pasó un accidente así; no se repite.)
4. No toques datos de producción (`onix_db`) salvo que haga falta y con respaldo previo (`backend/api/respaldos.crear("motivo")`, se verifica solo).
5. El dueño todavía está en fase de desarrollo (no en el aire), pero quiere que el automatizador sea **robusto, sin fallos**.
6. Antes de decir «listo»: pruebas automáticas + verificar la pantalla en el navegador (herramientas `mcp__Claude_Browser__*`) si tocas frontend.
7. Géneros, categorías y rotaciones: **solo los que el usuario crea** (nada de adorno). No agregues valores precargados.

## 1. Qué hay que construir (palabras del dueño, fielmente)
> «En SoHo hay una función de un Tips: colocarlo para que suene **en el intro de la canción**, **al inicio o al final del intro**, o **hacer puente entre la
> canción que está sonando y la otra que va a sonar, para que quede mezclado**.»
> «Se programa **en el reloj**. También se debe mostrar **en la biblioteca que está en el Studio**, por si está en **modo manual**, para que el operador pueda
> configurar cómo suena un Tips.»

**Tres modos** (ejemplos del dueño, son la regla):
- **Inicio del intro:** el tip suena desde el segundo 0 de la canción.
- **Final del intro:** el tip termina justo cuando entra la voz. Canción con intro de **20 s** y tip de **7 s** ⇒ el tip empieza a los **13 s**
  (= intro − duración del tip).
- **Puente:** el tip se reparte entre las dos canciones. Tip de **8 s** ⇒ empieza **4 s antes** de que termine la canción que suena y los otros 4 s
  suenan ya sobre la canción siguiente (mitad y mitad, alrededor del punto de mezcla).

**Decisiones ya tomadas (no vuelvas a preguntar):**
- Canción **sin intro marcado** ⇒ el tip va al inicio.
- Tip **más largo que el intro** ⇒ se recorta con fundido para **no pisar la voz** (nunca debe tapar la entrada del cantante).
- Mientras suena el tip, la música baja (como el voice track: ver `VOZ_DUCK` / `voz_fondo_pct` / `_voz_duck`) y sube a pleno al terminar.
- Se configura **en la fila del reloj** (nueva opción para filas de tipo Tips) **y** en la biblioteca de Studio para modo manual.

## 2. Entorno (Windows, ruta del proyecto)
`C:\Users\REDESSOCIALES\Desktop\SOLIS FM DIGITAL\SOLISFM\ONIX` (Python 3.12, FastAPI, BASS para audio, **MariaDB**).
- Base real `onix_db`; base de pruebas `onix_test` (y `onix_test_gwN` por worker). Clave en `.env` (ignorado por git). **No imprimas la clave.**
- **Suite completa (≈100 s):** `python -m pytest -n 4 -p no:warnings --timeout=180 -rfE`  (`pytest.ini` ya trae `--dist loadfile`).
  Las pruebas del motor usan `MockBackend` (sin sonido; se verifican volúmenes). Cada archivo de pruebas empieza con la base limpia.
- **Instancia aislada para ver pantallas** (otro puerto, base de pruebas, audio simulado), en segundo plano:
  `ONIX_PORT=8099 DB_NAME=onix_test ONIX_AUDIO_BACKEND=mock RADIO_MEDIA_ROOT=<carpeta temporal> python backend/api/api_server.py` → `http://localhost:8099/admin/`.
  Al terminar: detén **solo ese proceso** (el del puerto 8099); NO mates otros `python.exe` (el servidor del dueño y su `engine_service` corren aparte).
  Las pruebas y esa instancia comparten `onix_test`: correr pytest la reinicia (hay que volver a cargar datos de prueba).
- Windows/bash: evita bloques gigantes con muchas comillas en `python - <<EOF`; escribe el script en un archivo y ejecútalo.
- Frontend: el navegador revalida JS/CSS/HTML (middleware `pantalla_siempre_al_dia`); aun así **sube el `?v=`** de los archivos que cambies
  (`frontend/admin/index.html`, `biblioteca-musical.html`, etc.).

## 3. Dónde mirar en el código (verifica los nombres antes de apoyarte en ellos)
- `backend/audio/audio_engine.py`: `AudioEngine._start` (arranque de cada ítem; ramas `sobre_musica`, `outgoing_active`, arranque en frío),
  `_fade_times` y `_es_corto` / `ENTRADA_CORTO` (cuñas, jingles, sweepers y tips **entran a pleno volumen**: no lo rompas),
  `_cama` / `_vigilar_cama` / `_recuperar_cama` / `_soltar_cama` (música bajo una voz), evento `voz_sobre_musica`, `VOZ_BAJAR_MS`, `_voz_duck`,
  `_mix_point` (dónde arranca la siguiente), `_cue_range`, `_do_effect` / `_vigilar_efectos` (canales de efectos de la cartuchera), `_siguiente_es_voz`.
  El intro de una canción viene de `cue_intro` (o `intro`) en `audios`.
- `backend/audio/planner.py`: `TIPOS_RELOJ` ya incluye `Tips`; `Planner.extender` arma el plan por hora (tandas, eventos, filas del reloj);
  ojo con la regla acordada: **solo una tanda «inmediata» corta la canción; «al cierre» sin inmediata espera a que termine**.
- Relojes: `backend/api/relojes.py` y `frontend/admin/sections/08-relojes/` (filas del reloj: tabla `reloj_filas`).
- Studio: `frontend/studio/` (`studio.js`, biblioteca del Studio, programa/plan, cola del operador, cartuchera).
- Esquema nuevo ⇒ **`backend/migraciones_maria.py`** (migraciones idempotentes que corren al arrancar y en las pruebas) y luego regenerar con
  `python scripts/generar_esquema_maria.py` (actualiza `backend/esquema_maria.sql` y `semilla_maria.json`). NO uses `scripts/init_db.py` (SQLite histórico).
- SQL: solo dialecto **MariaDB** (`NOW()`, `DATE_SUB`, `CONCAT`…; en MariaDB `||` es «O», no concatena). Marcadores `?` y `:nombre` sí valen.
- Otros pendientes (NO son de esta tarea): chequeo de integridad de archivos (#7), categoría «Propiedades» (#13), ver `docs/estado-onix.md`.

## 4. Método sugerido
1. Lee primero el motor (`_start`, `_vigilar_cama`, `_mix_point`) y el reloj; dibuja el diseño en pocas líneas (qué dato nuevo se guarda y dónde:
   p. ej. un campo del tip en la fila del reloj + parámetros del operador en modo manual). Solo pregunta al dueño si algo **bloquea**.
2. Modelo de tiempos: calcula el instante de inicio del tip según el modo, con las **marcas reales** de la canción (`cue_intro`, `cue_inicio`, mezcla)
   y la **duración real** del tip. Casos límite: sin intro, tip > intro, tip > canción, canción corta, primera canción tras una tanda, pausa/stop/seek,
   modo manual vs automático, voice track del Locutor IA en la misma canción, cambio de hora con tanda al cierre.
3. Motor primero, con pruebas (volúmenes y tiempos con `MockBackend`); después planner/reloj/API; después pantallas (reloj y biblioteca del Studio).
4. Pruebas nuevas por cada modo y por cada caso límite (que fallen sin el código nuevo).
5. Verifica en el navegador con la instancia aislada y, si puedes, mide volúmenes reales con BASS en una prueba corta y controlada (sin tocar el aire).
6. Actualiza `docs/estado-onix.md` (punto 8) y comitea localmente con mensaje claro.

## 5. Criterios de «terminado»
- Los tres modos funcionan con los ejemplos del dueño (intro 20 s + tip 7 s ⇒ 13 s; tip 8 s en puente ⇒ 4 s + 4 s), cubiertos por pruebas.
- El tip nunca pisa la voz del cantante y la música recupera su volumen después del tip.
- Se configura en el reloj **y** en la biblioteca del Studio (modo manual), y se ve en el plan/Studio qué tip entrará y cuándo.
- Suite completa en verde, sin romper lo existente (cuñas a pleno volumen, tandas, voice track, control del aire).
- Informe final al dueño en español: qué hiciste, qué probaste, qué riesgos quedan y qué decisión necesitas de él.
