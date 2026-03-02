/**
 * onix-waveform.js — Editor Visual de Frecuencia · Ónix FM
 * =========================================================
 */
; (function () {
  'use strict';

  function ONIX_WF_ID(id) { return document.getElementById(id); }

  var _C = {
    wave: '#FF6600',
    progress: '#FF8C33',
    cursor: '#FFFFFF',
    intro: 'rgba(0,255,100,0.22)',
    outro: 'rgba(255,40,40,0.22)',
    hook: 'rgba(180,100,255,0.22)',
  };

  var _ws = null;
  var _rp = null;
  var _reg = {};
  var _dur = 0;
  var _raf = null;
  var _playing = false;
  var _inited = false;
  var _tries = 0;

  function _fmt(s) {
    if (!s || isNaN(s) || s < 0) return '0:00.0';
    var m = Math.floor(s / 60);
    var q = Math.floor(s % 60);
    var d = Math.floor((s % 1) * 10);
    return m + ':' + (q < 10 ? '0' : '') + q + '.' + d;
  }

  function _r1(n) { return Math.round(n * 10) / 10; }

  function _tick() {
    if (!_ws) return;
    var cur = _ws.getCurrentTime();
    var tot = _dur || _ws.getDuration() || 0;
    var c = ONIX_WF_ID('wf-time-cur');
    var t = ONIX_WF_ID('wf-time-total');
    if (c) c.textContent = _fmt(cur);
    if (t) t.textContent = _fmt(tot);
    if (_playing) _raf = requestAnimationFrame(_tick);
  }

  function _icon(playing) {
    var svg = ONIX_WF_ID('wf-play-icon');
    var btn = ONIX_WF_ID('wf-btn-play');
    if (!svg) return;
    if (playing) {
      svg.innerHTML = '<rect x="4" y="2" width="5" height="20" rx="1" fill="currentColor"/>' + '<rect x="15" y="2" width="5" height="20" rx="1" fill="currentColor"/>';
      if (btn) btn.title = 'Pausa';
    } else {
      svg.innerHTML = '<polygon points="5,2 21,12 5,22" fill="currentColor"/>';
      if (btn) btn.title = 'Play';
    }
  }

  function _clearReg() {
    try { if (_rp) _rp.clearRegions(); } catch (e) { }
    _reg = {};
  }

  function _addReg(id, start, end, color) {
    if (!_rp) return null;
    try {
      return _rp.addRegion({ id: id, start: start, end: end, color: color, drag: true, resize: true });
    } catch (e) { return null; }
  }

  function _defaultReg() {
    if (!_rp || !_dur) return;
    _clearReg();
    var d = _dur;
    _reg.intro = _addReg('intro', 0, _r1(Math.min(8, d * 0.1)), _C.intro);
    _reg.outro = _addReg('outro', _r1(Math.max(0, d - 30)), _r1(Math.max(d - 25, d - 3)), _C.outro);
    _reg.hook = _addReg('hook', _r1(d * 0.38), _r1(d * 0.38 + 6), _C.hook);
    _syncIn();
  }

  function _syncIn() {
    var map = { intro: 'input-intro', outro: 'input-outro', hook: 'input-hook' };
    for (var k in map) {
      if (_reg[k]) {
        var el = ONIX_WF_ID(map[k]);
        if (el) el.value = _r1(_reg[k].start);
      }
    }
  }

  function _moveReg(id, newStart) {
    var r = _reg[id];
    if (!r || !_dur) return;
    var w = r.end - r.start;
    var s = Math.max(0, Math.min(newStart, _dur - w));
    try { r.setOptions({ start: s, end: Math.min(_dur, s + w) }); } catch (e) { }
  }

  function _showPanel() {
    var p = ONIX_WF_ID('wf-panel');
    if (!p) return;
    p.style.cssText = 'display:block !important; visibility:visible !important; opacity:1 !important; z-index:99999 !important; position:relative !important;';
  }

  function _hidePanel() {
    var p = ONIX_WF_ID('wf-panel');
    if (p) p.style.cssText = 'display:none !important;';
  }

  function wfInit() {
    if (_inited) return;
    if (typeof WaveSurfer === 'undefined') {
      if (_tries++ < 50) { setTimeout(wfInit, 100); return; }
      console.error('[ÓnixWF] WaveSurfer no disponible. Revisa el CDN.');
      return;
    }
    var RP = (typeof RegionsPlugin !== 'undefined' && RegionsPlugin) || (typeof WaveSurferRegions !== 'undefined' && WaveSurferRegions) || (WaveSurfer.Regions ? WaveSurfer.Regions : null);
    if (!RP) {
      if (_tries++ < 50) { setTimeout(wfInit, 100); return; }
      console.error('[ÓnixWF] Plugin Regions no disponible. Revisa el CDN.');
      return;
    }
    _inited = true;
    _tries = 0;
    try {
      _rp = RP.create();
      _ws = WaveSurfer.create({
        container: '#waveform',
        waveColor: _C.wave,
        progressColor: _C.progress,
        cursorColor: _C.cursor,
        cursorWidth: 2,
        height: 180,
        barWidth: 4,
        barGap: 1,
        barRadius: 2,
        normalize: true,
        interact: true,
        fillParent: true,
        minPxPerSec: 50,
        plugins: [_rp],
      });
      _ws.on('ready', function () {
        _dur = _ws.getDuration();
        var sp = ONIX_WF_ID('wf-loading');
        if (sp) sp.style.display = 'none';
        var c = ONIX_WF_ID('wf-time-cur');
        var t = ONIX_WF_ID('wf-time-total');
        if (c) c.textContent = _fmt(0);
        if (t) t.textContent = _fmt(_dur);
        _defaultReg();
        _ws.zoom(70);
        console.info('[ÓnixWF] Listo — ' + _fmt(_dur));
      });
      _ws.on('play', function () { _playing = true; _icon(true); _raf = requestAnimationFrame(_tick); });
      _ws.on('pause', function () { _playing = false; _icon(false); if (_raf) { cancelAnimationFrame(_raf); _raf = null; } });
      _ws.on('finish', function () { _playing = false; _icon(false); if (_raf) { cancelAnimationFrame(_raf); _raf = null; } var c = ONIX_WF_ID('wf-time-cur'); if (c) c.textContent = _fmt(_dur); });
      _rp.on('region-updated', function (r) {
        var map = { intro: 'input-intro', outro: 'input-outro', hook: 'input-hook' };
        var el = ONIX_WF_ID(map[r.id]);
        if (el) el.value = _r1(r.start);
      });
      console.info('[ÓnixWF] Inicializado.');
    } catch (err) {
      console.error('[ÓnixWF] Error al crear WaveSurfer:', err);
      _inited = false;
    }

    document.addEventListener('click', function (e) {
      if (e.target.closest('#wf-btn-play')) { if (_ws) try { _ws.playPause(); } catch (er) { } return; }
      if (e.target.closest('#wf-btn-stop')) {
        if (_ws) { try { _ws.stop(); } catch (er) { } _playing = false; _icon(false); if (_raf) { cancelAnimationFrame(_raf); _raf = null; } var c = ONIX_WF_ID('wf-time-cur'); if (c) c.textContent = _fmt(0); }
        return;
      }
      if (e.target.closest('#wf-btn-reset')) { if (_dur > 0) _defaultReg(); return; }
      var mb = e.target.closest('[data-wf-mark]');
      if (mb) {
        if (!_ws) return;
        var id = mb.dataset.wfMark;
        var pos = _ws.getCurrentTime();
        _moveReg(id, pos);
        var inputMap = { intro: 'input-intro', outro: 'input-outro', hook: 'input-hook' };
        var el = ONIX_WF_ID(inputMap[id]);
        if (el) el.value = _r1(pos);
        return;
      }
    }, false);

    document.addEventListener('change', function (e) {
      var map = { 'input-intro': 'intro', 'input-outro': 'outro', 'input-hook': 'hook' };
      var id = map[e.target.id];
      if (!id) return;
      var v = parseFloat(e.target.value);
      if (!isNaN(v) && v >= 0) _moveReg(id, v);
    }, false);
  }

  function wfLoadFile(file) {
    if (!_ws) { setTimeout(function () { wfLoadFile(file); }, 200); return; }
    _showPanel();
    var fn = ONIX_WF_ID('wf-filename');
    if (fn) fn.textContent = file.name;
    var sp = ONIX_WF_ID('wf-loading');
    if (sp) sp.style.display = 'flex';
    if (_raf) { cancelAnimationFrame(_raf); _raf = null; }
    _playing = false;
    _dur = 0;
    _reg = {};
    _icon(false);
    var c = ONIX_WF_ID('wf-time-cur');
    var t = ONIX_WF_ID('wf-time-total');
    if (c) c.textContent = '0:00.0';
    if (t) t.textContent = '0:00.0';
    try { if (_ws.isPlaying && _ws.isPlaying()) _ws.stop(); } catch (e) { }
    setTimeout(function () {
      try {
        _ws.loadBlob(file);
      } catch (err) {
        console.error('[ÓnixWF] loadBlob falló:', err);
        var spErr = ONIX_WF_ID('wf-loading');
        if (spErr) spErr.style.display = 'none';
      }
    }, 300);
  }

  function wfDestroy() {
    if (_raf) { cancelAnimationFrame(_raf); _raf = null; }
    if (_ws) {
      try { _ws.destroy(); } catch (e) { }
      _ws = null;
      _rp = null;
      _reg = {};
      _dur = 0;
      _playing = false;
      _inited = false;
    }
    _hidePanel();
    var ids = ['input-intro', 'input-outro', 'input-hook'];
    for (var i = 0; i < ids.length; i++) {
      var el = ONIX_WF_ID(ids[i]);
      if (el) el.value = '';
    }
    var c = ONIX_WF_ID('wf-time-cur');
    var t = ONIX_WF_ID('wf-time-total');
    if (c) c.textContent = '0:00.0';
    if (t) t.textContent = '0:00.0';
    console.info('[ÓnixWF] Destruido.');
  }

  function wfGetMarkers() {
    return {
      intro: _reg.intro ? _r1(_reg.intro.start) : null,
      outro: _reg.outro ? _r1(_reg.outro.start) : null,
      hook: _reg.hook ? _r1(_reg.hook.start) : null,
    };
  }

  window.wfInit = wfInit;
  window.wfLoadFile = wfLoadFile;
  window.wfDestroy = wfDestroy;
  window.wfGetMarkers = wfGetMarkers;

})();
