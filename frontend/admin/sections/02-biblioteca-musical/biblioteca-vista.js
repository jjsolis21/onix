/**
 * biblioteca-vista.js — Ónix FM · Biblioteca musical (Fase A)
 *
 * Organización al estilo Jazler / sistemas grandes:
 *   · Árbol a la izquierda: Bandeja de entrada, tipos y categorías con conteos.
 *   · Tabla virtual: miles de canciones sin trabarse; columnas a elección,
 *     orden por columna, color del género en cada fila e iconos de estado.
 *   · Edición masiva: seleccionar (clic, Mayús+clic, Ctrl+A) y cambiar
 *     género, rotación, subgénero, voz, origen o energía de todas a la vez;
 *     aprobar, devolver a la Bandeja, re-detectar marcas o desactivar.
 *   · Bandeja de entrada: la música recién cargada NO suena en automático
 *     hasta clasificarla (género y rotación) y aprobarla.
 *
 * Reutiliza del Admin: el editor de canción (openModal), la carga por lote
 * (openLoteModal) y las categorías (onixCategories.open) de admin-app.js.
 *
 * API: GET /api/v1/audios?sin_tipo=Cuña · GET /api/v1/config/biblioteca/schema
 *      POST /api/v1/biblioteca/lote · GET /api/v1/audios/{id}/stream
 */
