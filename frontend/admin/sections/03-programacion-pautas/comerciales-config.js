/**
 * comerciales-config.js — Ónix FM · Admin › Comerciales
 *
 * «Config. de comerciales» (como las Propiedades de spot de Jazler SoHo), en una sola ventana con pestañas:
 *   · General       → tandas: minutos, inicio inmediato, cierre, duración máxima y tolerancia.
 *   · Prioridades   → orden de las secciones de la tanda (ID, cortinas, comerciales) y su tipo.
 *   · Clientes / Agencias → catálogo con contacto; al renombrar se actualizan sus spots.
 * y «Chequeo de integridad»: spots con el audio perdido, sin horarios, vencidos, sin rubro…
 *
 * API: /api/v1/config/motor · /api/v1/tandas/secciones · /api/v1/comerciales/catalogo/{clientes|agencias}
 *      /api/v1/comerciales/chequeo
 * Expone: window.ComercialesCatalogo { clientes, agencias, refrescar() } para las listas del formulario del spot.
 */
(function (global) {
  'use strict';
  const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const json = (m, b) => ({ method: m, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b) });
  async function api(url, opt) {
    const r = await fetch(url, opt);
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(Array.isArray(d.detail) ? d.detail.map(x => x.msg).join('; ') : (d.detail || `Error ${r.status}`));
    return d;
  }

  // ── Catálogo para las listas del formulario del spot ─────────────────────
  const Catalogo = global.ComercialesCatalogo = { clientes: [], agencias: [], async refrescar() {
    try {
      const [c, a] = await Promise.all([api('/api/v1/comerciales/catalogo/clientes'), api('/api/v1/comerciales/catalogo/agencias')]);
      this.clientes = c.items.map(x => x.nombre); this.agencias = a.items.map(x => x.nombre);
    } catch (_) { /* sin catálogo todavía */ }
    const unir = (id, extra) => {
      const dl = document.getElementById(id);
      if (!dl) return;
      const ya = [...dl.querySelectorAll('option')].map(o => o.value);
      dl.innerHTML = [...new Set([...extra, ...ya])].sort().map(n => `<option value="${esc(n)}">`).join('');
    };
    unir('cmClientes', this.clientes); unir('cmAgencias', this.agencias);
  } };

  // ── Ventana base ─────────────────────────────────────────────────────────
  function ventana(titulo, ancho = 880) {
    document.getElementById('ccfFondo')?.remove();
    const f = document.createElement('div');
    f.id = 'ccfFondo'; f.className = 'ccf-fondo';
    f.innerHTML = `<div class="ccf-caja" style="width:min(${ancho}px,96vw)" role="dialog" aria-label="${esc(titulo)}">
      <div class="ccf-cab"><h3>${esc(titulo)}</h3><button class="ccf-x" data-cerrar title="Cerrar (Esc)">✕</button></div>
      <div class="ccf-cuerpo"></div></div>`;
    document.body.appendChild(f);
    const cerrar = () => { f.remove(); document.removeEventListener('keydown', tecla, true); };
    const tecla = e => { if (e.key === 'Escape') { e.stopPropagation(); cerrar(); } };
    document.addEventListener('keydown', tecla, true);
    f.addEventListener('mousedown', e => { if (e.target === f) cerrar(); });
    f.querySelector('[data-cerrar]').addEventListener('click', cerrar);
    return { f, cuerpo: f.querySelector('.ccf-cuerpo'), cerrar };
  }
  const msgEn = (el, t, ok = true) => { el.textContent = t; el.className = `ccf-msg ${ok ? 'ok' : 'err'}`; };

  // ══════════ Config. de comerciales ══════════
  function abrirConfig(pestana = 'general') {
    const { cuerpo } = ventana('⚙ Config. de comerciales', 940);
    cuerpo.innerHTML = `
      <nav class="ccf-tabs">
        <button data-t="general">General</button><button data-t="prioridades">Prioridades</button>
        <button data-t="clientes">Clientes</button><button data-t="agencias">Agencias</button>
      </nav><div class="ccf-pagina"></div>`;
    const pag = cuerpo.querySelector('.ccf-pagina');
    const ir = t => {
      cuerpo.querySelectorAll('.ccf-tabs button').forEach(b => b.classList.toggle('on', b.dataset.t === t));
      ({ general, prioridades, clientes: () => catalogo('clientes'), agencias: () => catalogo('agencias') })[t]();
    };
    cuerpo.querySelector('.ccf-tabs').addEventListener('click', e => { const b = e.target.closest('button[data-t]'); if (b) ir(b.dataset.t); });

    // ── General: tandas ──
    async function general() {
      pag.innerHTML = '<p class="ccf-hint">Cargando…</p>';
      let c;
      try { c = await api('/api/v1/config/motor'); } catch (e) { pag.innerHTML = `<p class="ccf-msg err">${esc(e.message)}</p>`; return; }
      const campo = (id, t, ayuda, v, tipo = 'text') => `<div class="ccf-campo"><label for="${id}">${t}</label>
        <input id="${id}" type="${tipo}" value="${esc(v ?? '')}"><p>${ayuda}</p></div>`;
      pag.innerHTML = `
        <h4>En modo automático</h4>
        ${campo('ccfMin', 'Minutos de las tandas', 'En qué minuto de cada hora sale una tanda, separados por coma (por ejemplo <b>15,45</b>). '
          + 'Salen en el primer cambio de pista después de ese minuto.', c.tandas_minutos)}
        ${campo('ccfInm', 'Tandas con inicio inmediato', 'Minutos cuya tanda corta la canción con fade y sale en su hora exacta (por ejemplo <b>0</b> para la hora en punto). Vacío = ninguna.', c.tandas_inmediatas)}
        ${campo('ccfCie', 'Tandas al cierre', 'Minutos en que la tanda <b>termina</b> en vez de empezar: el bloque arranca antes para acabar en punto. Vacío = ninguna.', c.tandas_cierre)}
        ${campo('ccfMax', 'Duración máxima de la tanda (segundos)', 'Si una tanda dura más, el Studio avisa. 0 = sin límite.', c.tanda_max_seg, 'number')}
        ${campo('ccfTol', 'Tolerancia de tanda (segundos)', 'Cuánto puede atrasarse una tanda esperando que termine la canción en curso antes de buscar otra que encaje.', c.tolerancia_tanda_seg, 'number')}
        <div class="ccf-aviso"><b>Hoy estas tandas son las mismas todas las horas y todos los días.</b>
          La tabla de breaks por hora y por día (como «Breaks comerciales» de Soho) es el siguiente paso.</div>
        <div class="ccf-pie"><button class="ccf-btn ccf-btn--pri" id="ccfGuardarG">Guardar</button><span class="ccf-msg" id="ccfMsgG"></span></div>`;
      pag.querySelector('#ccfGuardarG').addEventListener('click', async () => {
        try {
          await api('/api/v1/config/motor', json('PUT', {
            tandas_minutos: pag.querySelector('#ccfMin').value.trim(), tandas_inmediatas: pag.querySelector('#ccfInm').value.trim(),
            tandas_cierre: pag.querySelector('#ccfCie').value.trim(), tanda_max_seg: parseInt(pag.querySelector('#ccfMax').value) || 0,
            tolerancia_tanda_seg: parseInt(pag.querySelector('#ccfTol').value) || 0 }));
          msgEn(pag.querySelector('#ccfMsgG'), '✓ Guardado: el motor ya lo usa');
        } catch (e) { msgEn(pag.querySelector('#ccfMsgG'), e.message, false); }
      });
    }

    // ── Prioridades: secciones de la tanda ──
    async function prioridades() {
      let sec;
      try { sec = await api('/api/v1/tandas/secciones'); } catch (e) { pag.innerHTML = `<p class="ccf-msg err">${esc(e.message)}</p>`; return; }
      const leer = () => pag.querySelectorAll('tr[data-i]').forEach(tr => {
        const s = sec[+tr.dataset.i];
        s.numero = parseInt(tr.querySelector('[data-k="numero"]').value) || 1;
        s.nombre = tr.querySelector('[data-k="nombre"]').value.trim();
        const tipo = tr.querySelector('[data-k="tipo"]').value;
        s.comercial = tipo === 'comercial'; s.opcional = tipo === 'opcional';
      });
      const pintar = () => {
        pag.innerHTML = `
          <p class="ccf-hint">La tanda sale en este orden. <b>Comercial</b>: cuñas de clientes, se reparten entre las tandas de la hora.
            <b>Siempre</b>: ID y cortinas, salen en cada tanda. <b>Opcional</b>: solo sale si la tanda lleva al menos una cuña comercial.</p>
          <table class="ccf-tabla"><thead><tr><th style="width:80px">Orden</th><th>Sección</th><th style="width:170px">Tipo</th><th style="width:70px">Spots</th><th style="width:40px"></th></tr></thead>
          <tbody>${sec.map((s, i) => `<tr data-i="${i}" class="t-${s.comercial ? 'comercial' : s.opcional ? 'opcional' : 'siempre'}">
            <td><input type="number" data-k="numero" min="1" max="99" value="${s.numero}"></td>
            <td><input data-k="nombre" maxlength="60" value="${esc(s.nombre)}"></td>
            <td><select data-k="tipo"><option value="siempre" ${!s.comercial && !s.opcional ? 'selected' : ''}>Siempre</option>
              <option value="comercial" ${s.comercial ? 'selected' : ''}>Comercial</option>
              <option value="opcional" ${s.opcional && !s.comercial ? 'selected' : ''}>Opcional</option></select></td>
            <td class="num">${s.pautas ?? 0}</td>
            <td><button class="ccf-btn ccf-btn--mini" data-quitar="${i}" title="Quitar">✕</button></td></tr>`).join('')}</tbody></table>
          <div class="ccf-pie"><button class="ccf-btn" id="ccfAddSec">＋ Agregar sección</button>
            <button class="ccf-btn ccf-btn--pri" id="ccfGuardarS">Guardar prioridades</button><span class="ccf-msg" id="ccfMsgS"></span></div>`;
      };
      pintar();
      pag.addEventListener('click', async e => {
        if (e.target.closest('#ccfAddSec')) {
          leer(); sec.push({ numero: Math.max(0, ...sec.map(s => s.numero || 0)) + 1, nombre: 'Nueva sección', comercial: false, opcional: false, pautas: 0 }); pintar();
        }
        const q = e.target.closest('[data-quitar]');
        if (q) {
          leer(); const s = sec[+q.dataset.quitar];
          if (s.pautas && !confirm(`«${s.nombre}» tiene ${s.pautas} spot(s). Si la borras, pasan a tratarse como comerciales. ¿Seguir?`)) return;
          sec.splice(+q.dataset.quitar, 1); pintar();
        }
        if (e.target.closest('#ccfGuardarS')) {
          leer();
          try {
            sec = await api('/api/v1/tandas/secciones', json('PUT', sec.map(({ numero, nombre, opcional, comercial }) => ({ numero, nombre, opcional, comercial }))));
            pintar(); msgEn(pag.querySelector('#ccfMsgS'), '✓ Guardado: el programa se vuelve a planificar');
          } catch (err) { msgEn(pag.querySelector('#ccfMsgS'), err.message, false); }
        }
      });
    }

    // ── Clientes / Agencias: catálogo ──
    async function catalogo(tipo) {
      const una = tipo === 'clientes' ? 'cliente' : 'agencia';
      let datos, elegido = null;
      const cargar = async () => { datos = await api(`/api/v1/comerciales/catalogo/${tipo}`); };
      try { await cargar(); } catch (e) { pag.innerHTML = `<p class="ccf-msg err">${esc(e.message)}</p>`; return; }
      const pintar = () => {
        const it = datos.items, e = elegido != null ? it.find(x => x.id === elegido) : null;
        pag.innerHTML = `
          <div class="ccf-dos">
            <div>
              <div class="ccf-lista-cab"><b>${tipo === 'clientes' ? 'Clientes' : 'Agencias de publicidad'}</b>
                <button class="ccf-btn ccf-btn--mini" id="ccfImp" title="Agrega al catálogo los nombres que ya usan tus spots">⇩ Traer los de mis spots</button></div>
              <div class="ccf-tabla-wrap"><table class="ccf-tabla"><thead><tr><th>Nombre</th><th style="width:60px">Spots</th><th>Contacto</th></tr></thead>
              <tbody>${it.map(x => `<tr data-id="${x.id}" class="${x.id === elegido ? 'sel' : ''}"><td><b>${esc(x.nombre)}</b></td><td class="num">${x.spots}</td>
                <td>${esc(x.contacto || x.telefono || '')}</td></tr>`).join('') || `<tr><td colspan="3" class="ccf-vacio">Todavía no hay ${tipo}. Agrega uno o trae los de tus spots.</td></tr>`}</tbody></table></div>
              ${datos.sin_catalogo.length ? `<p class="ccf-hint">${datos.sin_catalogo.length} nombre(s) de tus spots no están en el catálogo:
                ${datos.sin_catalogo.slice(0, 8).map(n => `<code>${esc(n)}</code>`).join(' ')}${datos.sin_catalogo.length > 8 ? '…' : ''}</p>` : ''}
            </div>
            <div class="ccf-form">
              <h4>${e ? 'Editar ' + una : 'Nuevo ' + una}</h4>
              <label>Nombre<input id="ccfNom" maxlength="120" value="${esc(e?.nombre || '')}" placeholder="Ej.: ${tipo === 'clientes' ? 'Coca-Cola' : 'Medios Sur'}"></label>
              <label>Persona de contacto<input id="ccfCon" maxlength="120" value="${esc(e?.contacto || '')}"></label>
              <label>Teléfono<input id="ccfTel" maxlength="60" value="${esc(e?.telefono || '')}"></label>
              <label>Correo<input id="ccfEma" maxlength="120" value="${esc(e?.email || '')}"></label>
              <label>Notas<textarea id="ccfNot" rows="2" maxlength="500">${esc(e?.notas || '')}</textarea></label>
              ${e && e.spots ? `<p class="ccf-hint">Si cambias el nombre, se actualizan sus <b>${e.spots}</b> spot(s).</p>` : ''}
              <div class="ccf-pie"><button class="ccf-btn ccf-btn--pri" id="ccfGuardarC">${e ? 'Guardar cambios' : 'Agregar'}</button>
                ${e ? '<button class="ccf-btn" id="ccfNuevoC">＋ Nuevo</button><button class="ccf-btn ccf-btn--peligro" id="ccfQuitarC">Quitar</button>' : ''}
              </div><span class="ccf-msg" id="ccfMsgC"></span>
            </div>
          </div>`;
      };
      pintar();
      const m = (t, ok = true) => { const el = pag.querySelector('#ccfMsgC'); if (el) msgEn(el, t, ok); };
      pag.onclick = async ev => {
        const tr = ev.target.closest('tr[data-id]');
        if (tr) { elegido = +tr.dataset.id; pintar(); return; }
        try {
          if (ev.target.closest('#ccfImp')) {
            const r = await api(`/api/v1/comerciales/catalogo/${tipo}/importar`, { method: 'POST' });
            await cargar(); pintar(); m(`✓ ${r.agregados} agregado(s)`); Catalogo.refrescar();
          } else if (ev.target.closest('#ccfNuevoC')) { elegido = null; pintar(); }
          else if (ev.target.closest('#ccfQuitarC')) {
            const x = datos.items.find(i => i.id === elegido);
            if (!confirm(`¿Quitar «${x.nombre}» del catálogo? Sus ${x.spots} spot(s) conservan el nombre.`)) return;
            await api(`/api/v1/comerciales/catalogo/${tipo}/${elegido}`, { method: 'DELETE' });
            elegido = null; await cargar(); pintar(); Catalogo.refrescar();
          } else if (ev.target.closest('#ccfGuardarC')) {
            const cuerpoE = { nombre: pag.querySelector('#ccfNom').value, contacto: pag.querySelector('#ccfCon').value,
              telefono: pag.querySelector('#ccfTel').value, email: pag.querySelector('#ccfEma').value, notas: pag.querySelector('#ccfNot').value };
            const r = elegido != null
              ? await api(`/api/v1/comerciales/catalogo/${tipo}/${elegido}`, json('PUT', cuerpoE))
              : await api(`/api/v1/comerciales/catalogo/${tipo}`, json('POST', cuerpoE));
            if (elegido == null) elegido = r.id;
            await cargar(); pintar();
            m(r.spots_actualizados ? `✓ Guardado · ${r.spots_actualizados} spot(s) actualizados` : '✓ Guardado');
            Catalogo.refrescar();
            if (r.spots_actualizados && global.PautasController?.reload) global.PautasController.reload();
          }
        } catch (err) { m(err.message, false); }
      };
    }
    ir(pestana);
  }

  // ══════════ Chequeo de integridad ══════════
  async function abrirChequeo() {
    const { cuerpo, cerrar } = ventana('🩺 Chequeo de integridad', 900);
    cuerpo.innerHTML = '<p class="ccf-hint">Revisando los spots activos…</p>';
    let r;
    try { r = await api('/api/v1/comerciales/chequeo'); } catch (e) { cuerpo.innerHTML = `<p class="ccf-msg err">${esc(e.message)}</p>`; return; }
    const GRUPOS = { error: 'No sonarán o suenan mal', aviso: 'Conviene revisar' };
    cuerpo.innerHTML = `
      <div class="ccf-resumen">
        <span><b>${r.revisados}</b> spots activos revisados</span>
        <span class="${r.errores ? 'mal' : 'bien'}"><b>${r.errores}</b> con error</span>
        <span class="${r.avisos ? 'aviso' : 'bien'}"><b>${r.avisos}</b> avisos</span>
      </div>
      ${!r.problemas.length ? '<div class="ccf-todo-bien">✓ No se han encontrado problemas.</div>' :
        ['error', 'aviso'].map(n => {
          const lista = r.problemas.filter(p => p.nivel === n);
          return lista.length ? `<h4 class="ccf-g-${n}">${GRUPOS[n]} (${lista.length})</h4>
            <div class="ccf-tabla-wrap"><table class="ccf-tabla"><tbody>${lista.map(p => `<tr>
              <td style="width:26%"><b>${esc(p.cliente)}</b></td><td style="width:24%">${esc(p.spot)}</td><td>${esc(p.texto)}</td>
              <td style="width:70px"><button class="ccf-btn ccf-btn--mini" data-abrir="${p.pauta_id}">Abrir</button></td></tr>`).join('')}</tbody></table></div>` : '';
        }).join('')}`;
    cuerpo.addEventListener('click', e => {
      const b = e.target.closest('[data-abrir]');
      if (b && global.PautasController?.openModal) { cerrar(); global.PautasController.openModal(b.dataset.abrir); }
    });
  }

  // ── Botones del panel izquierdo ──────────────────────────────────────────
  document.addEventListener('click', e => {
    if (e.target.closest('#btnCfgComerciales')) abrirConfig('general');
    else if (e.target.closest('#btnChequeo')) abrirChequeo();
  });
  Catalogo.refrescar();
  global.ComercialesConfig = { abrirConfig, abrirChequeo };
})(window);
