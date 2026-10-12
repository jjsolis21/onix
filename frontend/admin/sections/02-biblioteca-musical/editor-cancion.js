/**
 * editor-cancion.js — Ónix FM · Editor de canción (agregar / editar)
 *
 * Inspirado en el editor de Jazler SoHo, pensado para marcar con precisión:
 *   · Onda grande con zonas: silencio inicial, intro (zona para hablar),
 *     coro, outro y lo que queda después de la mezcla.
 *   · Seis marcas: Inicio, Intro, Inicio coro, Final coro, Outro, Mezcla.
 *     Cada una: « » mueve 0,1 s (Mayús = 1 s) Y EL AUDIO SALTA A ESE PUNTO y
 *     suena, para fijarla de oído; ▶ Escuchar (desde un poco antes);
 *     📍 Fijar aquí (donde va el audio); ✕ Quitar. También se arrastran.
 *   · Teclado: Espacio = play/pausa · 1–6 = elegir marca · ← → = mover la
 *     marca elegida (Mayús = 1 s) · F = fijar la elegida aquí · Ctrl+S = guardar.
 *   · Zoom de la onda, AutoCue (detecta inicio y mezcla), fade in / out,
 *     volumen propio de la canción (dB sobre la normalización).
 *   · Datos, carátula (archivo / etiqueta / quitar), tiempo de repetición y
 *     restricciones. Canción nueva: lee las etiquetas y avisa duplicados.
 *
 * API: GET /api/v1/biblioteca/{id}/ficha · PUT /api/v1/audios/{id}
 *      POST /api/v1/audios (nueva) · POST /api/v1/biblioteca/{id}/autocue
 *      POST|DELETE /api/v1/audios/{id}/caratula · POST /api/v1/biblioteca/etiquetas
 */
