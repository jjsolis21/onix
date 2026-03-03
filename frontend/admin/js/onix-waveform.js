/**
 * onix-waveform.js — Editor de Cue Points · Ónix FM
 * =====================================================
 * Estabilidad Visual y Edición de Cues Existentes.
 */
; (function () {
  'use strict';

  function _id(id) { return document.getElementById(id); }

  var CUE_DEFS = [
    { key: 'inicio', label: 'INICIO', inputId: 'cue-inicio' },
    { key: 'intro', label: 'INTRO', inputId: 'cue-intro' },
    { key: 'inicio_coro', label: 'INICIO CORO', inputId: 'cue-inicio-coro' },
    { key: 'final_coro', label: 'FINAL CORO', inputId: 'cue-final-coro' },
    { key: 'outro', label: 'OUTRO', inputId: 'cue-outro' },
    { key: 'mezcla', label: 'MEZCLA', inputId: 'cue-mezcla' },
  ];

  var _ws = null;
  var _rp = null;
  var _regions = {};
  var _cues = {};
  var _dur = 0;
  var _raf = null;
  var _playing = false;
  var _inited = false;
  var _listenersAttached = false;
  var _panelVisible = false;
  var _tries = 0;
  var _vol = 1.0;
  var _fadeIn = 0;
  var _fadeOut = 0;

  function _r1(n) { return Math.round(n * 10) / 10; }
  function _r2(n) { return Math.round(n * 100) / 100; }

  function _fmtLED(s) {
    if (!s || isNaN(s) || s < 0) return '0:00.0';
    var m = Math.floor(s / 60), q = Math.floor(s % 60), d = Math.floor((s % 1) * 10);
    return m + ':' + (q < 10 ? '0' : '') + q + '.' + d;
  }

  function _fmtShort(s) {
    return (!s || isNaN(s) || s < 0) ? '0.0s' : (_r1(s) + 's');
  }

  function _tick() {
    if (!_ws) return;
    var ec = _id('wf-time-cur');
    if (ec) ec.textContent = _fmtLED(_ws.getCurrentTime());
    if (_playing) _raf = requestAnimationFrame(_tick);
  }

  function _icon(playing) {
    var svg = _id('wf-play-icon');
    if (!svg) return;
    if (playing) {
      svg.innerHTML = '<rect x="4" y="2" width="5" height="20" rx="1" fill="currentColor"/><rect x="15" y="2" width="5" height="20" rx="1" fill="currentColor"/>';
    } else {
      svg.innerHTML = '<polygon points="5,2 21,12 5,22" fill="currentColor"/>';
    }
  }

  function _updateInfoBar() {
    var el = _id('wf-info-bar');
    if (!el || !_dur) return;
    el.innerHTML =
      '<span class="wf-info__item">Duración: <strong>' + _fmtShort(_dur) + '</strong></span>' +
      '<span class="wf-info__sep">·</span>' +
      '<span class="wf-info__item" style="color:#ff9090">Intro: <strong>' + _fmtShort(_cues.intro || 0) + '</strong></span>' +
      '<span class="wf-info__sep">·</span>' +
      '<span class="wf-info__item" style="color:#00ddaa">Coro: <strong>' + _fmtShort(Math.max(0, (_cues.final_coro || 0) - (_cues.inicio_coro || 0))) + '</strong></span>';
  }

  function _syncInputs() {
    CUE_DEFS.forEach(function (def) {
      var el = _id(def.inputId);
      if (el) el.value = _r2(_cues[def.key] || 0);
    });
    _updateInfoBar();
  }

  function _calcDefaults() {
    if (!_dur) return {};
    var d = _dur;
    return {
      inicio: 0,
      intro: _r1(Math.min(d * 0.12, 8)),
      inicio_coro: _r1(d * 0.30),
      final_coro: _r1(d * 0.65),
      outro: _r1(Math.max(d - 30, d * 0.80)),
      mezcla: _r1(Math.max(d - 6, d * 0.95)),
    };
  }

  function _autoCue() {
    if (!_dur) return;
    var d = _calcDefaults();
    for (var k in d) {
      /* Solo sobreescribir si el valor actual es 0 o no existe */
      if (!_cues[k]) _cues[k] = d[k];
    }
    _syncInputs();
    _drawRegions();
  }

  function _drawRegions() {
    if (!_rp || !_dur) return;
    try { _rp.clearRegions(); } catch (e) { }
    _regions = {};
    var s0 = _cues.inicio || 0, s1 = _cues.intro || 0;
    if (s1 > s0) _regions.zona_intro = _rp.addRegion({ id: 'zona_intro', start: s0, end: s1, color: 'rgba(180,30,30,0.28)', drag: false, resize: false });
    var s2 = _cues.final_coro || 0;
    if (s2 > s1) _regions.zona_cuerpo = _rp.addRegion({ id: 'zona_cuerpo', start: s1, end: s2, color: 'rgba(0,140,80,0.18)', drag: false, resize: false });
    var s4 = _cues.outro || 0, s5 = _cues.mezcla || _dur;
    if (s5 > s4) _regions.zona_outro = _rp.addRegion({ id: 'zona_outro', start: s4, end: s5, color: 'rgba(200,100,0,0.22)', drag: false, resize: false });
    if (_cues.mezcla > 0) _regions.linea_m = _rp.addRegion({ id: 'linea_mezcla', start: _cues.mezcla, end: Math.min(_dur, _cues.mezcla + 0.1), color: 'rgba(60,130,255,0.90)', drag: false, resize: false });
    _updateInfoBar();
  }

  function _showPanel() {
    if (_panelVisible) return;
    var p = _id('wf-panel');
    if (!p) return;
    _panelVisible = true;
    p.style.display = 'block';
  }

  function _hidePanel() {
    var p = _id('wf-panel');
    if (!p) return;
    _panelVisible = false;
    p.style.display = 'none';
  }

  function _spinner(show) {
    var sp = _id('wf-loading');
    if (sp) sp.style.display = show ? 'flex' : 'none';
  }

  /* ══════════════════════════════════════════════════════════════
     wfInit — Crea la instancia de WaveSurfer
  ══════════════════════════════════════════════════════════════ */
  function wfInit() {
    /* Si ya está iniciado y la instancia existe, no hacer nada */
    if (_inited && _ws) return;

    var container = _id('waveform');
    if (!container) {
      console.warn('[ÓnixWF] Contenedor #waveform no encontrado.');
      return;
    }
    container.innerHTML = ''; /* Limpiar restos de la onda anterior SOLAMENTE */

    if (typeof WaveSurfer === 'undefined') {
      if (_tries++ < 50) { setTimeout(wfInit, 100); return; }
      console.error('[ÓnixWF] WaveSurfer CDN no disponible.');
      return;
    }
    /* ... resto de wfInit ... */

    var RP = (typeof RegionsPlugin !== 'undefined' && RegionsPlugin) ||
      (typeof WaveSurferRegions !== 'undefined' && WaveSurferRegions) ||
      (WaveSurfer.Regions ? WaveSurfer.Regions : null);

    if (!RP) {
      if (_tries++ < 50) { setTimeout(wfInit, 100); return; }
      console.error('[ÓnixWF] Regions CDN no disponible.');
      return;
    }

    _inited = true;
    _tries = 0;

    try {
      _rp = RP.create();
      _ws = WaveSurfer.create({
        container: '#waveform',
        waveColor: '#333',
        progressColor: '#ff6600',
        cursorColor: '#ffffff',
        cursorWidth: 2,
        height: 120,
        barWidth: 2,
        barGap: 1,
        barRadius: 1,
        normalize: true,
        plugins: [_rp],
      });

      _ws.on('ready', function () {
        _dur = _ws.getDuration();
        _spinner(false);
        var ec = _id('wf-time-cur'); if (ec) ec.textContent = _fmtLED(0);
        var et = _id('wf-time-total'); if (et) et.textContent = _fmtLED(_dur);
        _autoCue();
        _ws.zoom(60);
        console.info('[ÓnixWF] Audio cargado.');
      });

      _ws.on('play', function () { _playing = true; _icon(true); _raf = requestAnimationFrame(_tick); });
      _ws.on('pause', function () { _playing = false; _icon(false); if (_raf) cancelAnimationFrame(_raf); });
      _ws.on('finish', function () { _playing = false; _icon(false); });

      _attachListeners();
      console.info('[ÓnixWF] Inicializado.');
    } catch (err) {
      console.error('[ÓnixWF] Error init:', err);
      _inited = false;
    }
  }

  function _attachListeners() {
    if (_listenersAttached) return;
    _listenersAttached = true;

    document.addEventListener('click', function (e) {
      if (e.target.closest('#wf-btn-play')) { if (_ws) _ws.playPause(); return; }
      if (e.target.closest('#wf-btn-stop')) { if (_ws) { _ws.stop(); _playing = false; _icon(false); } return; }
      if (e.target.closest('#wf-btn-autocue')) { _autoCue(); return; }
      if (e.target.closest('#wf-btn-reiniciar')) { _cues = {}; _autoCue(); return; }

      var nudgeBtn = e.target.closest('[data-wf-nudge]');
      if (nudgeBtn) {
        var parts = nudgeBtn.dataset.wfNudge.split(':'), key = parts[0], delta = parseFloat(parts[1]);
        _cues[key] = Math.max(0, Math.min(_dur, _r2((_cues[key] || 0) + delta)));
        _syncInputs(); _drawRegions(); return;
      }

      var escBtn = e.target.closest('[data-wf-escuchar]');
      if (escBtn && _ws) {
        var t = _cues[escBtn.dataset.wfEscuchar] || 0;
        _ws.seekTo(t / _dur); if (!_playing) _ws.play(); return;
      }

      var reinBtn = e.target.closest('[data-wf-reiniciar]');
      if (reinBtn) {
        var rk = reinBtn.dataset.wfReiniciar, defs = _calcDefaults();
        if (defs[rk] !== undefined) { _cues[rk] = defs[rk]; _syncInputs(); _drawRegions(); }
        return;
      }
    });

    document.addEventListener('change', function (e) {
      CUE_DEFS.forEach(function (def) {
        if (e.target.id === def.inputId) {
          var v = parseFloat(e.target.value);
          if (!isNaN(v) && v >= 0 && v <= _dur) { _cues[def.key] = _r2(v); _drawRegions(); _updateInfoBar(); }
        }
      });
      if (e.target.id === 'wf-volume') {
        _vol = parseFloat(e.target.value) / 100;
        if (_ws) _ws.setVolume(_vol);
        var vl = _id('wf-volume-label'); if (vl) vl.textContent = Math.round(_vol * 100) + '%';
      }
      if (e.target.id === 'wf-fade-in') _fadeIn = parseFloat(e.target.value) || 0;
      if (e.target.id === 'wf-fade-out') _fadeOut = parseFloat(e.target.value) || 0;
    });
  }

  /* ══════════════════════════════════════════════════════════════
     API PÚBLICA (window.wf...)
  ══════════════════════════════════════════════════════════════ */

  function wfLoadFile(file) {
    if (!_ws) { setTimeout(function () { wfLoadFile(file); }, 200); return; }
    _showPanel();
    if (_id('wf-filename')) _id('wf-filename').textContent = file.name;
    _spinner(true);
    _cues = {}; _dur = 0;
    _ws.loadBlob(file);
  }

  /**
   * Carga un audio por URL (usado para editar canciones existentes)
   * @param {string} url - URL del stream o archivo
   * @param {object} cues - Valores iniciales de cues {intro, mezcla, etc.}
   */
  function wfLoadUrl(url, cues) {
    console.log('[ÓnixDebug] Cargando onda desde:', url);
    if (!_ws) {
      console.warn('[ÓnixWF] _ws no existe, re-inicializando...');
      wfInit();
      if (!_ws) { setTimeout(function () { wfLoadUrl(url, cues); }, 200); return; }
    }
    _showPanel();
    _spinner(true);
    _cues = cues || {};
    _dur = 0;
    _ws.load(url);
    /* Al terminar de cargar ('ready'), se sincronizarán los inputs vía _autoCue */
  }

  function wfDestroy() {
    if (_raf) cancelAnimationFrame(_raf);
    if (_ws) {
      try { _ws.destroy(); } catch (e) { }
      _ws = null;
    }
    _rp = null;
    _inited = false; /* PERMITIR RE-INICIALIZACIÓN */
    _panelVisible = false;
    _hidePanel();

    /* NO BORRAR innerHTML aquí, se encarga wfInit */
    console.info('[ÓnixWF] Destruido.');
  }

  function wfGetMarkers() {
    return {
      inicio: _r2(_cues.inicio || 0),
      intro: _r2(_cues.intro || 0),
      inicio_coro: _r2(_cues.inicio_coro || 0),
      final_coro: _r2(_cues.final_coro || 0),
      outro: _r2(_cues.outro || 0),
      mezcla: _r2(_cues.mezcla || 0),
      fade_in: _fadeIn || 0,
      fade_out: _fadeOut || 0,
      volumen: Math.round(_vol * 100)
    };
  }

  window.wfInit = wfInit;
  window.wfLoadFile = wfLoadFile;
  window.wfLoadUrl = wfLoadUrl;
  window.wfDestroy = wfDestroy;
  window.wfGetMarkers = wfGetMarkers;
  window.wfAutoCue = function () { _autoCue(); };

})();
