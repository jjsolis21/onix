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

  // Regla "volver a sonar cada…": se guarda en horas; se muestra en la unidad más grande que la divide exacta
  const REP_UNIDADES = [{ n: 'horas', h: 1 }, { n: 'días', h: 24 }, { n: 'semanas', h: 168 }, { n: 'meses (30 d)', h: 720 }];
  function repInicial(horas) {
    horas = +horas || 0;
    const u = [...REP_UNIDADES].reverse().find(x => horas && horas % x.h === 0) || REP_UNIDADES[0];
    return { n: horas / u.h, h: u.h };
  }

  // Marcas: clave en la BD, nombre, color, segundos de "antes" al escuchar
  const MARCAS = [
    { k: 'cue_inicio',      n: 'Inicio',      c: '#4fc3f7', antes: 0 },
    { k: 'cue_intro',       n: 'Intro',       c: '#ff6b6b', antes: 0 },
    { k: 'cue_inicio_coro', n: 'Inicio coro', c: '#52d273', antes: 0 },
    { k: 'cue_final_coro',  n: 'Final coro',  c: '#52d273', antes: 0 },
    { k: 'cue_outro',       n: 'Outro',       c: '#ffa94d', antes: 0 },
    { k: 'cue_mezcla',      n: 'Mezcla',      c: '#4dabf7', antes: 0 },
  ];
  const TIPOS = { Musica: 'Música', Efecto: 'Jingle / ID', Sweeper: 'Sweeper', Programa: 'Programa', Tips: 'Tips', 'Cuña': 'Comercial / Cuña' };
  // Un comercial muestra solo lo que usa (como el editor de Soho): inicio, intro, outro y mezcla
  const MARCAS_CUNA = ['cue_inicio', 'cue_intro', 'cue_outro', 'cue_mezcla'];
  const PRIORIDAD = { 1: '▲ Alta', 2: '● Normal', 3: '▼ Baja' };

  let E = null;          // estado del editor abierto
  const marcasDe = () => (E && E.marcasActivas) || MARCAS;   // en una pauta solo se muestran las que usa

  // ── Abrir ────────────────────────────────────────────────
  async function abrir(id = null, opciones = {}) {
    if (E) cerrar(true);
    const schema = await fetch('/api/v1/config/biblioteca/schema').then(r => r.json()).catch(() => ({ categorias: [] }));
    let f = { titulo: '', artista: opciones.artista || '', tipo_audio: opciones.tipo || 'Musica', prioridad: 2, aprobada: 1 };
    // Categorías de jingles, sweepers y tips (Admin › Identificadores)
    const ident = await fetch('/api/v1/ident/categorias').then(r => r.ok ? r.json() : {}).catch(() => ({}));
    if (id) {
      const r = await fetch(`/api/v1/biblioteca/${id}/ficha`);
      if (!r.ok) { toast('No se pudo abrir la canción', 'error'); return; }
      f = await r.json();
    }
    // Desde la Biblioteca solo música y programas; Identificadores pasa jingles, sweepers y tips
    const tipos = opciones.tipos || ['Musica', 'Programa'];
    E = { id, f, schema, ident, tipos: tipos.includes(f.tipo_audio) ? tipos : [...tipos, f.tipo_audio], valores: Object.fromEntries(MARCAS.map(m => [m.k, +(f[m.k] || 0)])),
          sel: 'cue_intro', ws: null, rp: null, zonas: [], archivo: null, sucio: false, onGuardar: opciones.onGuardar,
          onCerrar: opciones.onCerrar,
          marcasActivas: f.tipo_audio === 'Cuña' ? MARCAS.filter(m => MARCAS_CUNA.includes(m.k)) : undefined };
    pintar();
    if (!id && opciones.archivo) elegirArchivo(opciones.archivo);
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

  /** Sección de la onda: reproductor, onda con marcas, fundidos y volumen. Es la misma en la ventana de canción
   *  y embebida en otros lugares (p. ej. Audio y versiones de una pauta). */
  function htmlOnda(f, nueva) {
    return `
        <section class="ec-sec ec-onda-sec">
          <div class="ec-barra">
            <button class="ec-play" data-acc="play" title="Reproducir / pausar (Espacio)">${ICO('play2')}</button>
            <button class="ec-btn" data-acc="stop" title="Parar y volver al inicio">${ICO('stop2')}</button>
            <span class="ec-tiempo"><b id="ecT">0:00.0</b> / <span id="ecDur">${fmt(f.duracion)}</span></span>
            <label class="ec-zoom">🔍 <input type="range" id="ecZoom" min="0" max="300" value="0" title="Zoom de la onda"></label>
            <button class="ec-btn" data-acc="autocue" ${nueva ? 'disabled title="Guarda primero la canción"' : 'title="Detectar inicio y mezcla automáticamente"'}>✨ AutoCue</button>
            <button class="ec-btn" data-acc="analizar" ${nueva ? 'disabled title="Guarda primero la canción"' : 'title="Sugiere título, artista, álbum, año, ritmo, energía y marcas (no cambia nada hasta que lo apruebes)"'}>🔎 Analizar</button>
          </div>
          <div class="ec-onda" id="ecOnda"><p class="ec-onda-vacia">${nueva ? 'La onda aparece al elegir el archivo' : 'Cargando la onda…'}</p></div>
          <div class="ec-marcas" id="ecMarcas"></div>
          <div class="ec-ajustes">
            <label>Fade in <input type="range" id="ecFadeIn" min="0" max="5" step="0.1" value="${+f.fade_in || 0}"><b id="ecFadeInV">${(+f.fade_in || 0).toFixed(1)} s</b></label>
            <label>Fade out <input type="range" id="ecFadeOut" min="0" max="10" step="0.1" value="${+f.fade_out || 0}"><b id="ecFadeOutV">${(+f.fade_out || 0).toFixed(1)} s</b></label>
            <label title="Ajuste propio de esta canción, sumado a la normalización">Volumen <input type="range" id="ecGan" min="-12" max="6" step="0.5" value="${+f.ganancia_db || 0}"><b id="ecGanV">${(+f.ganancia_db || 0) > 0 ? '+' : ''}${(+f.ganancia_db || 0).toFixed(1)} dB</b></label>
          </div>
          <div class="ec-sug" id="ecSug" hidden></div>
          <p class="ec-ayuda">Clic en la onda = ir a ese punto · Marca elegida: <b>← →</b> la mueven (Mayús = 1 s) y el audio salta ahí · <b>F</b> = fijarla donde va el audio · <b>1–6</b> elegir marca · <b>Espacio</b> play/pausa</p>
        </section>`;
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

        ${htmlOnda(f, nueva)}

        <div class="ec-dos">
          <section class="ec-sec"><h3>Datos</h3>
            <div class="ec-campos">
              <label class="ec-ancho"><span id="ecLblArtista">Artista</span> <input id="ecArtista" list="ecArtistas" value="${esc(f.artista)}" autocomplete="off"><datalist id="ecArtistas"></datalist></label>
              <label class="ec-ancho">Título <input id="ecTitulo" value="${esc(f.titulo)}"></label>
              <label class="ec-ancho" data-musica>Álbum <input id="ecAlbum" value="${esc(f.album || '')}"></label>
              <label data-musica>Año <input id="ecAnio" type="number" min="1900" max="2100" value="${esc(f.anio || '')}"></label>
              <label data-musica>BPM <input id="ecBpm" type="number" min="40" max="300" value="${esc(f.bpm || '')}"></label>
              <label>Tipo <select id="ecTipo">${E.tipos.map(k => `<option value="${k}" ${f.tipo_audio === k ? 'selected' : ''}>${TIPOS[k] || k}</option>`).join('')}</select></label>
              <label data-ident hidden>Categoría <select id="ecIdent"></select></label>
              <label data-musica>${esc(etq('cat1', 'Género'))} <select id="ecCat1">${opcionesCat('cat1', f.cat1)}</select></label>
              <label data-musica>${esc(etq('cat2', 'Rotación'))} <select id="ecCat2">${opcionesCat('cat2', f.cat2)}</select></label>
              <label data-musica>${esc(etq('cat3', 'Subgénero'))} <select id="ecCat3">${opcionesCat('cat3', f.cat3)}</select></label>
              <label data-musica data-cuna>Voz <select id="ecVoz">${opcionesCat('voz', f.voz)}</select></label>
              <label data-musica>Origen <select id="ecOrigen"><option value="">—</option>${['NACIONAL', 'INTERNACIONAL'].map(o => `<option ${String(f.origen || '').toUpperCase() === o ? 'selected' : ''}>${o}</option>`).join('')}</select></label>
              <label data-musica>Energía <select id="ecEnergia"><option value="">—</option>${[1, 2, 3, 4, 5].map(n => `<option value="${n}" ${+f.energia === n ? 'selected' : ''}>${n} · ${['', 'Lenta', 'Suave', 'Media', 'Movida', 'Rápida'][n]}</option>`).join('')}</select></label>
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
            <section class="ec-sec ec-meta"><h3>Publicar metadata</h3>
              <label>Al sonar <select id="ecMetaModo">
                <option value="" ${!f.meta_modo ? 'selected' : ''}>Según el tipo (música sí, cuñas no)</option>
                <option value="actualizar" ${f.meta_modo === 'actualizar' ? 'selected' : ''}>Actualizar</option>
                <option value="no_actualizar" ${f.meta_modo === 'no_actualizar' ? 'selected' : ''}>No actualizar</option></select></label>
              <div class="ec-meta-campos">
                <label>Título <input id="ecMetaTitulo" value="${esc(f.meta_titulo || '')}" placeholder="AUTO"></label>
                <label>Artista <input id="ecMetaArtista" value="${esc(f.meta_artista || '')}" placeholder="AUTO"></label>
                <label>Campo extra 1 <input id="ecMetaExtra1" value="${esc(f.meta_extra1 || '')}"></label>
                <label>Campo extra 2 <input id="ecMetaExtra2" value="${esc(f.meta_extra2 || '')}"></label>
              </div>
              <p class="ec-ayuda">Es el texto que se muestra al aire mientras suena (RDS, streaming). AUTO = el título y el artista del audio.
                «No actualizar» deja lo que ya se mostraba.</p>
            </section>
            <section class="ec-sec"><h3>Bloqueo en automático</h3>
              <label class="ec-rep">No repetir hasta
                <input type="number" id="ecRepN" min="0" max="999" value="${repInicial(f.repeticion_horas).n || ''}" placeholder="0">
                <select id="ecRepU">${REP_UNIDADES.map(u => `<option value="${u.h}" ${u.h === repInicial(f.repeticion_horas).h ? 'selected' : ''}>${u.n}</option>`).join('')}</select>
              </label>
              <p class="ec-ayuda">Después de sonar, el automático no la vuelve a elegir hasta que pase ese tiempo (0 = la regla general). En manual, el Studio avisa si ya sonó y tú decides.</p>
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
    ajustarTipo();
    fondo.querySelector('#ecTipo').addEventListener('change', ajustarTipo);
    fetch(`/api/v1/artistas?tipo=${encodeURIComponent(f.tipo_audio === 'Cuña' ? 'Cuña' : 'Musica')}`).then(r => r.ok ? r.json() : []).then(l => {
      const dl = fondo.querySelector('#ecArtistas');
      if (dl) dl.innerHTML = l.map(a => `<option value="${esc(a.artista)}">`).join('');
    }).catch(() => { });
  }

  /** Jingles, sweepers y tips no tienen género ni rotación: llevan su propia categoría (Admin › Identificadores) */
  function ajustarTipo() {
    const tipo = E.raiz.querySelector('#ecTipo').value, esCuna = tipo === 'Cuña';
    const esIdent = ['Efecto', 'Sweeper', 'Tips'].includes(tipo);
    E.raiz.querySelectorAll('[data-musica]').forEach(l => { l.hidden = esIdent || (esCuna && !l.hasAttribute('data-cuna')); });
    const lbl = E.raiz.querySelector('#ecLblArtista'); if (lbl) lbl.textContent = esCuna ? 'Anunciante / cliente' : 'Artista';
    const l = E.raiz.querySelector('[data-ident]'), sel = E.raiz.querySelector('#ecIdent');
    l.hidden = !esIdent;
    const actual = sel.value || E.f.categoria_ident || '';
    sel.innerHTML = '<option value="">(sin categoría)</option>' + (E.ident?.[tipo] || [])
      .map(c => `<option ${c.nombre.toUpperCase() === actual.toUpperCase() ? 'selected' : ''}>${esc(c.nombre)}</option>`).join('');
  }

  function pintarMarcas() {
    E.raiz.querySelector('#ecMarcas').innerHTML = marcasDe().map((m, i) => `
      <div class="ec-marca ${E.sel === m.k ? 'sel' : ''}" data-m="${m.k}" style="--mc:${m.c}">
        <div class="ec-marca-tit">${i + 1} · ${m.n}</div>
        <button class="ec-marcar" data-acc="fijar" title="Marca ${m.n} donde va sonando el audio (tecla F)">● MARCAR</button>
        <input class="ec-marca-v" data-acc="valor" value="${E.valores[m.k] ? fmt(E.valores[m.k]) : '—'}" title="Escribe m:ss.d o segundos">
        <div class="ec-marca-btns">
          <button data-acc="menos" title="Atrás 0,1 s (Mayús: 1 s) — el audio salta ahí">«</button>
          <button data-acc="mas" title="Adelante 0,1 s (Mayús: 1 s) — el audio salta ahí">»</button>
          <button data-acc="escuchar" title="Escuchar desde ${(E.antes != null ? E.antes : m.antes) ? (E.antes != null ? E.antes : m.antes) + ' s antes' : 'la marca'}">${ICO('play2')}</button>
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
    marcasDe().forEach(m => {
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
    // Siempre suena justo desde la marca (sin segundos de adelanto), igual que en pautas
    const antes = E.antes != null ? E.antes : m.antes;
    E.ws.setTime(Math.max(0, (E.valores[k] || 0) - antes));
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
      if (!b) { if (e.target === R && !E.embebido) pedirCerrar(); return; }
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
        case 'analizar': await analizar(); break;
        case 'sug-aplicar': await aplicarSugerencias(); break;
        case 'sug-cerrar': R.querySelector('#ecSug').hidden = true; break;
        case 'sug-clave': await guardarClaveAcoustid(); break;
        case 'guardar': guardar(false); break;
        case 'guardar-aprobar': guardar(true); break;
        case 'tapa-etiqueta': tapa('etiqueta'); break;
        case 'tapa-quitar': tapa('quitar'); break;
        case 'restricciones': window.BibliotecaVista?.restricciones?.([E.id]); break;
      }
      if (marca) R.querySelectorAll('.ec-marca').forEach(x => x.classList.toggle('sel', x.dataset.m === E.sel));
    });
    R.addEventListener('change', e => { if (e.target.id === 'ecSugItunes' || e.target.id === 'ecSugDeezer') cambiarFuentes(); });
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
    if (E.embebido) {
      // Dentro de otra ventana (pauta): solo con la pestaña a la vista, sin robar Ctrl+S ni Esc ni la barra a los botones
      if (!E.raiz.offsetParent || document.activeElement?.tagName === 'BUTTON') return;
    } else {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') { e.preventDefault(); guardar(false); return; }
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); pedirCerrar(); return; }
    }
    if (escribiendo) return;
    if (e.code === 'Space') { e.preventDefault(); E.ws?.playPause(); }
    else if (/^[1-6]$/.test(e.key) && marcasDe()[+e.key - 1]) { E.sel = marcasDe()[+e.key - 1].k; pintarMarcas(); }
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

  // ── Análisis: sugerencias que el operador aprueba (nada se aplica solo) ──
  const CAMPOS_FICHA = { titulo: '#ecTitulo', artista: '#ecArtista', album: '#ecAlbum', anio: '#ecAnio', bpm: '#ecBpm', energia: '#ecEnergia' };
  const verSug = c => c.campo === 'caratula' ? !!E.id && !!E.raiz.querySelector('#ecTapa')
    : c.campo.startsWith('cue_') ? marcasDe().some(m => m.k === c.campo)
    : (el => !!el && !el.closest('[hidden]'))(E.raiz.querySelector(CAMPOS_FICHA[c.campo]));   // jingles: sin álbum/BPM/energía
  const nombreActual = (c, v) => c.campo === 'energia' ? ({ 1: '1 · Lenta', 2: '2 · Suave', 3: '3 · Media', 4: '4 · Movida', 5: '5 · Rápida' }[v] || '—')
    : c.campo.startsWith('cue_') ? (v ? fmt(v) : '—') : (v || '—');
  const CONF = { alta: ['segura', '#52b788'], media: ['probable', '#ffa94d'], baja: ['aproximada', '#9aa3b2'] };

  async function analizar(clave) {
    const R = E.raiz, est = R.querySelector('#ecEstado'), cont = R.querySelector('#ecSug');
    est.textContent = 'Analizando (ritmo, marcas y búsqueda en línea)…';
    let d;
    try {
      const r = await fetch(`/api/v1/biblioteca/${E.id}/analizar`, { method: 'POST' });
      d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(typeof d.detail === 'string' ? d.detail : `Error ${r.status}`);
    } catch (err) { est.textContent = ''; toast(err.message, 'error'); return; }
    est.textContent = '';
    E.sug = d;
    const filas = d.campos.filter(c => c.mostrar && verSug(c));
    const marcada = c => !(c.actual) && c.confianza !== 'baja' && !(c.campo === 'cue_inicio');
    cont.hidden = false;
    cont.innerHTML = `
      <div class="ec-sug-cab"><b>🔎 Sugerencias</b><span>Nada se cambia hasta que marques lo que quieres y presiones «Aplicar»; luego guarda.</span>
        ${filas.length ? '<button class="ec-btn ec-pri" data-acc="sug-aplicar">Aplicar las marcadas</button>' : ''}
        <button class="ec-btn" data-acc="sug-cerrar" title="Cerrar">✕</button></div>
      ${filas.length ? `<table class="ec-sug-tabla"><thead><tr><th></th><th>Dato</th><th>Ahora</th><th>Sugerido</th><th>De dónde</th></tr></thead><tbody>
        ${filas.map(c => `<tr data-campo="${c.campo}"><td><input type="checkbox" ${marcada(c) ? 'checked' : ''}></td>
          <td>${esc(c.etiqueta)}</td><td class="ec-sug-ahora">${c.campo === 'caratula' ? 'sin carátula' : esc(nombreActual(c, c.actual))}</td>
          <td class="ec-sug-nuevo">${c.campo === 'caratula' ? `<img class="ec-sug-tapa" src="${esc(c.sugerido)}" alt="carátula sugerida" referrerpolicy="no-referrer">` : `<b>${esc(c.texto || nombreActual(c, c.sugerido))}</b>`}</td>
          <td><span class="ec-sug-conf" style="--cc:${CONF[c.confianza][1]}">${CONF[c.confianza][0]}</span> <small>${esc(c.fuente)}</small></td></tr>`).join('')}
        </tbody></table>` : '<p class="ec-sug-vacio">No hay nada nuevo que proponer: lo que se pudo detectar ya está en la ficha.</p>'}
      ${(d.avisos || []).map(a => `<p class="ec-sug-aviso">ℹ ${esc(a)}</p>`).join('')}
      ${d.acoustid || R.querySelector('#ecTitulo') === null ? '' : `<div class="ec-sug-clave"><input id="ecAcoustKey" placeholder="Clave gratuita de AcoustID (acoustid.org/new-application)"><button class="ec-btn" data-acc="sug-clave">Guardar clave y analizar de nuevo</button></div>`}
      ${R.querySelector('#ecTitulo') === null ? '' : `<div class="ec-sug-fuentes">Buscar también en:
        <label><input type="checkbox" id="ecSugItunes" ${d.fuentes?.itunes ? 'checked' : ''}> iTunes</label>
        <label><input type="checkbox" id="ecSugDeezer" ${d.fuentes?.deezer ? 'checked' : ''}> Deezer</label>
        <small>Buscan por título y artista (no escuchan el audio). Solo en el análisis de una canción.</small></div>`}
      `;
    cont.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }

  async function guardarClaveAcoustid() {
    const v = E.raiz.querySelector('#ecAcoustKey')?.value.trim();
    if (!v) { toast('Pega la clave de AcoustID', 'error'); return; }
    const r = await fetch('/api/v1/config/acoustid', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ clave: v }) });
    if (!r.ok) { toast('No se pudo guardar la clave', 'error'); return; }
    toast('✓ Clave guardada', 'success');
    await analizar();
  }

  async function cambiarFuentes() {
    const R = E.raiz;
    const r = await fetch('/api/v1/config/analisis', { method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ itunes: R.querySelector('#ecSugItunes').checked, deezer: R.querySelector('#ecSugDeezer').checked }) });
    if (!r.ok) { toast('No se pudo guardar', 'error'); return; }
    await analizar();
  }

  async function aplicarSugerencias() {
    const R = E.raiz, marcadas = [...R.querySelectorAll('#ecSug tbody tr')].filter(tr => tr.querySelector('input').checked);
    if (!marcadas.length) { toast('Marca lo que quieres aplicar', 'error'); return; }
    let n = 0, cover = null;
    marcadas.forEach(tr => {
      const c = E.sug.campos.find(x => x.campo === tr.dataset.campo);
      if (!c) return;
      if (c.campo === 'caratula') { cover = c.sugerido; n++; }
      else if (c.campo.startsWith('cue_')) { E.valores[c.campo] = +(+c.sugerido).toFixed(2); n++; }
      else { const el = R.querySelector(CAMPOS_FICHA[c.campo]); if (el) { el.value = c.sugerido; n++; } }
    });
    E.sucio = true;
    pintarMarcas(); dibujarMarcas();
    if (cover) {                       // la carátula se guarda enseguida (como «Importar imagen»); lo demás espera a Guardar
      const r = await fetch(`/api/v1/audios/${E.id}/caratula-url`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ url: cover }) });
      if (r.ok) {
        const img = `<img src="/api/v1/audios/${E.id}/caratula?t=${Date.now()}" onerror="this.remove()" alt="">`;
        R.querySelector('#ecTapa').innerHTML = img + '<span>♪</span>'; R.querySelector('#ecTapaMini').innerHTML = img + '♪';
        window.BibliotecaVista?.recargar?.();
      } else toast((await r.json().catch(() => ({}))).detail || 'No se pudo poner la carátula', 'error');
    }
    R.querySelector('#ecEstado').textContent = `✓ ${n} dato(s) aplicados a la ficha: revisa y guarda`;
    R.querySelector('#ecSug').hidden = true;
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
    const horas = Math.round((+v('#ecRepN') || 0) * (+v('#ecRepU') || 1));
    const d = {
      titulo: v('#ecTitulo'), artista: v('#ecArtista'), album: v('#ecAlbum'),
      fecha_lanzamiento: v('#ecAnio'), bpm: v('#ecBpm') ? +v('#ecBpm') : null,
      categoria_ident: v('#ecIdent'),
      tipo_audio: v('#ecTipo'), cat1: v('#ecCat1'), cat2: v('#ecCat2'), cat3: v('#ecCat3'), voz: v('#ecVoz'),
      origen: v('#ecOrigen') || null, energia: v('#ecEnergia') ? +v('#ecEnergia') : null, prioridad: +v('#ecPrioridad'),
      fade_in: +v('#ecFadeIn'), fade_out: +v('#ecFadeOut'), ganancia_db: +v('#ecGan'), repeticion_horas: horas,
      meta_modo: v('#ecMetaModo'), meta_titulo: v('#ecMetaTitulo'), meta_artista: v('#ecMetaArtista'),
      meta_extra1: v('#ecMetaExtra1'), meta_extra2: v('#ecMetaExtra2'),
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
    const { categoria_ident: catIdent, ...resto } = d;        // la categoría de identificadores tiene su propio endpoint
    // (la metadata sí admite vacío: así se vuelve a AUTO)
    const cuerpo = Object.fromEntries(Object.entries(resto).filter(([k, x]) => k.startsWith('meta_') || (x !== '' && x !== null && x !== undefined)));
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
      if (['Efecto', 'Sweeper', 'Tips'].includes(d.tipo_audio)) {
        await fetch('/api/v1/ident/audios', { method: 'PUT', headers: { 'Content-Type': 'application/json' },
                                              body: JSON.stringify({ ids: [id], categoria: catIdent || null }) });
      }
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
    const alCerrar = E.onCerrar;
    if (E.embebido) E.raiz.innerHTML = ''; else E.raiz?.remove();     // embebido: el contenedor es de quien lo montó
    document.removeEventListener('keydown', teclado, true);
    E = null;
    try { alCerrar?.(); } catch (_) { }
  }

  /**
   * Monta SOLO la onda y las marcas dentro de otro lugar (la pestaña «Audio y versiones» de una pauta):
   * la misma ventana de la canción, ajustada a lo que necesita ese uso.
   *   opc.marcas   claves de las marcas a mostrar (p. ej. ['cue_intro','cue_outro','cue_mezcla'])
   *   opc.valores  valores iniciales {cue_intro: 2.4, …}
   *   opc.audioId  para AutoCue (si el audio ya está guardado)
   *   opc.antes    segundos antes de la marca al escucharla (0 = justo desde la marca)
   *   opc.ajustes  true = fundidos y volumen
   *   opc.onCambio se llama cuando el usuario mueve o fija una marca
   */
  function montar(cont, opc = {}) {
    if (E) cerrar(true);
    const lista = MARCAS.filter(m => !opc.marcas || opc.marcas.includes(m.k));
    E = { embebido: true, raiz: cont, id: opc.audioId || null, f: { duracion: 0, fade_in: 0, fade_out: 0, ganancia_db: 0 },
          antes: opc.antes, marcasActivas: lista, valores: Object.fromEntries(MARCAS.map(m => [m.k, +((opc.valores || {})[m.k] || 0)])),
          sel: lista[0]?.k, ws: null, rp: null, zonas: [], archivo: null, sucio: false, schema: { categorias: [] }, ident: {}, tipos: [] };
    cont.innerHTML = `<div class="ec ec-embed ${opc.ajustes ? '' : 'ec-sin-ajustes'}">${htmlOnda(E.f, !E.id)}<span class="ec-estado" id="ecEstado"></span></div>`;
    pintarMarcas(); enlazar();
    document.addEventListener('keydown', teclado, true);
    const avisar = () => opc.onCambio?.();
    cont.addEventListener('click', () => setTimeout(avisar, 0));
    cont.addEventListener('change', avisar);
    if (opc.url) cargarOnda(opc.url);
    return {
      cargar(url, valores, audioId) {
        if (!E || !E.embebido) return;
        if (valores) MARCAS.forEach(m => { E.valores[m.k] = +((valores || {})[m.k] || 0); });
        E.id = audioId || null; E.sucio = false;
        ['autocue', 'analizar'].forEach(a => { const b = E.raiz.querySelector(`[data-acc="${a}"]`); if (b) { b.disabled = !E.id; if (!E.id) b.title = 'Guarda primero el audio'; } });
        const sg = E.raiz.querySelector('#ecSug'); if (sg) sg.hidden = true;
        pintarMarcas(); cargarOnda(url);
      },
      valores: () => (E && E.embebido ? { ...E.valores } : {}),
      destruir: () => { if (E && E.embebido) cerrar(true); },
    };
  }

  window.EditorCancion = {
    abrir, cerrar, montar,
    /** Valores de las marcas del editor embebido (vacío si no hay uno montado) */
    valoresMontados: () => (E && E.embebido ? { ...E.valores } : {}),
    /** Pausa el audio del editor embebido (al cambiar de pestaña) */
    pausarMontado: () => { try { if (E && E.embebido) E.ws?.pause(); } catch (_) { } },
    cerrarMontado: () => { if (E && E.embebido) cerrar(true); },
  };
})();
