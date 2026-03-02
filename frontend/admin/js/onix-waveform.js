/**
 * onix-waveform.js — Módulo de Editor Visual de Frecuencia para Ónix FM
 * =========================================================================
 * Integra WaveSurfer.js v7 en el modal de carga de biblioteca-musical.html.
 *
 * RESPONSABILIDADES DE ESTE MÓDULO:
 *   1. Inicializar WaveSurfer + plugin Regions con la identidad visual de Ónix FM.
 *   2. Cargar la onda cuando el DJ arrastra un archivo al dropzone.
 *   3. Gestionar las tres regiones de marcadores (intro, outro, hook).
 *   4. Sincronizar los inputs numéricos con los marcadores en tiempo real.
 *   5. Actualizar el contador de tiempo durante la pre-escucha.
 *   6. Exponer la función `wfDestroy()` para limpiar al cerrar el modal.
 *
 * INTEGRACIÓN EN admin-app.js:
 *   - Importar (o incluir antes que admin-app.js con <script>).
 *   - Llamar a `wfInit()` una sola vez al arrancar la app.
 *   - Llamar a `wfLoadFile(file)` cuando el dropzone reciba un archivo.
 *   - Llamar a `wfDestroy()` cuando el modal se cierre.
 *
 * DEPENDENCIAS (cargar antes de este archivo):
 *   <script src="https://unpkg.com/wavesurfer.js@7/dist/wavesurfer.min.js"></script>
 *   <script src="https://unpkg.com/wavesurfer.js@7/dist/plugins/regions.min.js"></script>
 * =========================================================================
 */

// ── Constantes de identidad visual Ónix FM ──────────────────────────────────

const WF_COLORS = {
  waveform:        '#ff6600',   // naranja principal de la onda
  waveformFaded:   '#ff660066', // onda con opacidad (parte "ya reproducida")
  progress:        '#ff8533',   // barra de progreso
  cursor:          '#ffffff',   // línea del cursor de posición
  intro:           'rgba(0, 230, 118, 0.25)',   // verde translúcido → región intro
  introBorder:     'rgba(0, 230, 118, 0.90)',   // borde sólido del marcador intro
  outro:           'rgba(255, 61, 61, 0.20)',   // rojo translúcido → región outro
  outroBorder:     'rgba(255, 61, 61, 0.90)',   // borde sólido del marcador outro
  hook:            'rgba(199, 146, 234, 0.20)', // violeta translúcido → región hook
  hookBorder:      'rgba(199, 146, 234, 0.90)', // borde sólido del marcador hook
};

// ── Estado interno del módulo ────────────────────────────────────────────────

let _wavesurfer  = null;  // instancia principal de WaveSurfer
let _regionsPlugin = null; // instancia del plugin Regions
let _regions     = {};    // { intro: RegionObject, outro: RegionObject, hook: RegionObject }
let _duration    = 0;     // duración total de la pista en segundos
let _rafId       = null;  // requestAnimationFrame para el contador de tiempo
let _isPlaying   = false; // estado de reproducción para alternar el icono

// ── Referencias a elementos del DOM ─────────────────────────────────────────

const DOM = {
  panel:       () => document.getElementById('waveform-panel'),
  container:   () => document.getElementById('waveform'),
  loading:     () => document.getElementById('wf-loading'),
  filename:    () => document.getElementById('wf-filename'),
  timeDisplay: () => document.getElementById('wf-time-display'),
  btnPlay:     () => document.getElementById('wf-btn-play'),
  btnReset:    () => document.getElementById('wf-btn-reset'),
  playIcon:    () => document.getElementById('wf-play-icon'),
  inputIntro:  () => document.getElementById('input-intro'),
  inputOutro:  () => document.getElementById('input-outro'),
  inputHook:   () => document.getElementById('input-hook'),
  setButtons:  () => document.querySelectorAll('.wf-set-btn'),
};

// ── Utilidades ───────────────────────────────────────────────────────────────

/**
 * Formatea segundos a MM:SS para el display del contador.
 * @param {number} secs
 * @returns {string} ej. "3:45"
 */
