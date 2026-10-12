/**
 * cunas.js — Ónix FM · Biblioteca de cuñas (publicidades)
 *
 * Las cuñas tienen su propia biblioteca, separada de la música (como la base
 * de "Commercials" de Jazler). Son audios con tipo_audio = 'Cuña'; el
 * "artista" es el anunciante.
 *
 *   · Pestañas en Programación de Pautas: "Pautas" | "Biblioteca de cuñas".
 *   · Biblioteca: búsqueda, escuchar, cuántas pautas usan cada cuña,
 *     "Crear pauta" con esa cuña, eliminar y SUBIR VARIAS a la vez.
 *   · Selector para el editor de pautas: CunasBiblioteca.elegir(cliente)
 *     → Promise<{id, titulo, artista} | null>.
 *   · CunasBiblioteca.subir(file, anunciante, onProgress) → audio creado.
 *
 * API: GET/POST /api/v1/audios (tipo=Cuña), DELETE /api/v1/audios/{id},
 *      GET /api/v1/pautas, GET /api/v1/artistas?tipo=Cuña
 */
; (function (global) {
  'use strict';

  const $ = id => document.getElementById(id);
  const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const fmt = s => { s = Math.round(+s || 0); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };
  const TIPO = 'Cuña';

  let cunas = [], usos = {}, audio = null, sonando = null, listo = false;

  // ── Datos ────────────────────────────────────────────────
  async function cargar() {
    const [a, p] = await Promise.all([
      fetch(`/api/v1/audios?tipo=${encodeURIComponent(TIPO)}&limit=50000`).then(r => r.ok ? r.json() : { data: [] }),
      fetch('/api/v1/pautas').then(r => r.ok ? r.json() : []).catch(() => []),
    ]);
    cunas = a.data || [];
    const lista = Array.isArray(p) ? p : (p.data || p.items || []);
    usos = {};
    const hoy = new Date().toISOString().slice(0, 10);
    lista.forEach(x => {
      if (!x.audio_id) return;
      const u = usos[x.audio_id] ??= { total: 0, vigentes: 0 };
      u.total++;
      if (x.activo !== false && (!x.fecha_fin || x.fecha_fin >= hoy)) u.vigentes++;
    });
    const n = $('cunasTabN'); if (n) n.textContent = cunas.length;
    return cunas;
  }

  /** Sube una cuña a la biblioteca. Devuelve el audio creado ({id, titulo, artista…}). */
  function subir(file, anunciante, onProgress) {
    const fd = new FormData();
    const titulo = file.name.replace(/\.[^.]+$/, '').replace(/[_]+/g, ' ').trim().toUpperCase() || 'CUÑA';
    fd.append('titulo', titulo);
    fd.append('artista', (anunciante || 'SIN ANUNCIANTE').trim().toUpperCase());
    fd.append('tipo_audio', TIPO);
    fd.append('file', file);
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', '/api/v1/audios');
      xhr.upload.addEventListener('progress', e => {
        if (e.lengthComputable && onProgress) onProgress(Math.round(e.loaded / e.total * 100));
      });
      xhr.addEventListener('load', () => {
        let j = {}; try { j = JSON.parse(xhr.responseText); } catch (_) { }
        if (xhr.status >= 200 && xhr.status < 300) resolve(j.data || j);
        else reject(new Error(typeof j.detail === 'string' ? j.detail : (j.detail?.mensaje || `Error ${xhr.status}`)));
      });
      xhr.addEventListener('error', () => reject(new Error('Error de red al subir')));
      xhr.send(fd);
    });
  }

  // ── Escuchar ─────────────────────────────────────────────
  function escuchar(id, boton) {
    if (!audio) { audio = new Audio(); audio.addEventListener('ended', parar); }
    if (sonando === id) { parar(); return; }
    parar();
    audio.src = `/api/v1/audios/${id}/stream`;
    audio.play().catch(() => { });
    sonando = id;
    if (boton) boton.innerHTML = ICO('stop2');
  }
  function parar() {
    if (audio) audio.pause();
    sonando = null;
    document.querySelectorAll('.cunas-play').forEach(b => { b.innerHTML = ICO('play2'); });
  }

  // ── Pestañas y biblioteca ───────────────────────────────
  function montar() {
    if (listo || !$('pautasRoot')) return;
    const body = document.querySelector('#pautasRoot .pautas-body');
    if (!body) return;
    listo = true;

    const tabs = document.createElement('div');
    tabs.className = 'cunas-tabs';
    tabs.innerHTML = `
      <button type="button" class="cunas-tab activo" data-tab="pautas">📋 Pautas</button>
      <button type="button" class="cunas-tab" data-tab="cunas">🎙 Biblioteca de cuñas <b id="cunasTabN">0</b></button>`;
    body.parentNode.insertBefore(tabs, body);

    const panel = document.createElement('div');
    panel.className = 'cunas-panel';
    panel.id = 'cunasPanel';
    panel.hidden = true;
    panel.innerHTML = `
      <div class="cunas-barra">
        <input type="search" id="cunasBuscar" placeholder="Buscar cuña o anunciante…" autocomplete="off">
        <input id="cunasAnunciante" list="cunasAnunciantes" placeholder="Anunciante de las que vas a subir" autocomplete="off">
        <datalist id="cunasAnunciantes"></datalist>
        <button type="button" class="btn-primary" id="cunasSubir">⬆ Subir cuñas</button>
        <input type="file" id="cunasArchivos" accept=".mp3,.wav,.ogg,.flac,.aac,.m4a,audio/*" multiple hidden>
      </div>
      <div class="cunas-zona" id="cunasZona">Arrastra aquí los archivos de las cuñas (puedes soltar varios)</div>
      <div class="cunas-subidas" id="cunasSubidas"></div>
      <div class="table-wrap">
        <table class="pautas-table cunas-tabla">
          <thead><tr><th></th><th>Cuña</th><th>Anunciante</th><th>Duración</th><th>Pautas</th><th>Cargada</th><th>Acciones</th></tr></thead>
          <tbody id="cunasFilas"></tbody>
        </table>
      </div>`;
    body.parentNode.insertBefore(panel, body.nextSibling);

    tabs.addEventListener('click', e => {
      const t = e.target.closest('.cunas-tab');
      if (!t) return;
      tabs.querySelectorAll('.cunas-tab').forEach(b => b.classList.toggle('activo', b === t));
      const enCunas = t.dataset.tab === 'cunas';
      body.hidden = enCunas;
      const stats = $('pautasStats'); if (stats) stats.hidden = enCunas;
      panel.hidden = !enCunas;
      if (enCunas) refrescar(); else parar();
    });
    $('cunasBuscar').addEventListener('input', pintar);
    $('cunasSubir').addEventListener('click', () => $('cunasArchivos').click());
    $('cunasArchivos').addEventListener('change', e => { subirVarias([...e.target.files]); e.target.value = ''; });
    const zona = $('cunasZona');
    zona.addEventListener('dragover', e => { e.preventDefault(); zona.classList.add('encima'); });
    zona.addEventListener('dragleave', () => zona.classList.remove('encima'));
    zona.addEventListener('drop', e => {
      e.preventDefault(); zona.classList.remove('encima');
      subirVarias([...e.dataTransfer.files].filter(f => f.type.startsWith('audio/') || /\.(mp3|wav|ogg|flac|aac|m4a)$/i.test(f.name)));
    });
    $('cunasFilas').addEventListener('click', async e => {
      const b = e.target.closest('button[data-acc]');
      if (!b) return;
      const c = cunas.find(x => x.id === +b.dataset.id);
      if (!c) return;
      if (b.dataset.acc === 'play') escuchar(c.id, b);
      if (b.dataset.acc === 'pauta') {
        parar();
        global.PautasController?.nuevaConCuna?.(c);
      }
      if (b.dataset.acc === 'borrar') {
        const u = usos[c.id]?.vigentes || 0;
        if (u) { alert(`"${c.titulo}" está en ${u} pauta(s) vigente(s). Quítala de esas pautas antes de borrarla.`); return; }
        if (!confirm(`¿Eliminar la cuña "${c.titulo}" de ${c.artista}?`)) return;
        const r = await fetch(`/api/v1/audios/${c.id}`, { method: 'DELETE' });
        if (!r.ok) { const d = await r.json().catch(() => ({})); alert(d.detail || `Error ${r.status}`); return; }
        refrescar();
      }
    });
    cargar();
  }

  async function refrescar() {
    await cargar();
    pintar();
    try {
      const an = await fetch(`/api/v1/artistas?tipo=${encodeURIComponent(TIPO)}`).then(r => r.ok ? r.json() : []);
      $('cunasAnunciantes').innerHTML = an.map(a => `<option value="${esc(a.artista)}">`).join('');
    } catch (_) { }
  }

  function pintar() {
    const q = ($('cunasBuscar')?.value || '').trim().toUpperCase();
    const lista = cunas.filter(c => !q || `${c.titulo} ${c.artista}`.toUpperCase().includes(q));
    $('cunasFilas').innerHTML = lista.length ? lista.map(c => {
      const u = usos[c.id] || { total: 0, vigentes: 0 };
      return `<tr>
        <td><button class="cunas-play" data-acc="play" data-id="${c.id}" title="Escuchar">${ICO(sonando === c.id ? 'stop2' : 'play2')}</button></td>
        <td><b>${esc(c.titulo)}</b></td><td>${esc(c.artista)}</td>
        <td class="mono">${fmt(c.duracion)}</td>
        <td>${u.vigentes ? `<span class="cunas-uso">${u.vigentes} vigente${u.vigentes !== 1 ? 's' : ''}</span>` : (u.total ? `${u.total} vencida${u.total !== 1 ? 's' : ''}` : '<span class="cunas-libre">sin pauta</span>')}</td>
        <td class="mono">${esc((c.created_at || '').slice(0, 10))}</td>
        <td class="cunas-acc">
          <button data-acc="pauta" data-id="${c.id}" title="Crear una pauta con esta cuña">＋ Pauta</button>
          <button data-acc="borrar" data-id="${c.id}" class="peligro" title="Eliminar">✕</button>
        </td></tr>`;
    }).join('') : `<tr><td colspan="7" class="cunas-vacio">${cunas.length ? 'Ninguna coincide con la búsqueda' : 'Aún no hay cuñas. Súbelas con el botón o arrastrándolas aquí arriba.'}</td></tr>`;
  }

  async function subirVarias(files) {
    if (!files.length) return;
    const anunciante = $('cunasAnunciante').value.trim();
    if (!anunciante && !confirm('No escribiste el anunciante. ¿Subirlas como "SIN ANUNCIANTE"? (luego se puede editar)')) return;
    const caja = $('cunasSubidas');
    caja.innerHTML = files.map((f, i) => `<div class="cunas-subida" id="cs${i}"><span>${esc(f.name)}</span><i><b></b></i><em>en espera</em></div>`).join('');
    let ok = 0;
    for (let i = 0; i < files.length; i++) {
      const fila = $(`cs${i}`), barra = fila.querySelector('b'), est = fila.querySelector('em');
      est.textContent = 'subiendo…';
      try {
        await subir(files[i], anunciante, pct => { barra.style.width = `${pct}%`; });
        barra.style.width = '100%'; est.textContent = '✓ cargada'; fila.classList.add('ok'); ok++;
      } catch (err) {
        est.textContent = `✗ ${err.message}`; fila.classList.add('error');
      }
    }
    await refrescar();
    setTimeout(() => { if (caja) caja.innerHTML = `<div class="cunas-resumen">✓ ${ok} de ${files.length} cuña(s) cargadas</div>`; }, 1500);
  }

  // ── Selector para el editor de pautas ───────────────────
  function elegir(cliente) {
    return new Promise(async resolve => {
      await cargar();
      const fondo = document.createElement('div');
      fondo.className = 'cunas-selector-fondo';
      fondo.innerHTML = `
        <div class="cunas-selector" role="dialog" aria-label="Elegir cuña">
          <div class="cunas-selector__cab"><h3>Elegir cuña de la biblioteca</h3>
            <button type="button" class="modal-close" data-cerrar aria-label="Cerrar">✕</button></div>
          <input type="search" placeholder="Buscar cuña o anunciante…" value="${esc(cliente || '')}">
          <div class="cunas-selector__lista"></div>
          <p class="cunas-selector__pie">¿No está? Ciérrala y usa <b>Subir archivo</b>, o cárgala en la pestaña <b>Biblioteca de cuñas</b>.</p>
        </div>`;
      document.body.appendChild(fondo);
      const input = fondo.querySelector('input'), lista = fondo.querySelector('.cunas-selector__lista');
      const cerrar = r => { parar(); fondo.remove(); resolve(r); };
      const pintarLista = () => {
        const q = input.value.trim().toUpperCase();
        const l = cunas.filter(c => !q || `${c.titulo} ${c.artista}`.toUpperCase().includes(q));
        lista.innerHTML = l.length ? l.map(c => `
          <div class="cunas-op" data-id="${c.id}">
            <button class="cunas-play" data-play="${c.id}" title="Escuchar">${ICO('play2')}</button>
            <div><b>${esc(c.titulo)}</b><small>${esc(c.artista)} · ${fmt(c.duracion)}</small></div>
          </div>`).join('') : '<p class="cunas-vacio">Ninguna cuña coincide.</p>';
      };
      input.addEventListener('input', pintarLista);
      fondo.addEventListener('click', e => {
        if (e.target === fondo || e.target.closest('[data-cerrar]')) return cerrar(null);
        const p = e.target.closest('[data-play]');
        if (p) { escuchar(+p.dataset.play, p); return; }
        const op = e.target.closest('.cunas-op');
        if (op) cerrar(cunas.find(c => c.id === +op.dataset.id) || null);
      });
      fondo.addEventListener('keydown', e => { if (e.key === 'Escape') cerrar(null); });
      pintarLista();
      // Si el filtro por cliente no encuentra nada, mostrar todas
      if (cliente && !lista.querySelector('.cunas-op')) { input.value = ''; pintarLista(); }
      input.focus();
    });
  }

  global.CunasBiblioteca = { montar, refrescar, elegir, subir };
})(window);
