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
    { k: 'energia', t: 'Energía',    w: '84px' },
    { k: 'prioridad', t: 'Prior.',   w: '76px' },
    { k: 'bpm',     t: 'BPM',        w: '50px', num: true, oculta: true },
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
    sel: new Set(), ancla: null, cursor: -1, sonando: null,
    verFicha: leer('bv_ficha', true), fichaId: null,
    calidad: {}, papelera: [],
    f: {}, bpm: {}, campoQ: 'todos', rapida: false, soloMarcados: false, autoplay: leer('bv_autoplay', false),
  };
  // Filtros combinables del panel izquierdo (como Jazler SoHo)
  const FILTROS = [
    ['tipo_audio', 'Tipo'], ['cat1', null], ['cat2', null], ['cat3', null], ['voz', 'Voz'],
    ['origen', 'Origen'], ['energia', 'Energía'], ['prioridad', 'Prioridad'], ['coro', 'Coro'], ['decada', 'Década'],
  ];
  const PRIORIDAD = { 1: '▲ Alta', 2: '● Normal', 3: '▼ Baja' };
  // Energía = tempo de la canción (se guarda el número)
  const ENERGIA = { 1: '1 · Lenta', 2: '2 · Suave', 3: '3 · Media', 4: '4 · Movida', 5: '5 · Rápida' };
  const CALIDAD = [['duplicados', '⧉ Posibles duplicados'], ['sin_clasificar', '🏷 Sin género o rotación'],
    ['volumen', '🔇 Volumen raro'], ['quemadas', '🔥 Quemadas (7 días)'], ['olvidadas', '💤 Olvidadas (30 días)'],
    ['restringidas', '⏱ Con restricciones']];
  const DIAS = ['Dom', 'Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb'];
  const enPapelera = () => S.nodo.tipo === 'papelera';
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
             intro: a.cue_intro || a.intro || 0, aprobada: a.aprobada ?? 1,
             prioridad: a.prioridad || 2, bpm: a.bpm || null,
             coro: (a.cue_inicio_coro || 0) > 0 ? 'CON CORO MARCADO' : 'SIN CORO MARCADO' };
  }

  async function cargar() {
    const [lista, schema, calidad, papelera] = await Promise.all([
      fetch('/api/v1/audios?sin_tipo=Cu%C3%B1a&limit=50000').then(r => r.json()),
      fetch('/api/v1/config/biblioteca/schema').then(r => r.json()).catch(() => ({ categorias: [] })),
      fetch('/api/v1/biblioteca/calidad').then(r => r.ok ? r.json() : {}).catch(() => ({})),
      fetch('/api/v1/biblioteca/papelera').then(r => r.ok ? r.json() : []).catch(() => []),
    ]);
    S.todos = (lista.data || []).map(normalizar);
    S.calidad = Object.fromEntries(Object.entries(calidad).map(([k, v]) => [k, Array.isArray(v) ? new Set(v) : v]));
    S.papelera = papelera.map(a => normalizar({ ...a, aprobada: 1 }));
    S.schema = schema;
    S.colores = {};
    const c1 = (schema.categorias || []).find(c => c.nombre_interno === 'cat1');
    (c1?.valores || []).forEach((v, i) => { S.colores[String(v.valor).toUpperCase()] = v.color || PALETA[i % PALETA.length]; });
    const ids = new Set((enPapelera() ? S.papelera : S.todos).map(a => a.id));
    S.sel.forEach(id => { if (!ids.has(id)) S.sel.delete(id); });
    pintarArbol(); filtrar(); pintarMasiva();
  }

  // ── Panel izquierdo ─────────────────────────────────────


  function contar(campo, soloMusica = true) {
    const m = new Map();
    S.todos.forEach(a => {
      if (soloMusica && a.tipo_audio !== 'Musica') return;
      const v = a[campo] ?? '';
      m.set(v, (m.get(v) || 0) + 1);
    });
    return m;
  }

  function pintarArbol() {
    const pend = S.todos.filter(a => a.tipo_audio === 'Musica' && !a.aprobada).length;
    const porTipo = contar('tipo_audio', false);
    // "Mostrar": vistas especiales
    const vistas = [['{"tipo":"todo"}', `Toda la biblioteca (${S.todos.length})`],
      ['{"tipo":"bandeja"}', `📥 Bandeja de entrada (${pend})`],
      ...CALIDAD.map(([k, t]) => [JSON.stringify({ tipo: 'ids', clave: k }), `${t} (${S.calidad[k]?.size ?? 0})`]),
      ['{"tipo":"papelera"}', `🗑 Papelera (${S.papelera.length})`]];
    $('bvVista').innerHTML = vistas.map(([v, t]) => `<option value='${esc(v)}'>${esc(t)}</option>`).join('');
    $('bvVista').value = JSON.stringify(S.nodo);
    $('bvVista').classList.toggle('bv-vista-alerta', S.nodo.tipo !== 'todo');
    pintarFiltros();
    $('bvResumen').textContent = `${porTipo.get('Musica') || 0} canciones · ${S.todos.length} audios` + (pend ? ` · ${pend} en la Bandeja` : '');
  }

  /** Desplegables combinables; cada opción dice cuántas quedan con los demás filtros puestos. */
  function pintarFiltros() {
    const etq = Object.fromEntries((S.schema?.categorias || []).map(c => [c.nombre_interno, c.etiqueta_visible]));
    const base = (enPapelera() ? S.papelera : S.todos);
    $('bvFiltros').innerHTML = FILTROS.map(([campo, titulo]) => {
      const nombre = titulo || etq[campo] || campo;
      const cuenta = new Map();
      base.forEach(a => { if (pasa(a, campo)) { const v = a[campo] ?? ''; cuenta.set(v, (cuenta.get(v) || 0) + 1); } });
      const orden = ['energia', 'prioridad'].includes(campo) ? (x, y) => x - y : (x, y) => String(x).localeCompare(String(y));
      const valores = [...cuenta.keys()].filter(v => v !== '' && v !== null).sort(orden);
      const actual = S.f[campo];
      const etiquetaValor = v => campo === 'tipo_audio' ? (TIPOS[v] || v) : campo === 'energia' ? (ENERGIA[v] || v)
        : campo === 'prioridad' ? (PRIORIDAD[v] || v) : v;
      return `<select data-filtro="${campo}" class="${actual !== undefined ? 'bv-filtro-activo' : ''}">
        <option value="">${esc(nombre)} (Todo)</option>
        ${valores.map(v => `<option value="${esc(v)}" ${String(actual) === String(v) ? 'selected' : ''}>${esc(etiquetaValor(v))} (${cuenta.get(v)})</option>`).join('')}
        ${cuenta.get('') ? `<option value="__vacio" ${actual === '' ? 'selected' : ''}>(sin asignar) (${cuenta.get('')})</option>` : ''}
      </select>`;
    }).join('') + `<div class="bv-bpm"><select data-bpm="min" title="BPM desde">${opcionesBpm('desde', S.bpm.min)}</select>
      <select data-bpm="max" title="BPM hasta">${opcionesBpm('hasta', S.bpm.max)}</select></div>`;
  }
  function opcionesBpm(txt, actual) {
    const conBpm = (enPapelera() ? S.papelera : S.todos).filter(a => a.bpm).length;
    return `<option value="">BPM ${txt} (Todo)</option>` + Array.from({ length: 15 }, (_, i) => 60 + i * 10).map(v =>
      `<option value="${v}" ${actual === v ? 'selected' : ''}>BPM ${txt} ${v}</option>`).join('') +
      (conBpm ? '' : '<option disabled>— ninguna canción tiene BPM —</option>');
  }



  // ── Filtro y orden ───────────────────────────────────────
  function pasa(a, ignorar = null) {
    const n = S.nodo;
    for (const [campo, valor] of Object.entries(S.f)) {
      if (campo === ignorar) continue;
      if (String(a[campo] ?? '') !== String(valor)) return false;
    }
    if (S.soloMarcados && !S.sel.has(a.id)) return false;
    if (S.bpm.min && !(a.bpm >= S.bpm.min)) return false;
    if (S.bpm.max && !(a.bpm && a.bpm <= S.bpm.max)) return false;
    if (n.tipo === 'bandeja' && !(a.tipo_audio === 'Musica' && !a.aprobada)) return false;
    if (n.tipo === 'ids' && !S.calidad[n.clave]?.has(a.id)) return false;
    if (n.campo) {
      if (n.campo !== 'tipo_audio' && a.tipo_audio !== 'Musica') return false;
      if ((a[n.campo] ?? '') !== n.valor) return false;
    }
    if (S.q) {
      const t = (S.campoQ === 'todos' ? `${a.titulo} ${a.artista} ${a.album || ''} ${a.cat1} ${a.cat2} ${a.anio ?? ''}`
                                      : String(a[S.campoQ] ?? '')).toUpperCase();
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
    S.filtrados = (enPapelera() ? S.papelera : S.todos).filter(pasa).sort((x, y) => {
      const a = clave(x, k), b = clave(y, k);
      return a < b ? -dir : a > b ? dir : 0;
    });
    const aviso = $('bvAviso');
    if (S.nodo.tipo === 'bandeja') {
      aviso.hidden = false;
      aviso.innerHTML = S.filtrados.length
        ? `📥 Estas canciones <b>no suenan en automático</b> hasta aprobarlas. Selecciónalas, asígnales género y rotación arriba y presiona <b>✓ Aprobar</b>.`
        : '✓ La Bandeja está vacía: todo lo cargado está clasificado y aprobado.';
    } else if (enPapelera()) {
      aviso.hidden = false;
      aviso.innerHTML = S.filtrados.length
        ? '🗑 Papelera: estas canciones no aparecen ni suenan. <b>♻ Restaurar</b> las devuelve; <b>✖ Borrar definitivamente</b> borra también el archivo (no se puede deshacer).'
        : '🗑 La Papelera está vacía.';
    } else if (S.nodo.tipo === 'ids') {
      const txt = { duplicados: 'Misma canción (artista + título) cargada más de una vez. Escucha y elimina la que sobre.',
        sin_clasificar: 'Sin género o sin rotación: los relojes que filtran por categoría no las pueden elegir.',
        volumen: 'No se pudo medir el volumen o está muy por fuera de lo normal: escúchalas antes de que salgan al aire.',
        quemadas: 'Sonaron mucho más que el resto esta semana. Revisa su rotación.',
        olvidadas: 'Aprobadas pero sin sonar en 30 días: quizá ningún reloj las elige (mira su ficha).',
        restringidas: 'Tienen franja horaria, días o tope por día.' }[S.nodo.clave];
      aviso.hidden = false; aviso.textContent = txt || '';
    } else aviso.hidden = true;
    pintarCabecera(); pintarFilas(); pintarFiltros();
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

  function selectRapido(a, campo) {
    let opciones;
    if (campo === 'energia') opciones = ['', '1', '2', '3', '4', '5'];
    else if (campo === 'prioridad') opciones = ['1', '2', '3'];
    else {
      const cat = (S.schema?.categorias || []).find(x => x.nombre_interno === campo);
      opciones = [''].concat((cat?.valores || []).map(v => String(v.valor).toUpperCase()));
      if (a[campo] && !opciones.includes(a[campo])) opciones.push(a[campo]);
    }
    return `<div><select class="bv-rapido" data-rapido="${campo}" data-id="${a.id}">${opciones.map(o =>
      `<option value="${esc(o)}" ${String(a[campo] ?? '') === o ? 'selected' : ''}>${o === '' ? '—' : esc(campo === 'energia' ? ENERGIA[o] : campo === 'prioridad' ? PRIORIDAD[o] : o)}</option>`).join('')}</select></div>`;
  }

  function celda(a, c) {
    if (S.rapida && !enPapelera() && ['cat1', 'cat2', 'voz', 'energia', 'prioridad'].includes(c.k)) return selectRapido(a, c.k);
    switch (c.k) {
      case 'titulo': return `<div class="bv-c-tit" title="${esc(a.titulo)} — ${esc(a.artista)}">
        <span class="bv-tapa" data-tapa="${a.id}">${S.sinTapa?.has(a.id) ? '♪' : `<img loading="lazy" src="/api/v1/audios/${a.id}/caratula" alt="" onerror="this.parentNode.textContent='♪';window.BibliotecaVista&&window.BibliotecaVista.sinTapa(${a.id})">`}</span>
        <b>${esc(a.titulo)}</b><small>${esc(a.artista)}</small></div>`;
      case 'cat1': return `<div>${a.cat1 ? `<span class="bv-chip" style="background:${esc(S.colores[String(a.cat1).toUpperCase()] || '#777')}">${esc(a.cat1)}</span>` : ''}</div>`;
      case 'energia': return `<div class="bv-energia e-${a.energia || ''}" title="Energía ${a.energia ?? '—'}">${a.energia ? ENERGIA[a.energia] : ''}</div>`;
      case 'prioridad': return `<div class="bv-prio p${a.prioridad}">${a.prioridad === 2 ? '' : PRIORIDAD[a.prioridad]}</div>`;
      case 'bpm': return `<div class="bv-num">${a.bpm || ''}</div>`;
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
        if (S.calidad.restringidas?.has(a.id)) i.push('<span title="Tiene restricciones (horas, días o tope)">⏱</span>');
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
      html += `<div class="bv-fila ${color ? 'tenida' : ''} ${S.sel.has(a.id) ? 'sel' : ''} ${i === S.cursor ? 'cursor' : ''}" data-i="${i}" style="--bv-color:${esc(color)}">
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
    // En la papelera solo se restaura o se borra
    const pap = enPapelera();
    document.querySelectorAll('#bvMasiva > select, #bvAprobar, #bvBandeja, #bvMarcas, #bvRestr, #bvDesactivar')
      .forEach(el => { el.hidden = pap; });
    $('bvRestaurar').hidden = !pap; $('bvBorrarDef').hidden = !pap;
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
    // Editor nuevo (editor-cancion.js); si no cargó, el anterior
    if (window.EditorCancion) { parar(); window.EditorCancion.abrir(id); return; }
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

  // ── Ficha lateral (fase B) ──────────────────────────────
  let tFicha, tOnda, onda = null;
  function mostrarFicha(id, forzar = false) {
    if (!S.verFicha || !id || (id === S.fichaId && !forzar)) return;
    S.fichaId = id;
    clearTimeout(tFicha);
    tFicha = setTimeout(async () => {
      try {
        const f = await fetch(`/api/v1/biblioteca/${id}/ficha`).then(r => { if (!r.ok) throw new Error(r.status); return r.json(); });
        if (S.fichaId !== id) return;                 // ya se movió a otra
        pintarFicha(f);
        clearTimeout(tOnda);
        tOnda = setTimeout(() => { if (S.fichaId === id) dibujarOnda(f); }, 350);   // solo si se queda en ella
      } catch (e) { $('bvFicha').innerHTML = `<p class="bv-ficha-vacia">No se pudo cargar la ficha (${esc(e.message)})</p>`; }
    }, 120);
  }

  function pintarFicha(f) {
    const etq = Object.fromEntries((S.schema?.categorias || []).map(c => [c.nombre_interno, c.etiqueta_visible]));
    const h = f.historial, max = Math.max(1, ...h.por_hora_30d);
    const marcas = { 1: '✂ automáticas', 0: '✋ a mano', '-1': '⚠ no se pudieron detectar' }[f.marcas_auto] ?? 'pendientes';
    const volumen = f.loudness_estado === 'ok' ? `${f.loudness_lufs} LUFS · ${f.ganancia_db > 0 ? '+' : ''}${f.ganancia_db ?? 0} dB`
      : (f.loudness_estado === 'error' ? '⚠ no se pudo medir' : 'midiendo…');
    const dato = (t, v) => v !== null && v !== undefined && v !== '' ? `<dt>${t}</dt><dd title="${esc(v)}">${esc(v)}</dd>` : '';
    $('bvFicha').innerHTML = `
      <div class="bv-f-tit">${esc(f.titulo)}</div>
      <div class="bv-f-art">${esc(f.artista)}${f.album ? ` · <span style="color:var(--text-lo)">${esc(f.album)}</span>` : ''}</div>
      ${f.tipo_audio === 'Musica' && !f.aprobada ? `<div class="bv-f-bandeja">📥 En la Bandeja: no suena en automático hasta aprobarla.</div>` : ''}
      <div class="bv-f-acc">
        <button class="bv-btn" data-f="play">${S.sonando === f.id ? '■ Parar' : '▶ Escuchar'}</button>
        <button class="bv-btn" data-f="editar">✎ Editar marcas y datos</button>
        ${f.tipo_audio === 'Musica' && !f.aprobada ? `<button class="bv-btn bv-btn--ok" data-f="aprobar">✓ Aprobar</button>` : ''}
        <button class="bv-btn bv-btn--peligro" data-f="eliminar" title="Mandar a la Papelera (se puede restaurar)">🗑 Eliminar</button>
      </div>
      <div class="bv-onda" id="bvOnda"></div>
      <div class="bv-onda-ley">
        <span style="color:#52b788">▏inicio ${fmt(f.cue_inicio)}</span>
        ${f.cue_intro ? `<span style="color:var(--blue-next)">▏intro ${Math.round(f.cue_intro)}s</span>` : ''}
        ${f.cue_mezcla ? `<span style="color:var(--gold-1)">▏mezcla ${fmt(f.cue_mezcla)}</span>` : ''}
        <span>${fmt(f.duracion)}</span>
      </div>
      ${f.duplicados.length ? `<div class="bv-f-sec"><h4>⚠ Posibles duplicados</h4>${f.duplicados.map(d =>
        `<div class="bv-f-dup" data-f="ir" data-id="${d.id}">${esc(d.titulo)} — ${esc(d.artista)} · ${fmt(d.duracion)} <small>(#${d.id}, ir)</small></div>`).join('')}</div>` : ''}
      <div class="bv-f-sec"><h4>Datos</h4><dl class="bv-f-datos">
        ${dato(etq.cat1 || 'Género', f.cat1)}${dato(etq.cat2 || 'Rotación', f.cat2)}${dato(etq.cat3 || 'Subgénero', f.cat3)}
        ${dato('Voz', f.voz)}${dato('Origen', f.origen)}${dato('Energía', f.energia ? ENERGIA[f.energia] : '')}
        ${dato('Prioridad', PRIORIDAD[f.prioridad || 2])}${dato('Coro', f.coro?.[0] ? `${fmt(f.coro[0])} → ${fmt(f.coro[1])}` : 'sin marcar')}
        ${dato('Año', f.anio)}${dato('BPM', f.bpm)}${dato('Volumen', volumen)}${dato('Marcas', marcas)}
        ${dato('Cargada', (f.creado || '').slice(0, 10))}${dato('Archivo', f.archivo_path)}
      </dl></div>
      <div class="bv-f-sec"><h4>Al aire</h4>
        <div class="bv-f-kpi"><div><b>${h.total}</b><span>veces en total</span></div><div><b>${h.ultimos_30_dias}</b><span>últimos 30 días</span></div></div>
        ${h.desde ? `<p style="font-size:11px;color:var(--text-lo);margin:0 0 6px">Desde ${esc(h.desde.slice(0, 10))}</p>` : ''}
        ${difusion(h.difusion)}
        ${f.en_programa.length ? `<p style="font-size:12px;color:var(--gold-1);margin:6px 0">▶ En el programa de hoy: ${f.en_programa.map(esc).join(', ')}</p>` : ''}
        ${h.ultimas.length ? `<div class="bv-f-ultimas">${h.ultimas.map(u => `<div>${esc(u.slice(0, 16).replace('T', ' '))}</div>`).join('')}</div>` : '<p style="font-size:12px;color:var(--text-lo)">Todavía no salió al aire.</p>'}
      </div>
      <div class="bv-f-sec"><h4>Restricciones</h4>
        <p style="font-size:12px;margin:0 0 6px;color:${textoRestr(f) ? 'var(--gold-1)' : 'var(--text-lo)'}">${esc(textoRestr(f) || 'Puede sonar a cualquier hora, todos los días.')}</p>
        <button class="bv-btn" data-f="restr">⏱ Cambiar restricciones</button>
      </div>
      <div class="bv-f-sec"><h4>Relojes que pueden elegirla</h4>
        ${f.tipo_audio !== 'Musica' ? '<p style="font-size:12px;color:var(--text-lo)">Solo aplica a música.</p>'
          : f.relojes.length ? f.relojes.map(r => `<div class="bv-f-reloj"><i style="background:${esc(r.color)}"></i>
            <div>${esc(r.nombre)}<small>fila ${r.filas.map(x => `${x.fila} (${esc(x.filtros)})`).join(', ')} · ${r.horas_en_grilla} h en la grilla</small></div></div>`).join('')
          : '<p class="bv-f-alerta">Ningún reloj la puede elegir: solo sonará si la pone el operador o en horas sin reloj.</p>'}
      </div>`;
  }

  function dibujarOnda(f) {
    const cont = $('bvOnda');
    if (!cont || typeof WaveSurfer === 'undefined') return;
    try { onda?.destroy(); } catch (_) { }
    onda = WaveSurfer.create({ container: cont, height: 70, waveColor: '#8a96a8', progressColor: '#8a96a8',
                               cursorWidth: 0, interact: false, normalize: true, barWidth: 2, barGap: 1,
                               url: `/api/v1/audios/${f.id}/stream` });
    onda.on('ready', () => {
      const dur = onda.getDuration() || f.duracion || 1;
      [[f.cue_inicio, '#52b788'], [f.cue_intro, 'var(--blue-next)'], [f.cue_mezcla, 'var(--gold-1)']].forEach(([t, color]) => {
        if (!t) return;
        const m = document.createElement('div');
        m.className = 'marca'; m.style.left = `${Math.min(100, t / dur * 100)}%`; m.style.background = color;
        cont.appendChild(m);
      });
    });
    onda.on('error', () => { cont.innerHTML = '<p class="bv-ficha-vacia" style="margin:22px 0">Sin archivo de audio</p>'; });
  }

  /** Análisis de difusión como Jazler: últimos 35 días (filas) × 24 horas (columnas). */
  function difusion(dias) {
    if (!dias?.length) return '';
    const cab = `<div class="bv-dif-f bv-dif-cab"><span></span>${Array.from({ length: 24 }, (_, hh) =>
      `<b>${hh % 3 === 0 ? String(hh).padStart(2, '0') : ''}</b>`).join('')}<em>Tot</em></div>`;
    return `<div class="bv-dif" title="Cada cuadro = una hora de un día; encendido = sonó">${cab}${dias.map(d => {
      const tot = d.horas.reduce((x, y) => x + y, 0);
      const finde = d.dia === 0 || d.dia === 6;
      return `<div class="bv-dif-f ${finde ? 'finde' : ''}"><span>${d.fecha.slice(8, 10)}/${d.fecha.slice(5, 7)} ${DIAS[d.dia].slice(0, 2)}</span>${
        d.horas.map((n, hh) => `<i class="${n ? 'on' : ''}" title="${d.fecha} ${String(hh).padStart(2, '0')}h${n ? `: ${n} vez/veces` : ''}"></i>`).join('')}<em>${tot || ''}</em></div>`;
    }).join('')}</div>`;
  }

  function textoRestr(f) {
    const p = [];
    if (f.restr_desde !== null && f.restr_desde !== undefined && f.restr_hasta !== null && f.restr_hasta !== undefined && f.restr_desde !== f.restr_hasta)
      p.push(`solo de ${String(f.restr_desde).padStart(2, '0')} a ${String(f.restr_hasta).padStart(2, '0')} h`);
    if (f.restr_dias) p.push(`solo ${f.restr_dias.split(',').map(d => DIAS[+d]).join(' ')}`);
    if (f.max_por_dia) p.push(`máximo ${f.max_por_dia} por día`);
    return p.join(' · ');
  }

  /** Ventana para editar restricciones de una o varias canciones. */
  async function editorRestricciones(ids) {
    if (!ids.length) return;
    let actual = {};
    if (ids.length === 1) actual = await fetch(`/api/v1/biblioteca/${ids[0]}/ficha`).then(r => r.json()).catch(() => ({}));
    const horas = sel => `<option value="">—</option>` + Array.from({ length: 24 }, (_, h) =>
      `<option value="${h}" ${sel === h ? 'selected' : ''}>${String(h).padStart(2, '0')}:00</option>`).join('');
    const dias = actual.restr_dias ? actual.restr_dias.split(',').map(Number) : [0, 1, 2, 3, 4, 5, 6];
    const fondo = document.createElement('div');
    fondo.className = 'bv-modal-fondo';
    fondo.innerHTML = `<div class="bv-modal" role="dialog" aria-label="Restricciones">
      <h3>⏱ Restricciones ${ids.length > 1 ? `de ${ids.length} canciones` : `· ${esc(actual.titulo || '')}`}</h3>
      <p class="bv-modal-ayuda">El automático solo las elige dentro de estas reglas (el operador igual puede ponerlas a mano).</p>
      <label>Puede sonar entre las <select data-r="desde">${horas(actual.restr_desde)}</select>
        y las <select data-r="hasta">${horas(actual.restr_hasta)}</select></label>
      <p class="bv-modal-ayuda">Ej.: 20 → 06 = solo de noche. Vacío = a cualquier hora.</p>
      <div class="bv-modal-dias">${[1, 2, 3, 4, 5, 6, 0].map(d =>
        `<label><input type="checkbox" value="${d}" ${dias.includes(d) ? 'checked' : ''}> ${DIAS[d]}</label>`).join('')}</div>
      <label>Máximo de veces por día <input type="number" min="0" max="48" data-r="max" value="${actual.max_por_dia || ''}" placeholder="sin tope"></label>
      <div class="bv-modal-pie">
        <button class="bv-btn" data-r="quitar">Quitar restricciones</button>
        <button class="bv-btn" data-r="cancelar">Cancelar</button>
        <button class="bv-btn bv-btn--pri" data-r="guardar">Guardar</button>
      </div></div>`;
    document.body.appendChild(fondo);
    const val = k => fondo.querySelector(`[data-r="${k}"]`).value;
    const cerrar = () => fondo.remove();
    fondo.addEventListener('click', async e => {
      const r = e.target.dataset?.r;
      if (e.target === fondo || r === 'cancelar') return cerrar();
      if (r !== 'guardar' && r !== 'quitar') return;
      const cambios = r === 'quitar'
        ? { restr_desde: null, restr_hasta: null, restr_dias: null, max_por_dia: null }
        : { restr_desde: val('desde') === '' ? null : +val('desde'), restr_hasta: val('hasta') === '' ? null : +val('hasta'),
            restr_dias: [...fondo.querySelectorAll('.bv-modal-dias input:checked')].map(i => +i.value),
            max_por_dia: val('max') === '' ? null : +val('max') };
      if (r === 'guardar' && !cambios.restr_dias.length) { aviso('Elige al menos un día', 'error'); return; }
      if (r === 'guardar' && (cambios.restr_desde === null) !== (cambios.restr_hasta === null)) { aviso('Elige las dos horas (desde y hasta) o ninguna', 'error'); return; }
      const antes = new Set(S.sel); S.sel = new Set(ids);
      const ok = await lote({ cambios }, n => r === 'quitar' ? `${n} sin restricciones` : `⏱ Restricciones guardadas en ${n} canción(es)`);
      S.sel = ids.length > 1 ? S.sel : antes; pintarMasiva();
      if (ok) { cerrar(); if (S.fichaId) mostrarFicha(S.fichaId, true); }
    });
  }

  function eliminarSeleccion() {
    const n = S.sel.size;
    if (!n || !confirm(`¿Eliminar ${n} canción(es)? Van a la Papelera: dejan de aparecer y de sonar, y se pueden restaurar.`)) return;
    lote({ accion: 'desactivar' }, k => `🗑 ${k} en la Papelera`).then(d => { if (d) { S.sel.clear(); pintarMasiva(); } });
  }

  async function papeleraAccion(accion) {
    const ids = [...S.sel];
    if (!ids.length) return;
    if (accion === 'borrar' && !confirm(`¿Borrar DEFINITIVAMENTE ${ids.length} canción(es)?\n\nSe borra también el archivo de audio del disco. No se puede deshacer.\n(El historial de emisión conserva título y artista.)`)) return;
    const r = await fetch(`/api/v1/biblioteca/${accion === 'borrar' ? 'borrar' : 'restaurar'}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ids }) });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) { aviso(typeof d.detail === 'string' ? d.detail : `Error ${r.status}`, 'error'); return; }
    if (accion === 'borrar') aviso(`✖ ${d.borrados} borrada(s) definitivamente${d.omitidos?.length ? ` · ${d.omitidos.length} no (están en una pauta)` : ''}`, d.omitidos?.length ? 'error' : 'success');
    else aviso(`♻ ${d.restaurados} restaurada(s)`, 'success');
    S.sel.clear();
    await cargar();
    if (typeof loadAudios === 'function') loadAudios();
  }

  /** Ir a otra canción (desde "posibles duplicados"). */
  function irA(id) {
    S.nodo = { tipo: 'todo' }; S.q = ''; $('bvBuscar').value = '';
    filtrar(); pintarArbol();
    const i = S.filtrados.findIndex(a => a.id === id);
    if (i < 0) return;
    S.cursor = i;
    $('bvScroll').scrollTop = Math.max(0, i * 34 - 100);
    pintarFilas();
    mostrarFicha(id);
  }

  // ── Eventos ──────────────────────────────────────────────
  function enlazar() {
    let t;
    $('bvBuscar').addEventListener('input', e => {
      clearTimeout(t);
      t = setTimeout(() => { S.q = e.target.value.trim().toUpperCase(); S.cursor = -1; $('bvScroll').scrollTop = 0; filtrar(); }, 120);
    });
    const cambiarVista = v => {
      const eraPapelera = enPapelera();
      S.nodo = JSON.parse(v);
      if (eraPapelera !== enPapelera()) { S.sel.clear(); pintarMasiva(); }
      S.cursor = -1; $('bvScroll').scrollTop = 0;
      pintarArbol(); filtrar();
    };
    $('bvVista').addEventListener('change', e => cambiarVista(e.target.value));
    $('bvFiltros').addEventListener('change', e => {
      const b = e.target.closest('select[data-bpm]');
      if (b) { S.bpm[b.dataset.bpm] = b.value ? +b.value : undefined; filtrar(); return; }
      const s = e.target.closest('select[data-filtro]');
      if (!s) return;
      if (s.value === '') delete S.f[s.dataset.filtro];
      else S.f[s.dataset.filtro] = s.value === '__vacio' ? '' : (['energia', 'prioridad'].includes(s.dataset.filtro) ? +s.value : s.value);
      S.cursor = -1; $('bvScroll').scrollTop = 0; filtrar();
    });
    $('bvReiniciar').addEventListener('click', () => {
      S.f = {}; S.bpm = {}; S.q = ''; $('bvBuscar').value = ''; S.soloMarcados = false; $('bvSoloMarcados').checked = false;
      cambiarVista('{"tipo":"todo"}');
    });
    $('bvCampoQ').addEventListener('change', e => { S.campoQ = e.target.value; filtrar(); });
    $('bvRapida').addEventListener('change', e => { S.rapida = e.target.checked; pintarFilas(); });
    $('bvSoloMarcados').addEventListener('change', e => { S.soloMarcados = e.target.checked; filtrar(); });
    $('bvAutoPlay').checked = S.autoplay;
    $('bvAutoPlay').addEventListener('change', e => { S.autoplay = e.target.checked; guardar('bv_autoplay', S.autoplay); });
    $('bvEliminar').addEventListener('click', () => {
      if (enPapelera()) { aviso('En la Papelera usa ♻ Restaurar o ✖ Borrar definitivamente', 'info'); return; }
      if (!S.sel.size && S.cursor >= 0) S.sel.add(S.filtrados[S.cursor].id);
      if (!S.sel.size) { aviso('Marca una canción (clic) o selecciónalas primero', 'info'); return; }
      eliminarSeleccion();
    });
    $('bvEstad').addEventListener('click', () => window.ShellNav?.activate('07-estadisticas'));
    $('bvImprimir').addEventListener('click', imprimir);
    $('bvExportarBD').addEventListener('click', () => typeof exportarBD === 'function' && exportarBD());
    $('bvIntegridad').addEventListener('click', () => typeof chequeoIntegridad === 'function' && chequeoIntegridad());
    $('bvFilas').addEventListener('change', async e => {
      const s = e.target.closest('select.bv-rapido');
      if (!s) return;
      const id = +s.dataset.id, campo = s.dataset.rapido, valor = s.value === '' ? null : s.value;
      const r = await fetch('/api/v1/biblioteca/lote', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids: [id], cambios: { [campo]: valor } }) });
      if (!r.ok) { aviso('No se pudo guardar', 'error'); return; }
      const a = S.todos.find(x => x.id === id);
      if (a) a[campo] = campo === 'energia' ? (valor ? +valor : null) : campo === 'prioridad' ? (+valor || 2) : (valor || '');
      pintarFiltros();
      if (S.fichaId === id) mostrarFicha(id, true);
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
      mostrarFicha(S.filtrados[S.cursor]?.id);
      if (S.autoplay && S.sonando !== S.filtrados[S.cursor]?.id && !e.target.closest('select')) escuchar(S.filtrados[S.cursor].id);
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
        mostrarFicha(S.filtrados[S.cursor]?.id);
        if (S.autoplay && S.filtrados[S.cursor] && S.sonando !== S.filtrados[S.cursor].id) escuchar(S.filtrados[S.cursor].id);
      } else if (e.key === ' ' && S.cursor >= 0) { e.preventDefault(); seleccionar(S.cursor, e); }
      else if (e.key === 'Enter' && S.cursor >= 0) editar(S.filtrados[S.cursor].id);
      else if (e.key === 'Escape') { S.sel.clear(); pintarFilas(); pintarCabecera(); pintarMasiva(); }
      else if (e.key === 'Delete' && !enPapelera()) {
        if (!S.sel.size && S.cursor >= 0) S.sel.add(S.filtrados[S.cursor].id);
        if (S.sel.size) eliminarSeleccion();
      }
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
    $('bvDesactivar').addEventListener('click', eliminarSeleccion);
    $('bvRestaurar').addEventListener('click', () => papeleraAccion('restaurar'));
    $('bvBorrarDef').addEventListener('click', () => papeleraAccion('borrar'));
    $('bvRestr').addEventListener('click', () => editorRestricciones([...S.sel]));
    $('bvDeselect').addEventListener('click', () => { S.sel.clear(); pintarFilas(); pintarCabecera(); pintarMasiva(); });

    // Botones de cabecera (ventanas de admin-app.js)
    $('bvNueva').addEventListener('click', () => window.EditorCancion ? window.EditorCancion.abrir(null)
                                                                        : (typeof openModal === 'function' && openModal()));
    $('bvLote').addEventListener('click', () => typeof openLoteModal === 'function' && openLoteModal());
    $('bvCategorias').addEventListener('click', () => window.onixCategories?.open?.());
    $('bvExportar').addEventListener('click', exportar);
    document.querySelector('.bv-cuerpo').classList.toggle('con-ficha', S.verFicha);
    $('bvVerFicha').addEventListener('click', () => {
      S.verFicha = !S.verFicha; guardar('bv_ficha', S.verFicha);
      document.querySelector('.bv-cuerpo').classList.toggle('con-ficha', S.verFicha);
      pintarFilas();
      if (S.verFicha && S.cursor >= 0) mostrarFicha(S.filtrados[S.cursor]?.id, true);
    });
    $('bvFicha').addEventListener('click', e => {
      const b = e.target.closest('[data-f]');
      if (!b) return;
      const id = S.fichaId;
      if (b.dataset.f === 'play') escuchar(id);
      if (b.dataset.f === 'editar') editar(id);
      if (b.dataset.f === 'aprobar') { const antes = new Set(S.sel); S.sel = new Set([id]);
        lote({ cambios: { aprobada: 1 } }, () => '✓ Aprobada: ya puede salir al aire').then(() => { S.sel = antes; pintarMasiva(); mostrarFicha(id, true); }); }
      if (b.dataset.f === 'ir') irA(+b.dataset.id);
      if (b.dataset.f === 'eliminar') { S.sel = new Set([id]); eliminarSeleccion(); }
      if (b.dataset.f === 'restr') editorRestricciones([id]);
    });
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
  /** Imprimir esta lista (como Jazler): lo que se ve, en una hoja limpia. */
  function imprimir() {
    const etq = Object.fromEntries((S.schema?.categorias || []).map(c => [c.nombre_interno, c.etiqueta_visible]));
    const filas = S.filtrados.map((a, i) => `<tr><td>${i + 1}</td><td>${esc(a.artista)}</td><td>${esc(a.titulo)}</td>
      <td>${esc(a.cat1)}</td><td>${esc(a.cat2)}</td><td>${a.anio ?? ''}</td><td>${fmt(a.duracion)}</td><td>${a.veces || ''}</td></tr>`).join('');
    const w = window.open('', '_blank');
    if (!w) { aviso('El navegador bloqueó la ventana de impresión', 'error'); return; }
    w.document.write(`<!doctype html><meta charset="utf-8"><title>Biblioteca ONIX FM</title>
      <style>body{font:12px Arial,sans-serif;margin:20px}h1{font-size:18px;margin:0 0 4px}p{color:#555;margin:0 0 12px}
      table{border-collapse:collapse;width:100%}th,td{border-bottom:1px solid #ccc;padding:3px 6px;text-align:left}
      th{background:#eee}tr:nth-child(even) td{background:#fafafa}</style>
      <h1>ONIX FM · Biblioteca musical</h1><p>${S.filtrados.length} canciones · ${new Date().toLocaleString('es')}</p>
      <table><thead><tr><th>#</th><th>Artista</th><th>Título</th><th>${esc(etq.cat1 || 'Género')}</th><th>${esc(etq.cat2 || 'Rotación')}</th>
      <th>Año</th><th>Dur.</th><th>Sonó</th></tr></thead><tbody>${filas}</tbody></table>`);
    w.document.close(); w.focus(); setTimeout(() => w.print(), 300);
  }

  window.BibliotecaVista = { init, recargar: () => cargar(), restricciones: ids => editorRestricciones(ids),
    sinTapa: id => { (S.sinTapa ??= new Set()).add(id); } };
  init();
})();