; (function () {
  'use strict';

  const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const fmt = s => {
    s = Math.max(0, +s || 0);
    return `${Math.floor(s / 60)}:${(s % 60).toFixed(1).padStart(4, '0')}`;
  };
  const toast = (m, t = 'info') => (typeof showToast === 'function' ? showToast(m, t) : console.info(m));

  // Marcas: clave en la BD, nombre, color, segundos de "antes" al escuchar
  const MARCAS = [
    { k: 'cue_inicio',      n: 'Inicio',      c: '#4fc3f7', antes: 0 },
    { k: 'cue_intro',       n: 'Intro',       c: '#ff6b6b', antes: 3 },
    { k: 'cue_inicio_coro', n: 'Inicio coro', c: '#52d273', antes: 0 },
    { k: 'cue_final_coro',  n: 'Final coro',  c: '#52d273', antes: 3 },
    { k: 'cue_outro',       n: 'Outro',       c: '#ffa94d', antes: 3 },
    { k: 'cue_mezcla',      n: 'Mezcla',      c: '#4dabf7', antes: 4 },
  ];
  const TIPOS = { Musica: 'Música', Efecto: 'Jingle / ID', Sweeper: 'Sweeper', Programa: 'Programa', Tips: 'Tips' };
  const PRIORIDAD = { 1: '▲ Alta', 2: '● Normal', 3: '▼ Baja' };

  let E = null;          // estado del editor abierto

  // ── Abrir ────────────────────────────────────────────────
  async function abrir(id = null, opciones = {}) {
    if (E) cerrar(true);
    const schema = await fetch('/api/v1/config/biblioteca/schema').then(r => r.json()).catch(() => ({ categorias: [] }));
    let f = { titulo: '', artista: '', tipo_audio: 'Musica', prioridad: 2, aprobada: 1 };
    if (id) {
      const r = await fetch(`/api/v1/biblioteca/${id}/ficha`);
      if (!r.ok) { toast('No se pudo abrir la canción', 'error'); return; }
      f = await r.json();
    }
    E = { id, f, schema, valores: Object.fromEntries(MARCAS.map(m => [m.k, +(f[m.k] || 0)])),
          sel: 'cue_intro', ws: null, rp: null, zonas: [], archivo: null, sucio: false, onGuardar: opciones.onGuardar };
    pintar();
    if (id) cargarOnda(`/api/v1/audios/${id}/stream`);
    document.addEventListener('keydown', teclado, true);
  }

  // ── Interfaz ─────────────────────────────────────────────
  function opcionesCat(campo, actual) {
    const c = (E.schema.categorias || []).find(x => x.nombre_interno === campo);
    const vals = (c?.valores || []).map(v => String(v.valor));
    const act = actual ? String(actual) : '';
    if (act && !vals.some(v => v.toUpperCase() === act.toUpperCase())) vals.push(act);
    return `<option value="">—</option>` + vals.map(v =>
      `<option value="${esc(v)}" ${v.toUpperCase() === act.toUpperCase() ? 'selected' : ''}>${esc(v)}</option>`).join('');
  }
  function etq(campo, def) {
    return (E.schema.categorias || []).find(x => x.nombre_interno === campo)?.etiqueta_visible || def;
  }

  function pintar() {
    const f = E.f, nueva = !E.id;
    const fondo = document.createElement('div');
    fondo.className = 'ec-fondo';
    fondo.innerHTML = `
    <div class="ec" role="dialog" aria-label="Editor de canción">
      <header class="ec-cab">
        <span class="ec-tapa-mini" id="ecTapaMini">${E.id ? `<img src="/api/v1/audios/${E.id}/caratula?t=${Date.now()}" onerror="this.remove()" alt="">` : ''}♪</span>
        <div class="ec-cab-tit"><b id="ecCabTit">${nueva ? 'Nueva canción' : `${esc(f.artista)} — ${esc(f.titulo)}`}</b>
          <small>${nueva ? 'Elige el archivo: los datos se leen de sus etiquetas' : (f.tipo_audio === 'Musica' && !f.aprobada ? '📥 En la Bandeja de entrada' : `#${E.id}`)}</small></div>
        <button class="ec-x" data-acc="cerrar" title="Cerrar (Esc)">✕</button>
      </header>
      <div class="ec-cuerpo">
        ${nueva ? `<section class="ec-sec"><h3>Archivo</h3>
          <label class="ec-zona" id="ecZona"><input type="file" id="ecArchivo" accept="audio/*,.mp3,.wav,.ogg,.flac,.m4a,.aac" hidden>
            <span id="ecZonaTxt">Arrastra aquí el archivo de audio o haz clic para elegirlo</span></label>
          <p class="ec-aviso" id="ecAvisoArchivo" hidden></p></section>` : ''}

        <section class="ec-sec ec-onda-sec">
          <div class="ec-barra">
            <button class="ec-play" data-acc="play" title="Reproducir / pausar (Espacio)">${ICO('play2')}</button>
            <button class="ec-btn" data-acc="stop" title="Parar y volver al inicio">${ICO('stop2')}</button>
            <span class="ec-tiempo"><b id="ecT">0:00.0</b> / <span id="ecDur">${fmt(f.duracion)}</span></span>
            <label class="ec-zoom">🔍 <input type="range" id="ecZoom" min="0" max="300" value="0" title="Zoom de la onda"></label>
            <button class="ec-btn" data-acc="autocue" ${nueva ? 'disabled title="Guarda primero la canción"' : 'title="Detectar inicio y mezcla automáticamente"'}>✨ AutoCue</button>
          </div>
          <div class="ec-onda" id="ecOnda"><p class="ec-onda-vacia">${nueva ? 'La onda aparece al elegir el archivo' : 'Cargando la onda…'}</p></div>
          <div class="ec-marcas" id="ecMarcas"></div>
          <div class="ec-ajustes">
            <label>Fade in <input type="range" id="ecFadeIn" min="0" max="5" step="0.1" value="${+f.fade_in || 0}"><b id="ecFadeInV">${(+f.fade_in || 0).toFixed(1)} s</b></label>
            <label>Fade out <input type="range" id="ecFadeOut" min="0" max="10" step="0.1" value="${+f.fade_out || 0}"><b id="ecFadeOutV">${(+f.fade_out || 0).toFixed(1)} s</b></label>
            <label title="Ajuste propio de esta canción, sumado a la normalización">Volumen <input type="range" id="ecGan" min="-12" max="6" step="0.5" value="${+f.ganancia_db || 0}"><b id="ecGanV">${(+f.ganancia_db || 0) > 0 ? '+' : ''}${(+f.ganancia_db || 0).toFixed(1)} dB</b></label>
          </div>
          <p class="ec-ayuda">Clic en la onda = ir a ese punto · Marca elegida: <b>← →</b> la mueven (Mayús = 1 s) y el audio salta ahí · <b>F</b> = fijarla donde va el audio · <b>1–6</b> elegir marca · <b>Espacio</b> play/pausa</p>
        </section>

        <div class="ec-dos">
          <section class="ec-sec"><h3>Datos</h3>
            <div class="ec-campos">
              <label class="ec-ancho">Artista <input id="ecArtista" list="ecArtistas" value="${esc(f.artista)}" autocomplete="off"><datalist id="ecArtistas"></datalist></label>
              <label class="ec-ancho">Título <input id="ecTitulo" value="${esc(f.titulo)}"></label>
              <label class="ec-ancho">Álbum <input id="ecAlbum" value="${esc(f.album || '')}"></label>
              <label>Año <input id="ecAnio" type="number" min="1900" max="2100" value="${esc(f.anio || '')}"></label>
              <label>BPM <input id="ecBpm" type="number" min="40" max="300" value="${esc(f.bpm || '')}"></label>
              <label>Tipo <select id="ecTipo">${Object.entries(TIPOS).map(([k, t]) => `<option value="${k}" ${f.tipo_audio === k ? 'selected' : ''}>${t}</option>`).join('')}</select></label>
              <label>${esc(etq('cat1', 'Género'))} <select id="ecCat1">${opcionesCat('cat1', f.cat1)}</select></label>
              <label>${esc(etq('cat2', 'Rotación'))} <select id="ecCat2">${opcionesCat('cat2', f.cat2)}</select></label>
              <label>${esc(etq('cat3', 'Subgénero'))} <select id="ecCat3">${opcionesCat('cat3', f.cat3)}</select></label>
              <label>Voz <select id="ecVoz">${opcionesCat('voz', f.voz)}</select></label>
              <label>Origen <select id="ecOrigen"><option value="">—</option>${['NACIONAL', 'INTERNACIONAL'].map(o => `<option ${String(f.origen || '').toUpperCase() === o ? 'selected' : ''}>${o}</option>`).join('')}</select></label>
              <label>Energía <select id="ecEnergia"><option value="">—</option>${[1, 2, 3, 4, 5].map(n => `<option value="${n}" ${+f.energia === n ? 'selected' : ''}>${n} · ${['', 'Lenta', 'Suave', 'Media', 'Movida', 'Rápida'][n]}</option>`).join('')}</select></label>
              <label>Prioridad <select id="ecPrioridad">${[1, 2, 3].map(n => `<option value="${n}" ${(+f.prioridad || 2) === n ? 'selected' : ''}>${PRIORIDAD[n]}</option>`).join('')}</select></label>
            </div>
          </section>
          <div class="ec-col">
            <section class="ec-sec"><h3>Carátula</h3>
              <div class="ec-tapa-fila">
                <div class="ec-tapa" id="ecTapa">${E.id ? `<img src="/api/v1/audios/${E.id}/caratula?t=${Date.now()}" onerror="this.remove()" alt="">` : ''}<span>♪</span></div>
                <div class="ec-tapa-btns">
                  <label class="ec-btn ${nueva ? 'ec-off' : ''}">🖼 Importar imagen<input type="file" id="ecTapaArchivo" accept="image/png,image/jpeg" hidden ${nueva ? 'disabled' : ''}></label>
                  <button class="ec-btn" data-acc="tapa-etiqueta" ${nueva ? 'disabled' : ''}>🏷 Leer de la etiqueta</button>
                  <button class="ec-btn" data-acc="tapa-quitar" ${nueva ? 'disabled' : ''}>✕ Quitar</button>
                </div>
              </div>
            </section>
            <section class="ec-sec"><h3>Bloqueo en automático</h3>
              <label class="ec-rep">No repetir hasta
                <input type="number" id="ecRepH" min="0" max="23" value="${(+f.repeticion_horas || 0) % 24 || ''}" placeholder="0"> horas
                <input type="number" id="ecRepD" min="0" max="30" value="${Math.floor((+f.repeticion_horas || 0) / 24) || ''}" placeholder="0"> días
              </label>
              <p class="ec-ayuda">Después de sonar, el automático no la vuelve a elegir hasta que pase ese tiempo (0 = la regla general).</p>
              ${nueva ? '' : `<button class="ec-btn" data-acc="restricciones">⏱ Horas, días y tope por día…</button>`}
            </section>
          </div>
        </div>
      </div>
      <footer class="ec-pie">
        <span class="ec-estado" id="ecEstado"></span>
        <button class="ec-btn" data-acc="cerrar">Cancelar</button>
        ${f.tipo_audio === 'Musica' && !f.aprobada && !nueva ? '<button class="ec-btn ec-ok" data-acc="guardar-aprobar">✓ Guardar y aprobar</button>' : ''}
        <button class="ec-btn ec-pri" data-acc="guardar">💾 Guardar (Ctrl+S)</button>
      </footer>
    </div>`;
    document.body.appendChild(fondo);
    E.raiz = fondo;
    pintarMarcas();
    enlazar();
    fetch('/api/v1/artistas?tipo=Musica').then(r => r.ok ? r.json() : []).then(l => {
      const dl = fondo.querySelector('#ecArtistas');
      if (dl) dl.innerHTML = l.map(a => `<option value="${esc(a.artista)}">`).join('');
    }).catch(() => { });
  }

  function pintarMarcas() {
    E.raiz.querySelector('#ecMarcas').innerHTML = MARCAS.map((m, i) => `
      <div class="ec-marca ${E.sel === m.k ? 'sel' : ''}" data-m="${m.k}" style="--mc:${m.c}">
        <div class="ec-marca-tit">${i + 1} · ${m.n}</div>
        <button class="ec-marcar" data-acc="fijar" title="Marca ${m.n} donde va sonando el audio (tecla F)">● MARCAR</button>
        <input class="ec-marca-v" data-acc="valor" value="${E.valores[m.k] ? fmt(E.valores[m.k]) : '—'}" title="Escribe m:ss.d o segundos">
        <div class="ec-marca-btns">
          <button data-acc="menos" title="Atrás 0,1 s (Mayús: 1 s) — el audio salta ahí">«</button>
          <button data-acc="mas" title="Adelante 0,1 s (Mayús: 1 s) — el audio salta ahí">»</button>
          <button data-acc="escuchar" title="Escuchar desde ${m.antes ? m.antes + ' s antes' : 'la marca'}">${ICO('play2')}</button>
          <button data-acc="quitar" title="Quitar la marca">✕</button>
        </div>
      </div>`).join('');
  }

  // ── Onda (WaveSurfer + Regions) ─────────────────────────
  function cargarOnda(url) {
    const cont = E.raiz.querySelector('#ecOnda');
    if (typeof WaveSurfer === 'undefined') { cont.innerHTML = '<p class="ec-onda-vacia">El editor de onda no está disponible</p>'; return; }
    try { E.ws?.destroy(); } catch (_) { }
    cont.innerHTML = '';
    const Regions = (typeof RegionsPlugin !== 'undefined' && RegionsPlugin) || WaveSurfer.Regions;
    E.rp = Regions ? Regions.create() : null;
    E.ws = WaveSurfer.create({
      container: cont, height: 170, waveColor: '#5b6372', progressColor: '#9aa3b2', cursorColor: '#ee9b00',
      cursorWidth: 2, normalize: true, autoScroll: true, autoCenter: true, dragToSeek: true,
      plugins: E.rp ? [E.rp] : [], url,
    });
    E.ws.on('ready', () => {
      E.dur = E.ws.getDuration();
      E.raiz.querySelector('#ecDur').textContent = fmt(E.dur);
      aplicarVolumen();
      dibujarMarcas();
    });
    E.ws.on('timeupdate', t => { const el = E.raiz?.querySelector('#ecT'); if (el) el.textContent = fmt(t); });
    E.ws.on('play', () => { const b = E.raiz?.querySelector('.ec-play'); if (b) b.innerHTML = ICO('pause2'); });
    E.ws.on('pause', () => { const b = E.raiz?.querySelector('.ec-play'); if (b) b.innerHTML = ICO('play2'); });
    E.ws.on('error', () => { cont.innerHTML = '<p class="ec-onda-vacia">No se pudo cargar el audio</p>'; });
    if (E.rp) {
      E.rp.on('region-updated', r => {
        const m = MARCAS.find(x => x.k === r.id);
        if (!m) return;
        E.valores[m.k] = +r.start.toFixed(2);
        E.sel = m.k; E.sucio = true;
        pintarMarcas(); dibujarZonas();
      });
    }
  }

  /** Marcas (líneas arrastrables) y zonas de color, como Jazler. */
  function dibujarMarcas() {
    if (!E.rp || !E.dur) return;
    E.rp.clearRegions();
    E.zonas = [];
    MARCAS.forEach(m => {
      const v = E.valores[m.k];
      if (!v && m.k !== 'cue_inicio') return;
      E.rp.addRegion({ id: m.k, start: Math.min(v || 0, E.dur), color: m.c, drag: true, resize: false, content: m.n });
    });
    dibujarZonas();
  }
  function dibujarZonas() {
    if (!E.rp || !E.dur) return;
    E.zonas.forEach(z => { try { z.remove(); } catch (_) { } });
    E.zonas = [];
    const v = E.valores, zona = (a, b, color) => {
      if (b > a + 0.05) E.zonas.push(E.rp.addRegion({ start: a, end: Math.min(b, E.dur), color, drag: false, resize: false }));
    };
    zona(0, v.cue_inicio, 'rgba(120, 20, 20, .45)');                                  // silencio que se salta
    if (v.cue_intro) zona(v.cue_inicio || 0, v.cue_intro, 'rgba(255, 107, 107, .22)'); // zona para hablar
    if (v.cue_inicio_coro && v.cue_final_coro) zona(v.cue_inicio_coro, v.cue_final_coro, 'rgba(82, 210, 115, .20)');
    if (v.cue_mezcla) zona(v.cue_mezcla, E.dur, 'rgba(77, 171, 247, .14)');            // ya entra la siguiente
  }

  function mover(k, delta) {
    const nuevo = Math.max(0, Math.min(E.dur || 1e6, +(E.valores[k] || 0) + delta));
    fijar(k, nuevo, true);
  }
  /** Pone la marca y, si `sonar`, el audio salta ahí y suena (precisión de oído). */
  function fijar(k, t, sonar = false) {
    E.valores[k] = +(+t).toFixed(2);
    E.sel = k; E.sucio = true;
    pintarMarcas(); dibujarMarcas();
    if (sonar && E.ws) { E.ws.setTime(E.valores[k]); if (!E.ws.isPlaying()) E.ws.play(); }
  }
  function escuchar(k) {
    if (!E.ws) return;
    const m = MARCAS.find(x => x.k === k);
    E.sel = k; pintarMarcas();
    E.ws.setTime(Math.max(0, (E.valores[k] || 0) - m.antes));
    E.ws.play();
  }
  function aplicarVolumen() {
    const db = +E.raiz.querySelector('#ecGan').value;
    try { E.ws?.setVolume(Math.min(1, Math.pow(10, db / 20))); } catch (_) { }
  }
  function leerTiempo(txt) {
    txt = String(txt).trim().replace(',', '.');
    if (!txt || txt === '—') return 0;
    const p = txt.split(':');
    const s = p.length === 2 ? (+p[0]) * 60 + (+p[1]) : +p[0];
    return Number.isFinite(s) && s >= 0 ? s : null;
  }

  // ── Eventos ──────────────────────────────────────────────
  function enlazar() {
    const R = E.raiz, $ = s => R.querySelector(s);
    R.addEventListener('click', async e => {
      const b = e.target.closest('[data-acc]');
      if (!b) { if (e.target === R) pedirCerrar(); return; }
      const acc = b.dataset.acc, marca = b.closest('.ec-marca')?.dataset.m;
      if (marca && acc !== 'valor') { E.sel = marca; }
      switch (acc) {
        case 'cerrar': pedirCerrar(); break;
        case 'play': if (E.ws) E.ws.playPause(); break;
        case 'stop': if (E.ws) { E.ws.stop(); } break;
        case 'menos': mover(marca, e.shiftKey ? -1 : -0.1); break;
        case 'mas': mover(marca, e.shiftKey ? 1 : 0.1); break;
        case 'escuchar': escuchar(marca); break;
        case 'fijar': if (E.ws) fijar(marca, E.ws.getCurrentTime()); else pintarMarcas(); break;
        case 'quitar': E.valores[marca] = 0; E.sucio = true; pintarMarcas(); dibujarMarcas(); break;
        case 'valor': E.sel = marca; break;
        case 'autocue': await autoCue(); break;
        case 'guardar': guardar(false); break;
        case 'guardar-aprobar': guardar(true); break;
        case 'tapa-etiqueta': tapa('etiqueta'); break;
        case 'tapa-quitar': tapa('quitar'); break;
        case 'restricciones': window.BibliotecaVista?.restricciones?.([E.id]); break;
      }
      if (marca) R.querySelectorAll('.ec-marca').forEach(x => x.classList.toggle('sel', x.dataset.m === E.sel));
    });
    R.querySelector('#ecMarcas').addEventListener('change', e => {
      const inp = e.target.closest('.ec-marca-v');
      if (!inp) return;
      const t = leerTiempo(inp.value);
      if (t === null) { toast('Escribe el tiempo como 1:23.4 o en segundos', 'error'); pintarMarcas(); return; }
      fijar(inp.closest('.ec-marca').dataset.m, t, true);
    });
    $('#ecZoom').addEventListener('input', e => { try { E.ws?.zoom(+e.target.value); } catch (_) { } });
    const slider = (id, out, f) => $(id).addEventListener('input', e => { $(out).textContent = f(+e.target.value); E.sucio = true; });
    slider('#ecFadeIn', '#ecFadeInV', v => `${v.toFixed(1)} s`);
    slider('#ecFadeOut', '#ecFadeOutV', v => `${v.toFixed(1)} s`);
    slider('#ecGan', '#ecGanV', v => `${v > 0 ? '+' : ''}${v.toFixed(1)} dB`);
    $('#ecGan').addEventListener('input', aplicarVolumen);
    R.querySelectorAll('.ec-campos input, .ec-campos select, #ecRepH, #ecRepD').forEach(el =>
      el.addEventListener('input', () => { E.sucio = true; }));
    $('#ecTapaArchivo')?.addEventListener('change', e => e.target.files[0] && tapa('archivo', e.target.files[0]));
    // Canción nueva: archivo
    const zona = $('#ecZona');
    if (zona) {
      $('#ecArchivo').addEventListener('change', e => e.target.files[0] && elegirArchivo(e.target.files[0]));
      zona.addEventListener('dragover', e => { e.preventDefault(); zona.classList.add('encima'); });
      zona.addEventListener('dragleave', () => zona.classList.remove('encima'));
      zona.addEventListener('drop', e => { e.preventDefault(); zona.classList.remove('encima'); e.dataTransfer.files[0] && elegirArchivo(e.dataTransfer.files[0]); });
    }
  }

  function teclado(e) {
    if (!E) return;
    const escribiendo = ['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName) &&
      !document.activeElement.matches('input[type=range]');
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') { e.preventDefault(); guardar(false); return; }
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); pedirCerrar(); return; }
    if (escribiendo) return;
    if (e.code === 'Space') { e.preventDefault(); E.ws?.playPause(); }
    else if (/^[1-6]$/.test(e.key)) { E.sel = MARCAS[+e.key - 1].k; pintarMarcas(); }
    else if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
      e.preventDefault();
      mover(E.sel, (e.key === 'ArrowLeft' ? -1 : 1) * (e.shiftKey ? 1 : 0.1));
    } else if (e.key.toLowerCase() === 'f' && E.ws) fijar(E.sel, E.ws.getCurrentTime());
    else return;
    e.stopPropagation();
  }

  // ── Acciones ─────────────────────────────────────────────
  async function autoCue() {
    const est = E.raiz.querySelector('#ecEstado');
    est.textContent = 'Analizando…';
    const r = await fetch(`/api/v1/biblioteca/${E.id}/autocue`, { method: 'POST' });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) { est.textContent = ''; toast(d.detail || 'No se pudo analizar', 'error'); return; }
    E.valores.cue_inicio = d.cue_inicio || 0;
    if (d.cue_mezcla) E.valores.cue_mezcla = d.cue_mezcla;
    E.sucio = true;
    pintarMarcas(); dibujarMarcas();
    est.textContent = `✨ Inicio ${fmt(d.cue_inicio)} · mezcla ${d.cue_mezcla ? fmt(d.cue_mezcla) : '—'} (sin guardar)`;
  }

  async function elegirArchivo(file) {
    E.archivo = file;
    const R = E.raiz;
    R.querySelector('#ecZonaTxt').textContent = `✓ ${file.name} · ${(file.size / 1048576).toFixed(1)} MB`;
    cargarOnda(URL.createObjectURL(file));
    try {
      const fd = new FormData();
      fd.append('file', file.slice(0, 2 * 1024 * 1024), file.name); fd.append('nombre', file.name);
      const t = await fetch('/api/v1/biblioteca/etiquetas', { method: 'POST', body: fd }).then(r => r.json());
      const poner = (sel, v) => { const el = R.querySelector(sel); if (el && v && !el.value) el.value = v; };
      poner('#ecArtista', t.artista); poner('#ecTitulo', t.titulo); poner('#ecAlbum', t.album);
      poner('#ecAnio', t.anio); poner('#ecBpm', t.bpm);
      R.querySelector('#ecCabTit').textContent = `${R.querySelector('#ecArtista').value} — ${R.querySelector('#ecTitulo').value}`;
      const av = R.querySelector('#ecAvisoArchivo'), d = t.duplicados?.[0];
      av.hidden = !d && t.origen_datos !== 'etiquetas';
      av.className = `ec-aviso ${d ? 'ec-aviso--error' : ''}`;
      av.textContent = d ? `⚠ Ya existe en la biblioteca: ${d.titulo} — ${d.artista} (#${d.id}). ¿Seguro que quieres cargarla otra vez?`
                         : '🏷 Datos leídos de las etiquetas del archivo';
    } catch (_) { /* sin etiquetas: se escribe a mano */ }
  }

  async function tapa(modo, archivo) {
    let r;
    if (modo === 'quitar') r = await fetch(`/api/v1/audios/${E.id}/caratula`, { method: 'DELETE' });
    else {
      const fd = new FormData();
      if (archivo) fd.append('file', archivo);
      r = await fetch(`/api/v1/audios/${E.id}/caratula`, { method: 'POST', body: archivo ? fd : undefined });
    }
    const d = await r.json().catch(() => ({}));
    if (!r.ok) { toast(modo === 'etiqueta' ? 'El archivo no trae carátula en su etiqueta' : (d.detail || 'No se pudo'), 'error'); }
    const img = `<img src="/api/v1/audios/${E.id}/caratula?t=${Date.now()}" onerror="this.remove()" alt="">`;
    E.raiz.querySelector('#ecTapa').innerHTML = (modo === 'quitar' ? '' : img) + '<span>♪</span>';
    E.raiz.querySelector('#ecTapaMini').innerHTML = (modo === 'quitar' ? '' : img) + '♪';
    window.BibliotecaVista?.recargar?.();
  }

  function datos() {
    const v = s => E.raiz.querySelector(s).value.trim();
    const horas = (+v('#ecRepH') || 0) + (+v('#ecRepD') || 0) * 24;
    const d = {
      titulo: v('#ecTitulo'), artista: v('#ecArtista'), album: v('#ecAlbum'),
      fecha_lanzamiento: v('#ecAnio'), bpm: v('#ecBpm') ? +v('#ecBpm') : null,
      tipo_audio: v('#ecTipo'), cat1: v('#ecCat1'), cat2: v('#ecCat2'), cat3: v('#ecCat3'), voz: v('#ecVoz'),
      origen: v('#ecOrigen') || null, energia: v('#ecEnergia') ? +v('#ecEnergia') : null, prioridad: +v('#ecPrioridad'),
      fade_in: +v('#ecFadeIn'), fade_out: +v('#ecFadeOut'), ganancia_db: +v('#ecGan'), repeticion_horas: horas,
    };
    MARCAS.forEach(m => { d[m.k] = E.valores[m.k] || 0; });
    return d;
  }

  async function guardar(aprobar) {
    const d = datos(), est = E.raiz.querySelector('#ecEstado');
    if (!d.titulo || !d.artista) { toast('Título y artista son obligatorios', 'error'); return; }
    if (!E.id && !E.archivo) { toast('Elige el archivo de audio', 'error'); return; }
    if (aprobar && (!d.cat1 || !d.cat2)) { toast('Para aprobarla necesita género y rotación', 'error'); return; }
    // PUT no acepta vacíos en categorías; null = no tocar
    const cuerpo = Object.fromEntries(Object.entries(d).filter(([, x]) => x !== '' && x !== null && x !== undefined));
    est.textContent = 'Guardando…';
    try {
      let id = E.id;
      if (!id) {
        const fd = new FormData();
        fd.append('file', E.archivo); fd.append('titulo', d.titulo); fd.append('artista', d.artista);
        fd.append('tipo_audio', d.tipo_audio);
        const creada = await subir(fd, pct => { est.textContent = `Subiendo… ${pct}%`; });
        id = creada.id;
      }
      const r = await fetch(`/api/v1/audios/${id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(cuerpo) });
      if (!r.ok) { const x = await r.json().catch(() => ({})); throw new Error(typeof x.detail === 'string' ? x.detail : (x.detail?.mensaje || `Error ${r.status}`)); }
      if (aprobar) await fetch('/api/v1/biblioteca/lote', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                                            body: JSON.stringify({ ids: [id], cambios: { aprobada: 1 } }) });
      E.sucio = false;
      toast(E.id ? `✓ Guardada: ${d.titulo}` : `✓ Cargada: ${d.titulo}${d.tipo_audio === 'Musica' ? ' (queda en la Bandeja hasta aprobarla)' : ''}`, 'success');
      const cb = E.onGuardar;
      cerrar(true);
      cb?.(id);
      window.BibliotecaVista?.recargar?.();
      if (typeof loadAudios === 'function') loadAudios();
    } catch (err) { est.textContent = ''; toast(err.message, 'error'); }
  }

  function subir(fd, progreso) {
    return new Promise((ok, mal) => {
      const x = new XMLHttpRequest();
      x.open('POST', '/api/v1/audios');
      x.upload.onprogress = e => e.lengthComputable && progreso(Math.round(e.loaded / e.total * 100));
      x.onload = () => {
        let j = {}; try { j = JSON.parse(x.responseText); } catch (_) { }
        if (x.status >= 200 && x.status < 300) ok(j.data || j);
        else mal(new Error(typeof j.detail === 'string' ? j.detail : (j.detail?.mensaje || `Error ${x.status}`)));
      };
      x.onerror = () => mal(new Error('Error de red al subir'));
      x.send(fd);
    });
  }

  function pedirCerrar() {
    if (E?.sucio && !confirm('Hay cambios sin guardar. ¿Cerrar igual?')) return;
    cerrar(true);
  }
  function cerrar() {
    if (!E) return;
    try { E.ws?.destroy(); } catch (_) { }
    E.raiz?.remove();
    document.removeEventListener('keydown', teclado, true);
    E = null;
  }

  window.EditorCancion = { abrir, cerrar };
})();