; (function () {
  'use strict';

  const $ = id => document.getElementById(id);
  const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const fmt = s => { s = Math.round(+s || 0); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };
  const guardar = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (_) { } };
  const leer = (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch (_) { return d; } };

  const TIPOS = { Musica: '🎵 Música', Efecto: '⚡ Jingles / ID', Sweeper: '〰 Sweepers', Programa: '🎙 Programas', Tips: '💡 Tips' };
  const PALETA = ['#8e44ad', '#cc0000', '#d35400', '#2980b9', '#16a085', '#6b7a1e', '#c0392b', '#7f8c8d'];

  // Columnas disponibles (la de selección y la de escuchar van siempre)
  const COLUMNAS = [
    { k: 'titulo',  t: 'Título / Artista', w: 'minmax(220px, 3fr)', fija: true },
    { k: 'cat1',    t: 'Género',     w: 'minmax(90px, 1fr)' },
    { k: 'cat2',    t: 'Rotación',   w: 'minmax(90px, 1fr)' },
    { k: 'cat3',    t: 'Subgénero',  w: 'minmax(80px, 1fr)', oculta: true },
    { k: 'voz',     t: 'Voz',        w: '80px' },
    { k: 'origen',  t: 'Origen',     w: '70px', oculta: true },
    { k: 'energia', t: 'Energía',    w: '70px' },
    { k: 'anio',    t: 'Año',        w: '56px' },
    { k: 'duracion', t: 'Dur.',      w: '56px', num: true },
    { k: 'intro',   t: 'Intro',      w: '52px', num: true, oculta: true },
    { k: 'veces',   t: 'Sonó',       w: '52px', num: true },
    { k: 'ultima',  t: 'Última vez', w: '96px' },
    { k: 'estado',  t: 'Estado',     w: '78px' },
  ];

  const S = {
    todos: [], filtrados: [], schema: null, colores: {},
    nodo: { tipo: 'todo' }, q: '', orden: leer('bv_orden', { k: 'artista', dir: 1 }),
    visibles: new Set(leer('bv_cols', COLUMNAS.filter(c => !c.oculta).map(c => c.k))),
    sel: new Set(), ancla: null, cursor: -1, sonando: null, abiertos: leer('bv_grupos', {}),
  };
  let audio = null;

  // ── Datos ────────────────────────────────────────────────
  function normalizar(a) {
    const anio = parseInt(String(a.fecha_lanzamiento || '').slice(0, 4), 10);
    // Categorías agrupadas sin distinguir mayúsculas ("Pop" y "POP" son la misma)
    const may = v => (v === null || v === undefined || v === '') ? '' : String(v).toUpperCase();
    return { ...a, cat1: may(a.cat1), cat2: may(a.cat2), cat3: may(a.cat3), voz: may(a.voz), origen: may(a.origen),
             anio: Number.isFinite(anio) ? anio : null,
             decada: Number.isFinite(anio) ? `${Math.floor(anio / 10) * 10}s` : null,
             veces: a.veces_reproducido || 0, ultima: a.ultima_reproduccion || '',
             intro: a.cue_intro || a.intro || 0, aprobada: a.aprobada ?? 1 };
  }

  async function cargar() {
    const [lista, schema] = await Promise.all([
      fetch('/api/v1/audios?sin_tipo=Cu%C3%B1a&limit=50000').then(r => r.json()),
      fetch('/api/v1/config/biblioteca/schema').then(r => r.json()).catch(() => ({ categorias: [] })),
    ]);
    S.todos = (lista.data || []).map(normalizar);
    S.schema = schema;
    S.colores = {};
    const c1 = (schema.categorias || []).find(c => c.nombre_interno === 'cat1');
    (c1?.valores || []).forEach((v, i) => { S.colores[String(v.valor).toUpperCase()] = v.color || PALETA[i % PALETA.length]; });
    const ids = new Set(S.todos.map(a => a.id));
    S.sel.forEach(id => { if (!ids.has(id)) S.sel.delete(id); });
    pintarArbol(); filtrar(); pintarMasiva();
  }

  // ── Árbol ────────────────────────────────────────────────
  function grupo(clave, titulo, nodos) {
    const cerrado = S.abiertos[clave] === false;
    return `<div class="bv-grupo ${cerrado ? 'cerrado' : ''}" data-grupo="${clave}">
      <button type="button">${titulo}</button><div>${nodos.join('')}</div></div>`;
  }
  function nodo(n, txt, cuenta, extra = '', color = '') {
    const activo = JSON.stringify(n) === JSON.stringify(S.nodo);
    return `<button type="button" class="bv-nodo ${extra} ${activo ? 'activo' : ''}" data-nodo='${esc(JSON.stringify(n))}'>
      ${color ? `<i style="background:${esc(color)}"></i>` : ''}<span>${esc(txt)}</span><small>${cuenta}</small></button>`;
  }
  function contar(campo, soloMusica = true) {
    const m = new Map();
    S.todos.forEach(a => {
      if (soloMusica && a.tipo_audio !== 'Musica') return;
      const v = a[campo] ?? '';
      m.set(v, (m.get(v) || 0) + 1);
    });
    return m;
  }
  function nodosCampo(campo, orden, colorear = false) {
    const m = contar(campo);
    const valores = [...m.keys()].filter(v => v !== '').sort((a, b) => orden ? orden(a, b) : String(a).localeCompare(String(b)));
    const out = valores.map(v => nodo({ campo, valor: v }, String(v), m.get(v), '', colorear ? (S.colores[String(v).toUpperCase()] || '#555') : ''));
    if (m.get('')) out.push(nodo({ campo, valor: '' }, '(sin asignar)', m.get(''), 'bv-nodo--vacio'));
    return out;
  }
  function pintarArbol() {
    const pend = S.todos.filter(a => a.tipo_audio === 'Musica' && !a.aprobada).length;
    const porTipo = contar('tipo_audio', false);
    const etiquetas = Object.fromEntries((S.schema?.categorias || []).map(c => [c.nombre_interno, c.etiqueta_visible]));
    $('bvArbol').innerHTML =
      nodo({ tipo: 'todo' }, 'Toda la biblioteca', S.todos.length) +
      nodo({ tipo: 'bandeja' }, '📥 Bandeja de entrada', pend, pend ? 'bv-nodo--bandeja' : '') +
      grupo('tipo', 'Tipo', Object.entries(TIPOS).map(([k, t]) => nodo({ campo: 'tipo_audio', valor: k }, t, porTipo.get(k) || 0))) +
      grupo('cat1', etiquetas.cat1 || 'Género', nodosCampo('cat1', null, true)) +
      grupo('cat2', etiquetas.cat2 || 'Rotación', nodosCampo('cat2')) +
      grupo('cat3', etiquetas.cat3 || 'Subgénero', nodosCampo('cat3')) +
      grupo('voz', 'Voz', nodosCampo('voz')) +
      grupo('origen', 'Origen', nodosCampo('origen')) +
      grupo('energia', 'Energía', nodosCampo('energia', (a, b) => a - b)) +
      grupo('decada', 'Década', nodosCampo('decada'));
    $('bvResumen').textContent = `${porTipo.get('Musica') || 0} canciones · ${S.todos.length} audios` + (pend ? ` · ${pend} en la Bandeja` : '');
  }

  // ── Filtro y orden ───────────────────────────────────────
  function pasa(a) {
    const n = S.nodo;
    if (n.tipo === 'bandeja' && !(a.tipo_audio === 'Musica' && !a.aprobada)) return false;
    if (n.campo) {
      if (n.campo !== 'tipo_audio' && a.tipo_audio !== 'Musica') return false;
      if ((a[n.campo] ?? '') !== n.valor) return false;
    }
    if (S.q) {
      const t = `${a.titulo} ${a.artista} ${a.album || ''}`.toUpperCase();
      if (!S.q.split(/\s+/).every(p => t.includes(p))) return false;
    }
    return true;
  }
  function clave(a, k) {
    if (k === 'titulo') return `${a.titulo}`.toUpperCase();
    if (k === 'artista') return `${a.artista} ${a.titulo}`.toUpperCase();
    if (k === 'estado') return (a.aprobada ? 1 : 0);
    const v = a[k];
    return typeof v === 'string' ? v.toUpperCase() : (v ?? -Infinity);
  }
  function filtrar() {
    const { k, dir } = S.orden;
    S.filtrados = S.todos.filter(pasa).sort((x, y) => {
      const a = clave(x, k), b = clave(y, k);
      return a < b ? -dir : a > b ? dir : 0;
    });
    const aviso = $('bvAviso');
    if (S.nodo.tipo === 'bandeja') {
      aviso.hidden = false;
      aviso.innerHTML = S.filtrados.length
        ? `📥 Estas canciones <b>no suenan en automático</b> hasta aprobarlas. Selecciónalas, asígnales género y rotación arriba y presiona <b>✓ Aprobar</b>.`
        : '✓ La Bandeja está vacía: todo lo cargado está clasificado y aprobado.';
    } else aviso.hidden = true;
    pintarCabecera(); pintarFilas();
    $('bvConteo').textContent = `${S.filtrados.length} de ${S.todos.length}`;
  }

  // ── Tabla virtual ────────────────────────────────────────
  function columnas() { return COLUMNAS.filter(c => c.fija || S.visibles.has(c.k)); }
  function plantilla() { return ['30px', '34px', ...columnas().map(c => c.w)].join(' '); }

  function pintarCabecera() {
    $('bv-root').style.setProperty('--bv-cols', plantilla());
    const { k, dir } = S.orden;
    const todos = S.filtrados.length && S.filtrados.every(a => S.sel.has(a.id));
    $('bvThead').innerHTML = `<div><input type="checkbox" class="bv-chk" id="bvSelTodo" ${todos ? 'checked' : ''} title="Seleccionar todas las visibles"></div><div></div>` +
      columnas().map(c => {
        const ck = c.k === 'titulo' ? (k === 'artista' ? 'artista' : 'titulo') : c.k;
        const activa = ck === k || (c.k === 'titulo' && (k === 'titulo' || k === 'artista'));
        return `<div data-orden="${c.k}" class="${activa ? (dir > 0 ? 'orden-asc' : 'orden-desc') : ''} ${c.num ? 'bv-num' : ''}"
          title="Ordenar${c.k === 'titulo' ? ' (clic otra vez: por artista)' : ''}">${c.k === 'titulo' && k === 'artista' ? 'Artista / Título' : c.t}</div>`;
      }).join('');
  }

  function celda(a, c) {
    switch (c.k) {
      case 'titulo': return `<div class="bv-c-tit" title="${esc(a.titulo)} — ${esc(a.artista)}"><b>${esc(a.titulo)}</b><small>${esc(a.artista)}</small></div>`;
      case 'cat1': return `<div>${a.cat1 ? `<span class="bv-chip" style="background:${esc(S.colores[String(a.cat1).toUpperCase()] || '#777')}">${esc(a.cat1)}</span>` : ''}</div>`;
      case 'energia': return `<div class="bv-energia" title="Energía ${a.energia ?? '—'}">${a.energia ? '●'.repeat(a.energia) : ''}</div>`;
      case 'duracion': return `<div class="bv-num">${fmt(a.duracion)}</div>`;
      case 'intro': return `<div class="bv-num">${a.intro ? Math.round(a.intro) + 's' : ''}</div>`;
      case 'veces': return `<div class="bv-num">${a.veces || ''}</div>`;
      case 'anio': return `<div>${a.anio ?? ''}</div>`;
      case 'ultima': return `<div class="bv-num" style="text-align:left">${esc((a.ultima || '').slice(0, 10))}</div>`;
      case 'estado': {
        const i = [];
        if (a.tipo_audio === 'Musica' && !a.aprobada) i.push('<span title="En la Bandeja: no suena en automático">📥</span>');
        if (a.marcas_auto === 1) i.push('<span title="Marcas automáticas (inicio y mezcla)">✂</span>');
        else if (a.marcas_auto === 0) i.push('<span title="Marcas puestas a mano">✋</span>');
        else if (a.marcas_auto === -1) i.push('<span title="No se pudieron detectar las marcas">⚠</span>');
        if (a.loudness_estado === 'error') i.push('<span title="No se pudo medir el volumen">🔇</span>');
        else if (a.loudness_estado === 'ok') i.push('<span title="Volumen normalizado">🔊</span>');
        return `<div class="bv-estado">${i.join('')}</div>`;
      }
      default: return `<div title="${esc(a[c.k] ?? '')}">${esc(a[c.k] ?? '')}</div>`;
    }
  }

  function pintarFilas() {
    const sc = $('bvScroll');
    const alto = parseFloat(getComputedStyle($('bv-root')).getPropertyValue('--bv-fila')) || 34;
    $('bvAlto').style.height = `${S.filtrados.length * alto}px`;
    if (!S.filtrados.length) {
      $('bvFilas').style.transform = 'none';
      $('bvFilas').innerHTML = `<div class="bv-vacio">${S.todos.length ? 'Nada coincide con este filtro o búsqueda.' : 'La biblioteca está vacía: carga canciones con «Nueva canción» o «Cargar lote».'}</div>`;
      return;
    }
    const desde = Math.max(0, Math.floor(sc.scrollTop / alto) - 8);
    const hasta = Math.min(S.filtrados.length, desde + Math.ceil(sc.clientHeight / alto) + 16);
    $('bvFilas').style.transform = `translateY(${desde * alto}px)`;
    const cols = columnas();
    let html = '';
    for (let i = desde; i < hasta; i++) {
      const a = S.filtrados[i];
      const color = a.cat1 ? (S.colores[String(a.cat1).toUpperCase()] || '') : '';
      html += `<div class="bv-fila ${S.sel.has(a.id) ? 'sel' : ''} ${i === S.cursor ? 'cursor' : ''}" data-i="${i}" style="--bv-color:${esc(color)}">
        <div><input type="checkbox" class="bv-chk" ${S.sel.has(a.id) ? 'checked' : ''} tabindex="-1"></div>
        <div><button class="bv-playbtn ${S.sonando === a.id ? 'sonando' : ''}" data-play="${a.id}" tabindex="-1">${S.sonando === a.id ? '■' : '▶'}</button></div>
        ${cols.map(c => celda(a, c)).join('')}</div>`;
    }
    $('bvFilas').innerHTML = html;
  }

  // ── Selección y edición masiva ───────────────────────────
  function seleccionar(i, ev) {
    const a = S.filtrados[i];
    if (!a) return;
    if (ev?.shiftKey && S.ancla !== null) {
      const [x, y] = [Math.min(S.ancla, i), Math.max(S.ancla, i)];
      for (let k = x; k <= y; k++) S.sel.add(S.filtrados[k].id);
    } else {
      S.sel.has(a.id) ? S.sel.delete(a.id) : S.sel.add(a.id);
      S.ancla = i;
    }
    S.cursor = i;
    pintarFilas(); pintarCabecera(); pintarMasiva();
  }
  function opcionesCategoria(campo, etiqueta) {
    const c = (S.schema?.categorias || []).find(x => x.nombre_interno === campo);
    return `<option value="">${etiqueta}…</option>` + (c?.valores || []).map(v => `<option>${esc(v.valor)}</option>`).join('') +
      `<option value="__quitar">(quitar)</option>`;
  }
  function pintarMasiva() {
    const n = S.sel.size;
    $('bvMasiva').hidden = !n;
    $('bvSelN').textContent = n;
    if (!n) return;
    const etq = Object.fromEntries((S.schema?.categorias || []).map(c => [c.nombre_interno, c.etiqueta_visible]));
    document.querySelectorAll('#bvMasiva select[data-campo]').forEach(s => {
      const c = s.dataset.campo;
      if (['cat1', 'cat2', 'cat3', 'voz'].includes(c) && !s.dataset.listo) {
        s.innerHTML = opcionesCategoria(c, etq[c] || c); s.dataset.listo = '1';
      }
      s.value = '';
    });
  }
  async function lote(cuerpo, mensaje) {
    const ids = [...S.sel];
    const r = await fetch('/api/v1/biblioteca/lote', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ids, ...cuerpo }),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) { aviso(typeof d.detail === 'string' ? d.detail : `Error ${r.status}`, 'error'); return null; }
    if (d.sin_clasificar?.length) {
      aviso(`${d.sin_clasificar.length} sin género o rotación: siguen en la Bandeja. ${mensaje ? '' : ''}`, 'error');
      S.sel = new Set(d.sin_clasificar);           // quedan seleccionadas las que faltan
    } else if (mensaje) aviso(mensaje(ids.length), 'success');
    await cargar();
    if (typeof loadAudios === 'function') loadAudios();   // el editor usa la lista de admin-app
    return d;
  }
  function aviso(msg, tipo = 'info') {
    if (typeof showToast === 'function') showToast(msg, tipo); else console.info(msg);
  }

  // ── Escuchar ─────────────────────────────────────────────
  function escuchar(id) {
    if (!audio) {
      audio = new Audio();
      audio.addEventListener('timeupdate', () => {
        $('bvProgFill').style.width = audio.duration ? `${audio.currentTime / audio.duration * 100}%` : '0';
      });
      audio.addEventListener('ended', () => parar());
    }
    if (S.sonando === id) { parar(); return; }
    const a = S.todos.find(x => x.id === id);
    audio.src = `/api/v1/audios/${id}/stream`;
    audio.play().catch(() => aviso('No se pudo reproducir el archivo', 'error'));
    S.sonando = id;
    $('bvPlay').textContent = '■';
    $('bvPlayTxt').textContent = a ? `${a.titulo} — ${a.artista}` : '';
    pintarFilas();
  }
  function parar() {
    if (audio) audio.pause();
    S.sonando = null;
    $('bvPlay').textContent = '▶';
    $('bvProgFill').style.width = '0';
    pintarFilas();
  }

  // ── Editor (admin-app.js) ────────────────────────────────
  async function editar(id) {
    if (typeof openModal !== 'function') return;
    if (typeof library !== 'undefined' && !library.some(t => t.id === id) && typeof loadAudios === 'function') await loadAudios();
    parar();
    openModal(id);
  }

  // ── CSV ──────────────────────────────────────────────────
  function exportar() {
    const cols = ['id', 'titulo', 'artista', 'album', 'cat1', 'cat2', 'cat3', 'voz', 'origen', 'energia', 'anio', 'duracion', 'veces', 'ultima', 'aprobada', 'archivo_path'];
    const q = v => `"${String(v ?? '').replace(/"/g, '""')}"`;
    const csv = '﻿' + [cols.join(';'), ...S.filtrados.map(a => cols.map(c => q(a[c])).join(';'))].join('\r\n');
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
    const l = document.createElement('a');
    l.href = url; l.download = `biblioteca-onix-${new Date().toISOString().slice(0, 10)}.csv`;
    l.click(); setTimeout(() => URL.revokeObjectURL(url), 2000);
  }

  // ── Eventos ──────────────────────────────────────────────
  function enlazar() {
    let t;
    $('bvBuscar').addEventListener('input', e => {
      clearTimeout(t);
      t = setTimeout(() => { S.q = e.target.value.trim().toUpperCase(); S.cursor = -1; $('bvScroll').scrollTop = 0; filtrar(); }, 120);
    });
    $('bvArbol').addEventListener('click', e => {
      const g = e.target.closest('.bv-grupo > button');
      if (g) {
        const div = g.parentElement;
        div.classList.toggle('cerrado');
        S.abiertos[div.dataset.grupo] = !div.classList.contains('cerrado');
        guardar('bv_grupos', S.abiertos);
        return;
      }
      const n = e.target.closest('.bv-nodo');
      if (!n) return;
      S.nodo = JSON.parse(n.dataset.nodo);
      S.cursor = -1; $('bvScroll').scrollTop = 0;
      document.querySelectorAll('.bv-nodo').forEach(x => x.classList.toggle('activo', x === n));
      filtrar();
    });
    $('bvScroll').addEventListener('scroll', () => requestAnimationFrame(pintarFilas));
    window.addEventListener('resize', () => pintarFilas());
    $('bvThead').addEventListener('click', e => {
      if (e.target.id === 'bvSelTodo') {
        const marcar = e.target.checked;
        S.filtrados.forEach(a => marcar ? S.sel.add(a.id) : S.sel.delete(a.id));
        pintarFilas(); pintarMasiva(); return;
      }
      const h = e.target.closest('[data-orden]');
      if (!h) return;
      let k = h.dataset.orden;
      if (k === 'titulo') k = S.orden.k === 'titulo' ? 'artista' : (S.orden.k === 'artista' ? 'titulo' : 'artista');
      S.orden = { k, dir: S.orden.k === k ? -S.orden.dir : 1 };
      if (h.dataset.orden === 'titulo' && S.orden.k !== k) S.orden.dir = 1;
      guardar('bv_orden', S.orden);
      filtrar();
    });
    $('bvFilas').addEventListener('click', e => {
      const p = e.target.closest('[data-play]');
      if (p) { escuchar(+p.dataset.play); return; }
      const f = e.target.closest('.bv-fila');
      if (!f) return;
      if (e.target.classList.contains('bv-chk') || e.shiftKey || e.ctrlKey || e.metaKey) { seleccionar(+f.dataset.i, e); return; }
      // Sin redibujar: si no, el segundo clic del doble clic cae en una fila nueva y no abre el editor
      S.cursor = +f.dataset.i;
      document.querySelectorAll('.bv-fila.cursor').forEach(x => x.classList.remove('cursor'));
      f.classList.add('cursor');
    });
    $('bvFilas').addEventListener('dblclick', e => {
      const f = e.target.closest('.bv-fila');
      if (f && !e.target.closest('[data-play], .bv-chk')) editar(S.filtrados[+f.dataset.i].id);
    });
    $('bvTabla').addEventListener('keydown', e => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'a') {
        e.preventDefault(); S.filtrados.forEach(a => S.sel.add(a.id)); pintarFilas(); pintarCabecera(); pintarMasiva();
      } else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        S.cursor = Math.max(0, Math.min(S.filtrados.length - 1, S.cursor + (e.key === 'ArrowDown' ? 1 : -1)));
        const alto = 34, sc = $('bvScroll');
        if (S.cursor * alto < sc.scrollTop) sc.scrollTop = S.cursor * alto;
        if ((S.cursor + 1) * alto > sc.scrollTop + sc.clientHeight) sc.scrollTop = (S.cursor + 1) * alto - sc.clientHeight;
        pintarFilas();
      } else if (e.key === ' ' && S.cursor >= 0) { e.preventDefault(); seleccionar(S.cursor, e); }
      else if (e.key === 'Enter' && S.cursor >= 0) editar(S.filtrados[S.cursor].id);
      else if (e.key === 'Escape') { S.sel.clear(); pintarFilas(); pintarCabecera(); pintarMasiva(); }
    });
    document.addEventListener('keydown', e => {
      if (e.key === '/' && $('bv-root')?.offsetParent && !['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName)) {
        e.preventDefault(); $('bvBuscar').focus();
      }
    });

    // Edición masiva
    $('bvMasiva').addEventListener('change', e => {
      const s = e.target.closest('select[data-campo]');
      if (!s || s.value === '') return;
      const valor = s.value === '__quitar' ? null : s.value;
      const nombre = s.options[s.selectedIndex].text;
      lote({ cambios: { [s.dataset.campo]: valor } }, n => `✓ ${n} canción(es): ${s.title} = ${valor === null ? '(quitado)' : nombre}`);
    });
    $('bvAprobar').addEventListener('click', () => lote({ cambios: { aprobada: 1 } }, n => `✓ ${n} aprobada(s): ya pueden salir al aire`));
    $('bvBandeja').addEventListener('click', () => lote({ cambios: { aprobada: 0 } }, n => `📥 ${n} devuelta(s) a la Bandeja`));
    $('bvMarcas').addEventListener('click', () => lote({ accion: 'redetectar_marcas' }, n => `✂ ${n} se volverán a analizar en segundo plano`));
    $('bvDesactivar').addEventListener('click', () => {
      if (confirm(`¿Desactivar ${S.sel.size} audio(s)? Dejan de aparecer y de sonar.`)) {
        lote({ accion: 'desactivar' }, n => `${n} desactivado(s)`).then(d => { if (d) S.sel.clear(); pintarMasiva(); });
      }
    });
    $('bvDeselect').addEventListener('click', () => { S.sel.clear(); pintarFilas(); pintarCabecera(); pintarMasiva(); });

    // Botones de cabecera (ventanas de admin-app.js)
    $('bvNueva').addEventListener('click', () => typeof openModal === 'function' && openModal());
    $('bvLote').addEventListener('click', () => typeof openLoteModal === 'function' && openLoteModal());
    $('bvCategorias').addEventListener('click', () => window.onixCategories?.open?.());
    $('bvExportar').addEventListener('click', exportar);
    $('bvColumnas').addEventListener('click', e => {
      const m = $('bvMenuCols');
      if (!m.hidden) { m.hidden = true; return; }
      m.innerHTML = COLUMNAS.filter(c => !c.fija).map(c =>
        `<label><input type="checkbox" value="${c.k}" ${S.visibles.has(c.k) ? 'checked' : ''}> ${c.t}</label>`).join('');
      const r = e.currentTarget.getBoundingClientRect();
      m.style.top = `${r.bottom + 4}px`; m.style.left = `${Math.max(8, r.right - 180)}px`;
      m.hidden = false;
    });
    $('bvMenuCols').addEventListener('change', e => {
      e.target.checked ? S.visibles.add(e.target.value) : S.visibles.delete(e.target.value);
      guardar('bv_cols', [...S.visibles]);
      pintarCabecera(); pintarFilas();
    });
    document.addEventListener('click', e => {
      if (!e.target.closest('#bvMenuCols, #bvColumnas')) $('bvMenuCols') && ($('bvMenuCols').hidden = true);
    });
    $('bvPlay').addEventListener('click', () => { if (S.sonando) parar(); else if (S.cursor >= 0) escuchar(S.filtrados[S.cursor].id); });
    $('bvProg').addEventListener('click', e => {
      if (!audio?.duration) return;
      const r = e.currentTarget.getBoundingClientRect();
      audio.currentTime = (e.clientX - r.left) / r.width * audio.duration;
    });

    // Al cerrar el editor, el lote o las categorías: recargar (puede haber cambios)
    ['jz-overlay', 'lote-overlay', 'cat-overlay'].forEach(id => {
      const el = $(id);
      if (!el) return;
      let abierto = el.classList.contains('active');
      new MutationObserver(() => {
        const ahora = el.classList.contains('active');
        if (abierto && !ahora) setTimeout(cargar, 300);
        abierto = ahora;
      }).observe(el, { attributes: true, attributeFilter: ['class'] });
    });
  }

  function init() {
    if (!$('bv-root') || $('bv-root').dataset.listo) return;
    $('bv-root').dataset.listo = '1';
    enlazar();
    cargar().catch(err => aviso(`No se pudo cargar la biblioteca: ${err.message}`, 'error'));
  }
  window.BibliotecaVista = { init, recargar: () => cargar() };
  init();
})();