function _formatTime(secs) {
  if (!secs || isNaN(secs)) return '0:00';
  const m = Math.floor(secs / 60);
  const s = Math.floor(secs % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}

/**
 * Redondea a 1 decimal para evitar valores tipo 12.300000000002 en los inputs.
 */
function _round1(n) {
  return Math.round(n * 10) / 10;
}

/**
 * Actualiza el contador de tiempo en el header del panel.
 * Se llama repetidamente vía requestAnimationFrame mientras reproduce.
 */
function _tickTimeDisplay() {
  if (!_wavesurfer) return;
  const current = _wavesurfer.getCurrentTime();
  const total   = _duration || _wavesurfer.getDuration() || 0;
  const display = DOM.timeDisplay();
  if (display) {
    display.textContent = `${_formatTime(current)} / ${_formatTime(total)}`;
  }
  if (_isPlaying) {
    _rafId = requestAnimationFrame(_tickTimeDisplay);
  }
}

/**
 * Cambia el icono del botón entre ▶ (play) y ⏸ (pausa).
 * @param {boolean} playing - true = mostrar icono de pausa
 */
function _updatePlayIcon(playing) {
  const icon = DOM.playIcon();
  if (!icon) return;
  const btn  = DOM.btnPlay();
  if (playing) {
    // Icono de pausa: dos rectángulos
    icon.innerHTML = `
      <rect x="5"  y="3" width="4" height="18" rx="1"/>
      <rect x="15" y="3" width="4" height="18" rx="1"/>
    `;
    btn && btn.setAttribute('title', 'Pausa');
  } else {
    // Icono de play: triángulo
    icon.innerHTML = `<polygon points="5,3 19,12 5,21"/>`;
    btn && btn.setAttribute('title', 'Play (pre-escucha)');
  }
}

// ── Gestión de regiones ──────────────────────────────────────────────────────

/**
 * Crea las tres regiones (intro, outro, hook) con posiciones inteligentes
 * basadas en la duración de la pista.
 *
 * Lógica de posicionamiento automático:
 *   - Intro:  primeros 8 segundos (o 10% de la duración si la canción es corta)
 *   - Outro:  30 segundos antes del final (estándar para crossfade en radio)
 *   - Hook:   al 40% de la canción (suele coincidir con el primer estribillo)
 *
 * El DJ puede arrastrar cualquier marcador después.
 */
function _createDefaultRegions() {
  if (!_regionsPlugin || !_duration) return;

  // Limpiar regiones anteriores antes de crear nuevas
  _regionsPlugin.clearRegions();
  _regions = {};

  const dur = _duration;

  // Calcular posiciones por defecto
  const introStart = 0;
  const introEnd   = _round1(Math.min(8, dur * 0.10));
  const outroStart = _round1(Math.max(0, dur - 30));
  const outroEnd   = _round1(Math.max(outroStart + 3, dur - 2));
  const hookStart  = _round1(dur * 0.40);
  const hookEnd    = _round1(dur * 0.40 + 5);

  // Crear región INTRO (verde)
  _regions.intro = _regionsPlugin.addRegion({
    start:   introStart,
    end:     introEnd,
    color:   WF_COLORS.intro,
    drag:    true,       // el DJ puede moverla arrastrando
    resize:  true,       // y ajustar sus bordes
    id:      'intro',
  });

  // Crear región OUTRO (rojo)
  _regions.outro = _regionsPlugin.addRegion({
    start:   outroStart,
    end:     outroEnd,
    color:   WF_COLORS.outro,
    drag:    true,
    resize:  true,
    id:      'outro',
  });

  // Crear región HOOK (violeta)
  _regions.hook = _regionsPlugin.addRegion({
    start:   hookStart,
    end:     hookEnd,
    color:   WF_COLORS.hook,
    drag:    true,
    resize:  true,
    id:      'hook',
  });

  // Escribir los valores iniciales en los inputs numéricos
  _syncInputsFromRegions();
}

/**
 * Copia los valores .start de cada región a sus inputs numéricos respectivos.
 * Se llama tanto al crear las regiones como al arrastrarlas.
 */
function _syncInputsFromRegions() {
  const inputIntro = DOM.inputIntro();
  const inputOutro = DOM.inputOutro();
  const inputHook  = DOM.inputHook();

  if (_regions.intro && inputIntro) {
    inputIntro.value = _round1(_regions.intro.start);
  }
  if (_regions.outro && inputOutro) {
    inputOutro.value = _round1(_regions.outro.start);
  }
  if (_regions.hook && inputHook) {
    inputHook.value = _round1(_regions.hook.start);
  }
}

/**
 * Mueve la región de un marcador específico a una nueva posición temporal.
 * Preserva la anchura de la región (diferencia end - start).
 *
 * @param {string} regionId  - 'intro' | 'outro' | 'hook'
 * @param {number} newStart  - nuevo tiempo de inicio en segundos
 */
function _moveRegionTo(regionId, newStart) {
  const region = _regions[regionId];
  if (!region) return;

  const width = region.end - region.start;
  const start = Math.max(0, Math.min(newStart, _duration - width));
  const end   = Math.min(_duration, start + width);

  // WaveSurfer v7: setOptions actualiza start/end sin recrear la región
  region.setOptions({ start, end });
}

// ── Inicialización ───────────────────────────────────────────────────────────

/**
 * Inicializa WaveSurfer y el plugin Regions.
 * Debe llamarse UNA SOLA VEZ al montar la página/módulo.
 *
 * Razón de usar WaveSurfer.create() en lugar de `new WaveSurfer()`:
 * WaveSurfer v7 recomienda el método estático create() para garantizar
 * que los plugins estén registrados antes de que el constructor termine.
 */
function wfInit() {
  // Verificar que las librerías están cargadas.
  // Si WaveSurfer no existe en window, el script UMD no se cargó correctamente.
  if (typeof WaveSurfer === 'undefined') {
    console.error(
      '[Ónix WF] WaveSurfer no encontrado. ' +
      'Asegúrate de cargar wavesurfer.min.js antes de este archivo.'
    );
    return;
  }

  // Crear el plugin de regiones antes de la instancia principal.
  // En v7, los plugins se crean y pasan como array en la configuración.
  _regionsPlugin = WaveSurfer.Regions
    ? WaveSurfer.Regions.create()          // build UMD
    : window.WaveSurferRegions?.create();   // fallback por si el nombre varía

  if (!_regionsPlugin) {
    console.error(
      '[Ónix WF] Plugin Regions no encontrado. ' +
      'Carga regions.min.js antes de este archivo.'
    );
    return;
  }

  _wavesurfer = WaveSurfer.create({
    container:         '#waveform',    // selector del div contenedor
    waveColor:         WF_COLORS.waveform,
    progressColor:     WF_COLORS.progress,
    cursorColor:       WF_COLORS.cursor,
    cursorWidth:       1,
    height:            128,            // altura en px del canvas de onda (estándar Ónix FM)
    barWidth:          2,              // ancho de cada barra vertical
    barGap:            1,              // espacio entre barras
    barRadius:         2,              // bordes redondeados en las barras
    normalize:         true,           // normalizar amplitud para mejor visualización
    interact:          true,           // habilitar clic para saltar de posición
    plugins:           [_regionsPlugin],
  });

  // ── Eventos de WaveSurfer ──────────────────────────────────────────────────

  // 'ready': la onda está pintada y el audio está listo para reproducirse.
  // Aquí es cuando calculamos la duración y creamos las regiones.
  _wavesurfer.on('ready', () => {
    _duration = _wavesurfer.getDuration();

    // Ocultar el overlay de carga con transición suave
    const loading = DOM.loading();
    if (loading) loading.classList.add('hidden');

    // Actualizar display inicial de duración
    const display = DOM.timeDisplay();
    if (display) display.textContent = `0:00 / ${_formatTime(_duration)}`;

    // Crear las regiones con posiciones inteligentes
    _createDefaultRegions();

    console.info(`[Ónix WF] Listo. Duración: ${_formatTime(_duration)} (${_duration.toFixed(2)}s)`);
  });

  // 'play' y 'pause': mantener sincronizado el estado interno y el icono
  _wavesurfer.on('play', () => {
    _isPlaying = true;
    _updatePlayIcon(true);
    _rafId = requestAnimationFrame(_tickTimeDisplay);
  });

  _wavesurfer.on('pause', () => {
    _isPlaying = false;
    _updatePlayIcon(false);
    if (_rafId) {
      cancelAnimationFrame(_rafId);
      _rafId = null;
    }
  });

  // 'finish': el audio llegó al final
  _wavesurfer.on('finish', () => {
    _isPlaying = false;
    _updatePlayIcon(false);
    if (_rafId) {
      cancelAnimationFrame(_rafId);
      _rafId = null;
    }
    // Actualizar el display una última vez para mostrar el tiempo total
    const display = DOM.timeDisplay();
    if (display) display.textContent = `${_formatTime(_duration)} / ${_formatTime(_duration)}`;
  });

  // ── Eventos del plugin Regions ─────────────────────────────────────────────

  // 'region-updated': se dispara mientras el DJ arrastra o redimensiona una región.
  // Aquí sincronizamos el input numérico correspondiente en tiempo real.
  _regionsPlugin.on('region-updated', (region) => {
    const input = {
      intro: DOM.inputIntro,
      outro: DOM.inputOutro,
      hook:  DOM.inputHook,
    }[region.id];

    if (input && input()) {
      input().value = _round1(region.start);
    }
  });

  // ── Listeners de los controles del panel ───────────────────────────────────
  //
  // RAZÓN DE USAR DELEGACIÓN EN document EN LUGAR DE addEventListener DIRECTO:
  // El Shell inyecta el HTML del panel dinámicamente vía fetch()+innerHTML.
  // Cuando wfInit() corre, DOM.btnPlay() devuelve null porque el panel aún
  // no existe en el DOM. Un addEventListener sobre null se ignora silenciosamente
  // y el botón nunca responde. Con delegación en document, el listener existe
  // desde el inicio y captura todos los clicks que burbujean hasta document,
  // sin importar cuándo se crearon los nodos origen.
  //
  // Se registra un segundo listener de 'click' para no interferir con el
  // listener existente que puede haber en el código que consuma este módulo.
  document.addEventListener('click', function _wfClickHandler(e) {

    // Botón ▶/⏸ Play/Pausa del editor de onda
    if (e.target.closest('#wf-btn-play')) {
      if (_wavesurfer) _wavesurfer.playPause();
      return;
    }

    // Botón de reset de regiones: las regenera en sus posiciones automáticas
    if (e.target.closest('#wf-btn-reset')) {
      if (_duration > 0) _createDefaultRegions();
      return;
    }

    // Botones "Marcar posición actual" — data-region="intro|outro|hook"
    const setBtn = e.target.closest('.wf-set-btn');
    if (setBtn) {
      if (!_wavesurfer) return;
      const regionId   = setBtn.dataset.region;       // 'intro' | 'outro' | 'hook'
      const currentPos = _wavesurfer.getCurrentTime();

      // Mover la región al tiempo actual del cursor
      _moveRegionTo(regionId, currentPos);

      // Y actualizar el input numérico directamente
      const inputMap = {
        intro: DOM.inputIntro,
        outro: DOM.inputOutro,
        hook:  DOM.inputHook,
      };
      const inputFn = inputMap[regionId];
      if (inputFn && inputFn()) {
        inputFn().value = _round1(currentPos);
      }
      return;
    }

  }); // fin _wfClickHandler

  // Sincronización inversa: si el DJ escribe un número en el input,
  // mover la región de la onda para reflejarlo visualmente.
  // También usa delegación en document para el mismo motivo que arriba.
  document.addEventListener('change', function _wfChangeHandler(e) {
    const inputIdMap = {
      'input-intro': 'intro',
      'input-outro': 'outro',
      'input-hook':  'hook',
    };
    const regionId = inputIdMap[e.target.id];
    if (!regionId) return;
    const val = parseFloat(e.target.value);
    if (!isNaN(val) && val >= 0) {
      _moveRegionTo(regionId, val);
    }
  }); // fin _wfChangeHandler

  console.info('[Ónix WF] Módulo WaveSurfer inicializado correctamente.');
}

// ── API pública ──────────────────────────────────────────────────────────────

/**
 * Carga un archivo de audio en la onda.
 * Debe llamarse cada vez que el dropzone recibe un nuevo archivo.
 *
 * INTEGRACIÓN en admin-app.js:
 *   En el listener de tu dropzone, donde ya tienes:
 *     selectedFile = file;
 *   Añade justo después:
 *     wfLoadFile(file);
 *
 * @param {File} file - El objeto File del input o drag-and-drop
 */
function wfLoadFile(file) {
  if (!_wavesurfer) {
    console.warn('[Ónix WF] wfLoadFile llamado antes de wfInit(). Inicializando ahora...');
    wfInit();
  }

  // Mostrar el panel (que arranca oculto)
  const panel = DOM.panel();
  if (panel) panel.style.display = 'block';

  // Mostrar el nombre del archivo en el header
  const fnEl = document.getElementById('wf-filename');
  if (fnEl) fnEl.textContent = file.name;

  // Mostrar overlay de carga mientras WaveSurfer decodifica
  const loading = DOM.loading();
  if (loading) loading.classList.remove('hidden');

  // Detener reproducción previa si la hay
  if (_isPlaying) _wavesurfer.stop();

  // Resetear estado
  _duration  = 0;
  _isPlaying = false;
  _regions   = {};
  _updatePlayIcon(false);

  const display = DOM.timeDisplay();
  if (display) display.textContent = '0:00 / 0:00';

  // Crear URL de objeto para el archivo local.
  // WaveSurfer v7 acepta directamente un Blob/File a través de loadBlob().
  // Esto evita subir el archivo al servidor solo para previsualizar.
  _wavesurfer.loadBlob(file);
}

/**
 * Destruye la instancia de WaveSurfer y limpia el estado.
 * Llamar cuando el modal se cierra para liberar memoria y el contexto de audio.
 *
 * INTEGRACIÓN en admin-app.js:
 *   En el handler de cierre del modal (botón × o ESC), añade:
 *     wfDestroy();
 */
function wfDestroy() {
  if (_rafId) {
    cancelAnimationFrame(_rafId);
    _rafId = null;
  }

  if (_wavesurfer) {
    try {
      _wavesurfer.destroy();
    } catch (e) {
      // Silenciar errores al destruir (puede ocurrir si el elemento ya no existe en DOM)
    }
    _wavesurfer   = null;
    _regionsPlugin = null;
    _regions      = {};
    _duration     = 0;
    _isPlaying    = false;
  }

  // Ocultar el panel y limpiar inputs
  const panel = DOM.panel();
  if (panel) panel.style.display = 'none';

  [DOM.inputIntro, DOM.inputOutro, DOM.inputHook].forEach(fn => {
    const el = fn();
    if (el) el.value = '';
  });

  const display = DOM.timeDisplay();
  if (display) display.textContent = '0:00 / 0:00';

  console.info('[Ónix WF] Instancia destruida y panel ocultado.');
}

/**
 * Devuelve los valores actuales de los marcadores como objeto plano.
 * Útil si quieres leer los valores desde admin-app.js antes del submit.
 *
 * @returns {{ intro: number|null, outro: number|null, hook: number|null }}
 */
function wfGetMarkers() {
  return {
    intro: _regions.intro ? _round1(_regions.intro.start) : null,
    outro: _regions.outro ? _round1(_regions.outro.start) : null,
    hook:  _regions.hook  ? _round1(_regions.hook.start)  : null,
  };
}


// =============================================================================
// GUÍA DE INTEGRACIÓN EN admin-app.js
// =============================================================================
//
// ── PASO 1: Inicializar al cargar la página ───────────────────────────────────
//
//   // Al final del DOMContentLoaded listener, o donde ya tienes tu init:
//   wfInit();
//
//
// ── PASO 2: Cargar la onda cuando el dropzone recibe un archivo ───────────────
//
//   Busca en admin-app.js donde asignas `selectedFile`.
//   Típicamente es algo como:
//
//     dropzone.addEventListener('drop', (e) => {
//       e.preventDefault();
//       const file = e.dataTransfer.files[0];
//       selectedFile = file;
//       showFilePreview(file);          // ← tu código actual
//       wfLoadFile(file);               // ← AÑADIR ESTA LÍNEA
//     });
//
//   O si usas un <input type="file">:
//
//     fileInput.addEventListener('change', (e) => {
//       selectedFile = e.target.files[0];
//       wfLoadFile(selectedFile);       // ← AÑADIR ESTA LÍNEA
//     });
//
//
// ── PASO 3: Destruir al cerrar el modal ──────────────────────────────────────
//
//   Busca donde cierras/reseteas el modal. Añade:
//
//     function closeUploadModal() {
//       // ... tu código de cierre existente ...
//       wfDestroy();                    // ← AÑADIR
//     }
//
//
// ── PASO 4: Los valores se envían solos en el FormData ───────────────────────
//
//   Los inputs #input-intro, #input-outro, #input-hook tienen name="intro",
//   name="outro", name="hook". Si tu FormData se construye así:
//
//     const fd = new FormData(document.getElementById('mi-form'));
//
//   ...los valores se incluyen automáticamente. Si lo construyes manualmente:
//
//     const fd = new FormData();
//     fd.append('titulo',   document.getElementById('input-titulo').value);
//     // ...otros campos...
//
//   Entonces añade manualmente:
//
//     const markers = wfGetMarkers();
//     if (markers.intro !== null) fd.append('intro', markers.intro);
//     if (markers.outro !== null) fd.append('outro', markers.outro);
//     if (markers.hook  !== null) fd.append('hook',  markers.hook);
//
//   El endpoint POST /api/v1/audios ya espera estos campos opcionales:
//     intro: Optional[float] = Form(None)
//     outro: Optional[float] = Form(None)
//     hook:  Optional[float] = Form(None)
//
// =============================================================================
