/**
 * onix-waveform.js — Editor de Cue Points · Ónix FM
 * =====================================================
 * Anti-flickering fixes:
 *   1. _listenersAttached flag — event listeners registrados UNA vez.
 *   2. _showPanel() muta props individuales, no cssText completo.
 *   3. _panelVisible guard — evita re-show innecesario.
 *
 * 6 cue points Jazler: INICIO · INTRO · INICIO CORO ·
 *                      FINAL CORO · OUTRO · MEZCLA
 *
 * Exports: wfInit, wfLoadFile, wfDestroy, wfGetMarkers, wfAutoCue
 */
;(function () {
  'use strict';

  function _id(id) { return document.getElementById(id); }

  var CUE_DEFS = [
    { key: 'inicio',      label: 'INICIO',      inputId: 'cue-inicio'      },
    { key: 'intro',       label: 'INTRO',       inputId: 'cue-intro'       },
    { key: 'inicio_coro', label: 'INICIO CORO', inputId: 'cue-inicio-coro' },
    { key: 'final_coro',  label: 'FINAL CORO',  inputId: 'cue-final-coro'  },
    { key: 'outro',       label: 'OUTRO',       inputId: 'cue-outro'       },
    { key: 'mezcla',      label: 'MEZCLA',      inputId: 'cue-mezcla'      },
  ];

  var _ws                = null;
  var _rp                = null;
  var _regions           = {};
  var _cues              = {};
  var _dur               = 0;
  var _raf               = null;
  var _playing           = false;
  var _inited            = false;
  var _listenersAttached = false; /* KEY: anti-listener-duplication */
  var _panelVisible      = false; /* KEY: anti-repaint guard */
  var _tries             = 0;
  var _vol               = 1.0;
  var _fadeIn            = 0;
  var _fadeOut           = 0;

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
    var et = _id('wf-time-total');
    if (ec) ec.textContent = _fmtLED(_ws.getCurrentTime());
    if (et) et.textContent = _fmtLED(_dur || 0);
    if (_playing) _raf = requestAnimationFrame(_tick);
  }

  function _icon(playing) {
    var svg = _id('wf-play-icon');
    var btn = _id('wf-btn-play');
    if (!svg) return;
    if (playing) {
      svg.innerHTML = '<rect x="4" y="2" width="5" height="20" rx="1" fill="currentColor"/><rect x="15" y="2" width="5" height="20" rx="1" fill="currentColor"/>';
      if (btn) btn.title = 'Pausa';
    } else {
      svg.innerHTML = '<polygon points="5,2 21,12 5,22" fill="currentColor"/>';
      if (btn) btn.title = 'Play';
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
      inicio:      0,
      intro:       _r1(Math.min(d * 0.12, 8)),
      inicio_coro: _r1(d * 0.30),
      final_coro:  _r1(d * 0.65),
      outro:       _r1(Math.max(d - 30, d * 0.80)),
      mezcla:      _r1(Math.max(d - 6,  d * 0.95)),
    };
  }

  function _autoCue() {
    if (!_dur) return;
    var d = _calcDefaults();
    for (var k in d) { _cues[k] = d[k]; }
    _syncInputs();
    _drawRegions();
  }

  function _drawRegions() {
    if (!_rp || !_dur) return;
    try { _rp.clearRegions(); } catch (e) {}
    _regions = {};
    var s0 = _cues.inicio || 0, s1 = _cues.intro || 0;
    if (s1 > s0) _regions.zona_intro   = _rp.addRegion({ id: 'zona_intro',    start: s0, end: s1,               color: 'rgba(180,30,30,0.28)',   drag: false, resize: false });
    var s2 = _cues.final_coro || 0;
    if (s2 > s1) _regions.zona_cuerpo  = _rp.addRegion({ id: 'zona_cuerpo',   start: s1, end: s2,               color: 'rgba(0,140,80,0.18)',    drag: false, resize: false });
    var s4 = _cues.outro || 0, s5 = _cues.mezcla || _dur;
    if (s5 > s4) _regions.zona_outro   = _rp.addRegion({ id: 'zona_outro',    start: s4, end: s5,               color: 'rgba(200,100,0,0.22)',   drag: false, resize: false });
    if (_cues.mezcla) _regions.linea_m = _rp.addRegion({ id: 'linea_mezcla', start: _cues.mezcla, end: Math.min(_dur, _cues.mezcla + 0.05), color: 'rgba(60,130,255,0.90)', drag: false, resize: false });
    _updateInfoBar();
  }

  /* ── Panel show/hide: props individuales, NO cssText ─────────── */
  function _showPanel() {
    if (_panelVisible) return;           /* guard: no repaint doble */
    var p = _id('wf-panel');
    if (!p) return;
    _panelVisible = true;
    p.style.display    = 'block';
    p.style.visibility = 'visible';
    p.style.opacity    = '1';
    p.style.zIndex     = '1';
    p.style.position   = 'relative';
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
     wfInit
  ══════════════════════════════════════════════════════════════ */
  function wfInit() {
    if (_inited) return;

    if (typeof WaveSurfer === 'undefined') {
      if (_tries++ < 50) { setTimeout(wfInit, 100); return; }
      console.error('[ÓnixWF] WaveSurfer CDN no disponible.');
      return;
    }

    var RP =
      (typeof RegionsPlugin     !== 'undefined' && RegionsPlugin)     ||
      (typeof WaveSurferRegions !== 'undefined' && WaveSurferRegions) ||
      (WaveSurfer.Regions ? WaveSurfer.Regions : null);

    if (!RP) {
      if (_tries++ < 50) { setTimeout(wfInit, 100); return; }
      console.error('[ÓnixWF] Regions CDN no disponible.');
      return;
    }

    _inited = true;
    _tries  = 0;

    try {
      _rp = RP.create();
      _ws = WaveSurfer.create({
        container:    '#waveform',
        waveColor:    '#4a6a4a',
        progressColor:'#6aaa6a',
        cursorColor:  '#ffffff',
        cursorWidth:  1,
        height:       120,
        barWidth:     2,
        barGap:       1,
        barRadius:    1,
        normalize:    true,
        interact:     true,
        fillParent:   true,
        minPxPerSec:  50,
        plugins:      [_rp],
      });

      _ws.on('ready', function () {
        _dur = _ws.getDuration();
        _spinner(false);
        var ec = _id('wf-time-cur');   if (ec) ec.textContent = _fmtLED(0);
        var et = _id('wf-time-total'); if (et) et.textContent = _fmtLED(_dur);
        _autoCue();
        _ws.zoom(60);
        console.info('[ÓnixWF] Listo — ' + _fmtLED(_dur));
      });
      _ws.on('play',   function () { _playing = true;  _icon(true);  _raf = requestAnimationFrame(_tick); });
      _ws.on('pause',  function () { _playing = false; _icon(false); if (_raf) { cancelAnimationFrame(_raf); _raf = null; } });
      _ws.on('finish', function () {
        _playing = false; _icon(false);
        if (_raf) { cancelAnimationFrame(_raf); _raf = null; }
        var ec = _id('wf-time-cur'); if (ec) ec.textContent = _fmtLED(_dur);
      });
      console.info('[ÓnixWF] Inicializado.');
    } catch (err) {
      console.error('[ÓnixWF] Error:', err);
      _inited = false;
    }

    _attachListeners(); /* siempre al final, guarded por flag */
  }

  /* ══════════════════════════════════════════════════════════════
     _attachListeners — separada de wfInit para evitar duplicados
  ══════════════════════════════════════════════════════════════ */
  function _attachListeners() {
    if (_listenersAttached) return;
    _listenersAttached = true;

    document.addEventListener('click', function (e) {

      if (e.target.closest('#wf-btn-play')) {
        if (_ws) try { _ws.playPause(); } catch (er) {}
        return;
      }
      if (e.target.closest('#wf-btn-stop')) {
        if (_ws) { try { _ws.stop(); } catch (er) {} _playing = false; _icon(false); if (_raf) { cancelAnimationFrame(_raf); _raf = null; } var ec = _id('wf-time-cur'); if (ec) ec.textContent = _fmtLED(0); }
        return;
      }
      if (e.target.closest('#wf-btn-autocue') || e.target.closest('#wf-btn-reiniciar')) {
        if (_dur > 0) _autoCue();
        return;
      }

      var nudgeBtn = e.target.closest('[data-wf-nudge]');
      if (nudgeBtn) {
        var parts = nudgeBtn.dataset.wfNudge.split(':');
        var key = parts[0], delta = parseFloat(parts[1]);
        var nv = Math.max(0, Math.min(_dur, _r2((_cues[key] || 0) + delta)));
        _cues[key] = nv;
        var def = CUE_DEFS.find(function (d) { return d.key === key; });
        if (def) { var inp = _id(def.inputId); if (inp) inp.value = nv; }
        _drawRegions(); _updateInfoBar();
        return;
      }

      var escBtn = e.target.closest('[data-wf-escuchar]');
      if (escBtn && _ws) {
        var t = _cues[escBtn.dataset.wfEscuchar] || 0;
        try { _ws.seekTo(t / _dur); if (!_playing) _ws.play(); } catch (er) {}
        return;
      }

      var reinBtn = e.target.closest('[data-wf-reiniciar]');
      if (reinBtn) {
        var rk = reinBtn.dataset.wfReiniciar;
        var def2 = _calcDefaults();
        if (def2[rk] !== undefined) {
          _cues[rk] = def2[rk];
          var rd = CUE_DEFS.find(function (d) { return d.key === rk; });
          if (rd) { var ri = _id(rd.inputId); if (ri) ri.value = _r2(_cues[rk]); }
          _drawRegions(); _updateInfoBar();
        }
        return;
      }

    }, false);

    document.addEventListener('change', function (e) {
      CUE_DEFS.forEach(function (def) {
        if (e.target.id === def.inputId) {
          var v = parseFloat(e.target.value);
          if (!isNaN(v) && v >= 0 && v <= _dur) { _cues[def.key] = _r2(v); _drawRegions(); _updateInfoBar(); }
        }
      });
      if (e.target.id === 'wf-volume') {
        _vol = parseFloat(e.target.value) / 100;
        if (_ws) try { _ws.setVolume(_vol); } catch (er) {}
        var vl = _id('wf-volume-label'); if (vl) vl.textContent = Math.round(_vol * 100) + '%';
      }
      if (e.target.id === 'wf-fade-in')  _fadeIn  = parseFloat(e.target.value) || 0;
      if (e.target.id === 'wf-fade-out') _fadeOut = parseFloat(e.target.value) || 0;
    }, false);

    document.addEventListener('input', function (e) {
      if (e.target.id === 'wf-volume') {
        _vol = parseFloat(e.target.value) / 100;
        if (_ws) try { _ws.setVolume(_vol); } catch (er) {}
        var vl = _id('wf-volume-label'); if (vl) vl.textContent = Math.round(_vol * 100) + '%';
      }
    }, false);
  }

  /* ══════════════════════════════════════════════════════════════
     wfLoadFile
  ══════════════════════════════════════════════════════════════ */
  function wfLoadFile(file) {
    if (!_ws) { setTimeout(function () { wfLoadFile(file); }, 200); return; }

    _showPanel(); /* guard interno: no hace nada si ya está visible */

    var fn = _id('wf-filename'); if (fn) fn.textContent = file.name;
    _spinner(true);
    if (_raf) { cancelAnimationFrame(_raf); _raf = null; }
    _playing = false; _dur = 0; _cues = {}; _regions = {};
    _icon(false);
    var ec = _id('wf-time-cur');   if (ec) ec.textContent = '0:00.0';
    var et = _id('wf-time-total'); if (et) et.textContent = '0:00.0';
    var ib = _id('wf-info-bar');   if (ib) ib.innerHTML = '<span class="wf-info__item" style="color:#444">Analizando…</span>';
    CUE_DEFS.forEach(function (def) { var inp = _id(def.inputId); if (inp) inp.value = '0'; });
    try { if (_ws.isPlaying && _ws.isPlaying()) _ws.stop(); } catch (e) {}

    setTimeout(function () {
      try { _ws.loadBlob(file); }
      catch (err) { console.error('[ÓnixWF] loadBlob:', err); _spinner(false); }
    }, 250);
  }

  function wfAutoCue()    { if (_dur > 0) _autoCue(); }

  function wfDestroy() {
    if (_raf) { cancelAnimationFrame(_raf); _raf = null; }
    if (_ws) {
      try { _ws.destroy(); } catch (e) {}
      _ws = null; _rp = null; _regions = {}; _cues = {};
      _dur = 0; _playing = false; _inited = false;
    }
    _hidePanel();
    _panelVisible = false;
    CUE_DEFS.forEach(function (def) { var inp = _id(def.inputId); if (inp) inp.value = ''; });
    var ec = _id('wf-time-cur');   if (ec) ec.textContent = '0:00.0';
    var et = _id('wf-time-total'); if (et) et.textContent = '0:00.0';
    console.info('[ÓnixWF] Destruido.');
  }

  function wfGetMarkers() {
    return {
      inicio:      _r2(_cues.inicio      || 0),
      intro:       _r2(_cues.intro       || 0),
      inicio_coro: _r2(_cues.inicio_coro || 0),
      final_coro:  _r2(_cues.final_coro  || 0),
      outro:       _r2(_cues.outro       || 0),
      mezcla:      _r2(_cues.mezcla      || 0),
      fade_in:     _fadeIn,
      fade_out:    _fadeOut,
      volumen:     Math.round(_vol * 100),
    };
  }

  window.wfInit       = wfInit;
  window.wfLoadFile   = wfLoadFile;
  window.wfDestroy    = wfDestroy;
  window.wfGetMarkers = wfGetMarkers;
  window.wfAutoCue    = wfAutoCue;

})();
