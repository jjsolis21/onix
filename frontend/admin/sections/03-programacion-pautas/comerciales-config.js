/**
 * comerciales-config.js — Ónix FM · Admin › Comerciales
 *
 * «Config. de comerciales» (como las Propiedades de spot de Jazler SoHo), en una sola ventana con pestañas:
 *   · General       → tandas: minutos, inicio inmediato, cierre, duración máxima y tolerancia.
 *   · Breaks por hora → tandas distintas por día y hora (como «Breaks comerciales» de Soho).
 *   · Prioridades   → orden de las secciones de la tanda (ID, cortinas, comerciales) y su tipo.
 *   · Clientes / Agencias → catálogo con contacto; al renombrar se actualizan sus spots.
 * y «Chequeo de integridad»: spots con el audio perdido, sin horarios, vencidos, sin rubro…
 *
 * API: /api/v1/config/motor · /api/v1/comerciales/tandas-grilla · /api/v1/tandas/secciones · /api/v1/comerciales/catalogo/{clientes|agencias}
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
        <button data-t="general">General</button><button data-t="breaks">Breaks por hora</button><button data-t="prioridades">Prioridades</button><button data-t="separadores">Separadores</button><button data-t="rubros">Rubros</button>
        <button data-t="clientes">Clientes</button><button data-t="agencias">Agencias</button>
      </nav><div class="ccf-pagina"></div>`;
    const pag = cuerpo.querySelector('.ccf-pagina');
    const ir = t => {
      cuerpo.querySelectorAll('.ccf-tabs button').forEach(b => b.classList.toggle('on', b.dataset.t === t));
      ({ general, breaks, prioridades, separadores, rubros, clientes: () => catalogo('clientes'), agencias: () => catalogo('agencias') })[t]();
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
        ${campo('ccfCie', 'Tandas al cierre', 'Minutos en que la tanda <b>termina</b> en vez de empezar: el sistema suma lo que duran sus cuñas y la arranca antes (7 min de cuñas con <b>0</b> → empieza a las :53 y termina en punto). '
          + 'Si además pones ese minuto en «inicio inmediato», corta la canción a la hora calculada para terminar exacto. Vacío = ninguna.', c.tandas_cierre)}
        ${campo('ccfMax', 'Duración máxima de la tanda (segundos)', 'Si una tanda dura más, el Studio avisa. 0 = sin límite.', c.tanda_max_seg, 'number')}
        ${campo('ccfTol', 'Tolerancia de tanda (segundos)', 'Cuánto puede atrasarse una tanda esperando que termine la canción en curso antes de buscar otra que encaje.', c.tolerancia_tanda_seg, 'number')}
        <div class="ccf-campo"><label for="ccfVoz">Voces de mujer</label>
          <label class="ccf-chk"><input id="ccfVoz" type="checkbox" ${c.regla_voces_mujer ? 'checked' : ''}> No sonar dos voces de mujer seguidas</label>
          <p>Cada cuña lleva su voz (Hombre / Mujer…) en la ventana de edición. Si no hay forma de separarlas (por ejemplo, solo hay dos cuñas y las dos son de mujer), la regla cede: ninguna cuña se queda sin salir.</p></div>
        <div class="ccf-aviso">Estos valores valen para <b>todas las horas</b>, salvo las que cambies en la pestaña
          <b>Breaks por hora</b>, donde cada día y hora puede tener sus propios minutos o no tener tandas.</div>
        <div class="ccf-pie"><button class="ccf-btn ccf-btn--pri" id="ccfGuardarG">Guardar</button><span class="ccf-msg" id="ccfMsgG"></span></div>`;
      pag.querySelector('#ccfGuardarG').addEventListener('click', async () => {
        try {
          await api('/api/v1/config/motor', json('PUT', {
            tandas_minutos: pag.querySelector('#ccfMin').value.trim(), tandas_inmediatas: pag.querySelector('#ccfInm').value.trim(),
            tandas_cierre: pag.querySelector('#ccfCie').value.trim(), tanda_max_seg: parseInt(pag.querySelector('#ccfMax').value) || 0,
            tolerancia_tanda_seg: parseInt(pag.querySelector('#ccfTol').value) || 0,
            regla_voces_mujer: pag.querySelector('#ccfVoz').checked }));
          msgEn(pag.querySelector('#ccfMsgG'), '✓ Guardado: el motor ya lo usa');
        } catch (e) { msgEn(pag.querySelector('#ccfMsgG'), e.message, false); }
      });
    }

    // ── Breaks por hora: grilla día × hora ──
    async function breaks() {
      pag.innerHTML = '<p class="ccf-hint">Cargando…</p>';
      let d;
      try { d = await api('/api/v1/comerciales/tandas-grilla'); } catch (e) { pag.innerHTML = `<p class="ccf-msg err">${esc(e.message)}</p>`; return; }
      const grilla = d.grilla || {}, gen = d.general || {};
      const DIAS = [[1, 'Lun'], [2, 'Mar'], [3, 'Mié'], [4, 'Jue'], [5, 'Vie'], [6, 'Sáb'], [0, 'Dom']];   // clave: 0 = domingo
      const sel = new Set();
      let ancla = null;
      const texto = k => {
        const e = grilla[k];
        if (!e) return `<span class="g-gen">${esc(gen.tandas_minutos || '')}</span>`;
        if (e.off) return '<span class="g-off">—</span>';
        const inm = e.inmediatas ? ` <i title="Inicio inmediato: ${esc(e.inmediatas)}">⚡</i>` : '';
        const cie = e.cierre ? ` <i title="Al cierre: ${esc(e.cierre)}">⇥</i>` : '';
        return `<b>${esc(e.minutos)}</b>${inm}${cie}`;
      };
      const pintar = () => {
        pag.querySelector('#ccfGrilla').innerHTML = `<thead><tr><th></th>${DIAS.map(([n, t]) => `<th data-dia="${n}" title="Elegir todo el día">${t}</th>`).join('')}</tr></thead>
          <tbody>${Array.from({ length: 24 }, (_, h) => `<tr><th data-hora="${h}" title="Elegir esta hora todos los días">${String(h).padStart(2, '0')}:00</th>
            ${DIAS.map(([n]) => { const k = `${n}-${h}`; return `<td data-k="${k}" class="${sel.has(k) ? 'sel' : ''} ${grilla[k] ? (grilla[k].off ? 'off' : 'propia') : ''}">${texto(k)}</td>`; }).join('')}</tr>`).join('')}</tbody>`;
        const n = sel.size;
        pag.querySelector('#ccfSelN').textContent = n ? `${n} hora${n > 1 ? 's' : ''} elegida${n > 1 ? 's' : ''}` : 'Elige horas en la tabla';
        pag.querySelectorAll('#ccfEd [data-acc], #ccfEd input').forEach(x => { x.disabled = !n; });
        if (n === 1) {                                     // una sola: el editor muestra lo que tiene
          const e = grilla[[...sel][0]];
          pag.querySelector('#ccfGMin').value = e && !e.off ? e.minutos : '';
          pag.querySelector('#ccfGInm').value = e && !e.off ? (e.inmediatas ?? '') : '';
          pag.querySelector('#ccfGCie').value = e && !e.off ? (e.cierre ?? '') : '';
        }
      };
      pag.innerHTML = `
        <p class="ccf-hint">Cada celda es una hora de un día. En gris, la hora usa los minutos generales (<b>${esc(gen.tandas_minutos || '')}</b>, pestaña General);
          en blanco, sus propios minutos; <b>—</b> = esa hora no tiene tandas. ⚡ inicio inmediato · ⇥ al cierre.
          Clic para elegir una hora, <b>Mayús+clic</b> para un rango, <b>Ctrl+clic</b> para sumar; clic en el día o la hora elige toda la columna o fila.</p>
        <div class="ccf-breaks">
          <div class="ccf-tabla-wrap ccf-grilla-wrap"><table class="ccf-grilla" id="ccfGrilla"></table></div>
          <div class="ccf-ed" id="ccfEd">
            <h4 id="ccfSelN"></h4>
            <label>Minutos de las tandas<input id="ccfGMin" placeholder="Ej: 15,45"></label>
            <label>Inicio inmediato<input id="ccfGInm" placeholder="Ej: 15 (vacío = ninguna)"></label>
            <label>Al cierre<input id="ccfGCie" placeholder="Ej: 0 (vacío = ninguna)"></label>
            <button class="ccf-btn ccf-btn--pri" data-acc="propia">Aplicar a lo elegido</button>
            <button class="ccf-btn" data-acc="off">Sin tandas</button>
            <button class="ccf-btn" data-acc="general">Volver a lo general</button>
            <hr>
            <button class="ccf-btn ccf-btn--pri" id="ccfGuardarB" style="margin-top:auto">Guardar la grilla</button>
            <span class="ccf-msg" id="ccfMsgB"></span>
          </div>
        </div>`;
      pintar();
      const orden = k => { const [dd, h] = k.split('-').map(Number); return [DIAS.findIndex(x => x[0] === dd), h]; };
      pag.querySelector('#ccfGrilla').addEventListener('click', e => {
        const td = e.target.closest('td[data-k]'), dia = e.target.closest('th[data-dia]'), hora = e.target.closest('th[data-hora]');
        if (!e.ctrlKey && !e.shiftKey) sel.clear();
        if (dia) for (let h = 0; h < 24; h++) sel.add(`${dia.dataset.dia}-${h}`);
        else if (hora) DIAS.forEach(([n]) => sel.add(`${n}-${hora.dataset.hora}`));
        else if (td && e.shiftKey && ancla) {              // rectángulo entre el ancla y esta celda
          const [c1, h1] = orden(ancla), [c2, h2] = orden(td.dataset.k);
          for (let c = Math.min(c1, c2); c <= Math.max(c1, c2); c++)
            for (let h = Math.min(h1, h2); h <= Math.max(h1, h2); h++) sel.add(`${DIAS[c][0]}-${h}`);
        } else if (td) {
          if (e.ctrlKey && sel.has(td.dataset.k)) sel.delete(td.dataset.k); else sel.add(td.dataset.k);
          ancla = td.dataset.k;
        }
        pintar();
      });
      const msg = pag.querySelector('#ccfMsgB');
      pag.querySelector('#ccfEd').addEventListener('click', async e => {
        const acc = e.target.closest('[data-acc]')?.dataset.acc;
        if (acc === 'propia') {
          const v = id => pag.querySelector(id).value.trim();
          if (!v('#ccfGMin')) { msgEn(msg, 'Escribe los minutos (o usa «Sin tandas»)', false); return; }
          sel.forEach(k => { grilla[k] = { minutos: v('#ccfGMin'), inmediatas: v('#ccfGInm'), cierre: v('#ccfGCie') }; });
        } else if (acc === 'off') sel.forEach(k => { grilla[k] = { off: true }; });
        else if (acc === 'general') sel.forEach(k => { delete grilla[k]; });
        if (acc) { msgEn(msg, 'Sin guardar todavía', false); pintar(); }
        if (e.target.id === 'ccfGuardarB') {
          try {
            const r = await api('/api/v1/comerciales/tandas-grilla', json('PUT', { grilla }));
            Object.keys(grilla).forEach(k => delete grilla[k]); Object.assign(grilla, r.grilla);
            pintar(); msgEn(msg, '✓ Guardado: el motor ya lo usa');
          } catch (err) { msgEn(msg, err.message, false); }
        }
      });
    }

    // ── Separadores: varios audios que rotan en las secciones que salen en cada tanda ──
    async function separadores() {
      pag.innerHTML = '<p class="ccf-hint">Cargando…</p>';
      let d;
      try { d = await api('/api/v1/comerciales/separadores'); } catch (e) { pag.innerHTML = `<p class="ccf-msg err">${esc(e.message)}</p>`; return; }
      const mmss = s => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
      const tipoTxt = { Efecto: 'Jingle / ID', Sweeper: 'Sweeper', Tips: 'Tip', 'Cuña': 'Cuña' };
      const sec = d.secciones;
      const pintar = () => {
        pag.innerHTML = `
          <p class="ccf-hint">Cada sección que <b>sale en cada tanda</b> puede llevar <b>varios audios que rotan</b>: en la primera tanda suena el A, en la
            siguiente el B, luego el C… y vuelve a empezar. Úsalo para el <b>separador de entrada</b> (jingle, sweeper o identificador que anuncia que empieza
            la tanda). El orden de la tanda es: <b>Promociones → Identificación → Separador → Comerciales → Cortina de salida</b>.</p>
          ${d.advertencias.length ? `<div class="ccf-aviso">⚠ ${d.advertencias.map(a => `La sección <b>${esc(a.nombre)}</b> está como «Comercial»: se reparte entre las tandas en vez de salir en cada una.`).join(' ')}
            Cámbiala a «Siempre» en la pestaña <b>Prioridades</b>.</div>` : ''}
          ${sec.map((s, i) => `<section class="ccf-sep" data-i="${i}">
            <header><b>${esc(s.numero)} · ${esc(s.nombre)}</b>
              <span class="ccf-sep-tipo ${s.opcional ? 'opc' : 'sie'}">${s.opcional ? 'Opcional · solo si la tanda lleva comerciales' : 'Siempre · en cada tanda'}</span></header>
            ${s.numero === 0 ? `<p class="ccf-hint" style="margin:6px 0">Las promociones son <b>spots</b> de esta sección: <b>Nuevo spot › 1 · Datos › Sección: ${esc(s.nombre)}</b>.
                Suenan siempre primero; entre ellas manda la <b>prioridad</b> del spot. Ahora hay <b>${s.otras_pautas}</b>.</p>` : `
              <ol class="ccf-sep-lista">${s.audios.length ? s.audios.map((a, j) => `<li><span class="n">${String.fromCharCode(65 + j)}</span>
                <span class="t"><b>${esc(a.titulo)}</b> <small>${esc(tipoTxt[a.tipo] || a.tipo)} · ${mmss(a.duracion)}</small></span>
                <button class="ccf-btn ccf-btn--mini" data-up="${i}:${j}" ${j ? '' : 'disabled'}>▲</button>
                <button class="ccf-btn ccf-btn--mini" data-down="${i}:${j}" ${j < s.audios.length - 1 ? '' : 'disabled'}>▼</button>
                <button class="ccf-btn ccf-btn--mini" data-del="${i}:${j}">✕</button></li>`).join('')
                : '<li class="vacio">Sin audios: esta sección no pone separador</li>'}</ol>
              <div class="ccf-sep-add"><select data-add="${i}"><option value="">＋ Agregar audio de la biblioteca…</option>
                ${['Efecto', 'Sweeper', 'Tips', 'Cuña'].map(t => {
                  const l = d.disponibles.filter(a => a.tipo === t && !s.audios.some(x => x.id === a.id));
                  return l.length ? `<optgroup label="${esc(tipoTxt[t])}">${l.map(a => `<option value="${a.id}">${esc(a.titulo)} · ${mmss(a.duracion)}</option>`).join('')}</optgroup>` : '';
                }).join('')}</select>
                <button class="ccf-btn ccf-btn--pri" data-guardar="${i}">Guardar</button>
                <span class="ccf-msg" id="ccfSepMsg${i}"></span></div>`}
          </section>`).join('')}`;
      };
      pintar();
      pag.onclick = async e => {
        const q = a => e.target.closest(`[data-${a}]`);
        const par = v => v.split(':').map(Number);
        let h;
        if ((h = q('up'))) { const [i, j] = par(h.dataset.up); [sec[i].audios[j - 1], sec[i].audios[j]] = [sec[i].audios[j], sec[i].audios[j - 1]]; pintar(); }
        else if ((h = q('down'))) { const [i, j] = par(h.dataset.down); [sec[i].audios[j + 1], sec[i].audios[j]] = [sec[i].audios[j], sec[i].audios[j + 1]]; pintar(); }
        else if ((h = q('del'))) { const [i, j] = par(h.dataset.del); sec[i].audios.splice(j, 1); pintar(); }
        else if ((h = q('guardar'))) {
          const i = +h.dataset.guardar, m = pag.querySelector(`#ccfSepMsg${i}`);
          try {
            await api(`/api/v1/comerciales/separadores/${sec[i].numero}`, json('PUT', { audios: sec[i].audios.map(a => a.id) }));
            msgEn(m, sec[i].audios.length ? `✓ Guardado: rotan ${sec[i].audios.length} audio(s)` : '✓ Sin separador');
          } catch (err) { msgEn(m, err.message, false); }
        }
      };
      pag.onchange = e => {
        const sel = e.target.closest('select[data-add]');
        if (!sel || !sel.value) return;
        const i = +sel.dataset.add, a = d.disponibles.find(x => x.id === +sel.value);
        if (a) { sec[i].audios.push(a); pintar(); }
      };
    }

    // ── Rubros: pares que pueden compartir tanda pero no sonar seguidos ──
    async function rubros() {
      let d;
      try { d = await api('/api/v1/comerciales/rubros'); } catch (e) { pag.innerHTML = `<p class="ccf-msg err">${esc(e.message)}</p>`; return; }
      pag.innerHTML = `
        <p class="ccf-hint">Dos cuñas de un <b>mismo rubro</b> ya no suenan seguidas. Aquí marcas rubros <b>distintos</b> que tampoco deben sonar
          uno tras otro (por ejemplo Restaurante y Cafetería): pueden ir en la misma tanda, pero separados por otra cuña.
          Si no hay forma de separarlos, se permiten: ninguna cuña se queda sin salir.</p>
        <div class="ccf-dos">
          <div><div class="ccf-lista-cab"><b>Rubros en uso</b></div>
            <div class="ccf-tabla-wrap"><table class="ccf-tabla"><tbody>${d.rubros.length ? d.rubros.map(r =>
              `<tr data-rubro="${esc(r.nombre)}" title="Clic para usarlo en el par"><td><b>${esc(r.nombre)}</b></td><td class="num">${r.spots} spot${r.spots !== 1 ? 's' : ''}</td></tr>`).join('')
              : '<tr><td class="ccf-vacio">Los spots aún no tienen rubro</td></tr>'}</tbody></table></div></div>
          <div><div class="ccf-lista-cab"><b>No deben sonar seguidos</b></div>
            <div class="ccf-form">
              <label>Rubro A<input id="ccfRbA" list="ccfRbLista" autocomplete="off"></label>
              <label>Rubro B<input id="ccfRbB" list="ccfRbLista" autocomplete="off"></label>
              <datalist id="ccfRbLista">${d.rubros.map(r => `<option value="${esc(r.nombre)}">`).join('')}</datalist>
              <div class="ccf-pie"><button class="ccf-btn ccf-btn--pri" id="ccfRbAdd">＋ Agregar par</button><span class="ccf-msg" id="ccfRbMsg"></span></div>
            </div>
            <div class="ccf-tabla-wrap" style="margin-top:10px"><table class="ccf-tabla"><tbody>${d.conflictos.length ? d.conflictos.map(c =>
              `<tr><td><b>${esc(c.a)}</b> ↔ <b>${esc(c.b)}</b></td><td style="text-align:right"><button class="ccf-btn ccf-btn--mini ccf-btn--peligro" data-quitar-par="${esc(c.a)}|${esc(c.b)}">Quitar</button></td></tr>`).join('')
              : '<tr><td class="ccf-vacio">Aún no hay pares</td></tr>'}</tbody></table></div>
          </div>
        </div>`;
      const msg = pag.querySelector('#ccfRbMsg');
      pag.onclick = async e => {
        const fila = e.target.closest('tr[data-rubro]');
        if (fila) { const A = pag.querySelector('#ccfRbA'), B = pag.querySelector('#ccfRbB'); (A.value ? B : A).value = fila.dataset.rubro; return; }
        const q = e.target.closest('[data-quitar-par]');
        if (q) {
          const [a, b] = q.dataset.quitarPar.split('|');
          try { await api(`/api/v1/comerciales/rubros/conflictos?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`, { method: 'DELETE' }); rubros(); }
          catch (err) { msgEn(msg, err.message, false); }
          return;
        }
        if (e.target.id === 'ccfRbAdd') {
          try {
            await api('/api/v1/comerciales/rubros/conflictos', json('POST', { a: pag.querySelector('#ccfRbA').value, b: pag.querySelector('#ccfRbB').value }));
            rubros();
          } catch (err) { msgEn(msg, err.message, false); }
        }
      };
    }

    // ── Prioridades: secciones de la tanda ──
    async function prioridades() {
      let sec;
      try { sec = await api('/api/v1/tandas/secciones'); } catch (e) { pag.innerHTML = `<p class="ccf-msg err">${esc(e.message)}</p>`; return; }
      const leer = () => pag.querySelectorAll('tr[data-i]').forEach(tr => {
        const s = sec[+tr.dataset.i];
        { const n = parseInt(tr.querySelector('[data-k="numero"]').value); s.numero = Number.isFinite(n) ? n : 1; }
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
            <td><input type="number" data-k="numero" min="0" max="99" value="${s.numero}"></td>
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
  // ══════════ Bloques publicitarios: playlist de cuñas elegidas, programada como evento de Relojes ══════════
  async function abrirBloques() {
    const { cuerpo } = ventana('🧩 Bloques publicitarios', 1060);
    const mmss = s => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
    const hhmm = (h, delta) => { const [a, b] = h.split(':').map(Number); const t = (((a * 60 + b) * 60 + delta) % 86400 + 86400) % 86400;
      return `${String(Math.floor(t / 3600)).padStart(2, '0')}:${String(Math.floor(t % 3600 / 60)).padStart(2, '0')}`; };
    const NOMBRES = [[1, 'Lun'], [2, 'Mar'], [3, 'Mié'], [4, 'Jue'], [5, 'Vie'], [6, 'Sáb'], [0, 'Dom']];
    let activas = [], elegidas = [], programados = [], editando = null;
    cuerpo.innerHTML = `<p class="ccf-hint">Arma una lista con los spots que quieras, mira cuánto dura y prográmala a una hora.
        Queda como un <b>evento en Relojes</b> y el modo automático la saca sola.</p>
      <div class="ccf-bq">
        <div><h4>1 · Biblioteca de comerciales</h4><input id="ccfBqBus" placeholder="Buscar cuña o cliente…" style="margin-bottom:6px">
          <div class="ccf-tabla-wrap" style="max-height:44vh"><table class="ccf-tabla"><tbody id="ccfBqAct"></tbody></table></div></div>
        <div><h4>2 · Tu bloque <span id="ccfBqDur" class="ccf-bq-dur"></span> <span id="ccfBqEstado" class="ccf-bq-calc"></span></h4>
          <ol class="ccf-bq-lista" id="ccfBqLista"></ol>
          <div class="ccf-bq-form">
            <label>Nombre<input id="ccfBqNom" maxlength="70" placeholder="Ej: Cierre de las 11"></label>
            <fieldset><legend>3 · ¿Cuándo?</legend>
              <label class="ccf-radio"><input type="radio" name="ccfBqModo" value="empieza" checked> <b>Empieza a las</b> <span>espera a que termine la canción</span></label>
              <label class="ccf-radio"><input type="radio" name="ccfBqModo" value="inmediato"> <b>Empieza inmediato a las</b> <span>corta la canción con fade</span></label>
              <label class="ccf-radio"><input type="radio" name="ccfBqModo" value="termina"> <b>Termina a las</b> <span>calcula cuándo arrancar según la duración</span></label>
              <label style="flex-direction:row;align-items:center;gap:8px"><input type="time" id="ccfBqHora" value="10:00" style="width:auto">
                <span class="ccf-bq-calc" id="ccfBqCalc"></span></label>
              <label class="ccf-radio" id="ccfBqCortarFila" hidden><input type="checkbox" id="ccfBqCortar" checked> Cortar la canción para terminar exacto</label>
            </fieldset>
            <div class="ccf-bq-dias" id="ccfBqDias">${NOMBRES.map(([n, t]) => `<label><input type="checkbox" value="${n}" checked>${t}</label>`).join('')}</div>
            <div class="ccf-pie"><button class="ccf-btn ccf-btn--pri" id="ccfBqCrear">Programar bloque</button>
              <button class="ccf-btn" id="ccfBqCancelar" hidden>Cancelar edición</button><span class="ccf-msg" id="ccfBqMsg"></span></div>
          </div></div>
      </div>
      <h4 style="margin-top:16px">Bloques programados</h4><div id="ccfBqProg"></div>`;
    const $ = id => cuerpo.querySelector('#' + id);
    const total = () => elegidas.reduce((a, p) => a + p.duracion, 0);
    const modo = () => cuerpo.querySelector('input[name="ccfBqModo"]:checked').value;

    const pintarAct = () => {
      const q = $('ccfBqBus').value.trim().toLowerCase();
      const usadas = new Set(elegidas.map(p => p.audio_id));
      $('ccfBqAct').innerHTML = activas.filter(p => !usadas.has(p.audio_id) && (!q || (p.cliente + ' ' + p.spot).toLowerCase().includes(q)))
        .map(p => `<tr data-add="${p.audio_id}" title="Agregar al bloque"><td><b>${esc(p.spot || p.cliente)}</b><br><small>${p.cliente ? esc(p.cliente) : 'sin spot con contrato'}</small></td>
          <td class="num">${mmss(p.duracion)}</td><td><button class="ccf-btn ccf-btn--mini">＋</button></td></tr>`).join('')
        || '<tr><td class="ccf-vacio">No hay cuñas en la Biblioteca de comerciales (o ya están todas en el bloque)</td></tr>';
    };
    const pintarLista = () => {
      $('ccfBqLista').innerHTML = elegidas.map((p, i) => `<li><span class="n">${i + 1}</span><span class="t"><b>${esc(p.spot || p.cliente)}</b> <small>${esc(p.cliente)}</small></span>
        <span class="d">${mmss(p.duracion)}</span><button class="ccf-btn ccf-btn--mini" data-up="${i}" ${i ? '' : 'disabled'}>▲</button>
        <button class="ccf-btn ccf-btn--mini" data-down="${i}" ${i < elegidas.length - 1 ? '' : 'disabled'}>▼</button>
        <button class="ccf-btn ccf-btn--mini" data-del="${i}">✕</button></li>`).join('')
        || '<li class="vacio">Agrega spots desde la lista de la izquierda</li>';
      $('ccfBqDur').textContent = elegidas.length ? `· ${elegidas.length} cuñas · ${mmss(total())}` : '';
      calc();
    };
    const calc = () => {
      const h = $('ccfBqHora').value || '00:00', dur = total();
      $('ccfBqCortarFila').hidden = modo() !== 'termina';
      $('ccfBqCalc').textContent = !dur ? '' : modo() === 'termina'
        ? `→ arranca a las ${hhmm(h, -dur)} y termina a las ${h}`
        : `→ empieza a las ${h} y termina a las ${hhmm(h, dur)}`;
    };
    const pintarProg = () => {
      $('ccfBqProg').innerHTML = programados.length ? `<table class="ccf-tabla"><thead><tr><th>Bloque</th><th>Empieza</th><th>Días</th><th>Cuñas</th><th>Dura</th><th></th></tr></thead><tbody>${
        programados.map(b => `<tr><td><b>${esc(b.nombre)}</b>${b.inmediato ? ' <small title="Corta la canción">⚡</small>' : ''}<br>
          <small>${b.cunas.map(c => esc(c.cliente || c.titulo)).join(' · ')}</small></td><td class="num">${esc(b.hora)}</td>
          <td>${b.dias.length === 7 ? 'Todos' : NOMBRES.filter(([n]) => b.dias.includes(n)).map(x => x[1]).join(' ')}</td>
          <td class="num">${b.cunas.length}</td><td class="num">${mmss(b.duracion)}</td>
          <td style="white-space:nowrap"><button class="ccf-btn ccf-btn--mini" data-editar="${b.evento_id}">✎ Editar</button>
          <button class="ccf-btn ccf-btn--mini ccf-btn--peligro" data-quitar="${b.evento_id}">Quitar</button></td></tr>`).join('')}</tbody></table>`
        : '<p class="ccf-hint">Todavía no hay bloques programados.</p>';
    };
    const modoEdicion = () => {
      $('ccfBqCrear').textContent = editando ? 'Guardar cambios' : 'Programar bloque';
      $('ccfBqCancelar').hidden = !editando;
      $('ccfBqEstado').textContent = editando ? '✎ Editando un bloque programado' : '';
    };
    const cargarProg = async () => { try { programados = await api('/api/v1/comerciales/bloques'); } catch (_) { programados = []; } pintarProg(); };

    cuerpo.addEventListener('click', async e => {
      const ed = e.target.closest('[data-editar]'), add = e.target.closest('[data-add]'), up = e.target.closest('[data-up]'), dn = e.target.closest('[data-down]'),
        del = e.target.closest('[data-del]'), qt = e.target.closest('[data-quitar]');
      if (ed) {                                               // cargar el bloque en el formulario
        const b = programados.find(x => x.evento_id === +ed.dataset.editar);
        if (!b) return;
        editando = b.evento_id;
        elegidas = b.cunas.map(c => activas.find(a => a.audio_id === c.audio_id)
          || { audio_id: c.audio_id, spot: c.titulo, cliente: c.cliente, duracion: c.duracion });
        $('ccfBqNom').value = b.nombre; $('ccfBqHora').value = b.hora_objetivo;
        cuerpo.querySelector(`input[name="ccfBqModo"][value="${b.modo}"]`).checked = true;
        $('ccfBqCortar').checked = b.cortar;
        cuerpo.querySelectorAll('#ccfBqDias input').forEach(x => { x.checked = b.dias.includes(+x.value); });
        pintarAct(); pintarLista(); modoEdicion();
        cuerpo.scrollTop = 0;
      }
      else if (add) { elegidas.push(activas.find(p => p.audio_id === +add.dataset.add)); pintarAct(); pintarLista(); }
      else if (up) { const i = +up.dataset.up; [elegidas[i - 1], elegidas[i]] = [elegidas[i], elegidas[i - 1]]; pintarLista(); }
      else if (dn) { const i = +dn.dataset.down; [elegidas[i + 1], elegidas[i]] = [elegidas[i], elegidas[i + 1]]; pintarLista(); }
      else if (del) { elegidas.splice(+del.dataset.del, 1); pintarAct(); pintarLista(); }
      else if (qt && confirm('¿Quitar este bloque? Deja de sonar y desaparece de Relojes › Eventos.')) {
        try { await api(`/api/v1/comerciales/bloques/${qt.dataset.quitar}`, { method: 'DELETE' }); cargarProg(); }
        catch (err) { msgEn($('ccfBqMsg'), err.message, false); }
      }
    });
    cuerpo.addEventListener('input', e => { if (e.target.id === 'ccfBqBus') pintarAct(); else calc(); });
    $('ccfBqCancelar').addEventListener('click', () => {
      editando = null; elegidas = []; $('ccfBqNom').value = ''; modoEdicion(); pintarAct(); pintarLista(); msgEn($('ccfBqMsg'), '', true);
    });
    $('ccfBqCrear').addEventListener('click', async () => {
      const msg = $('ccfBqMsg');
      const dias = [...cuerpo.querySelectorAll('#ccfBqDias input:checked')].map(x => +x.value);
      if (!elegidas.length) return msgEn(msg, 'Agrega al menos un spot', false);
      if (!$('ccfBqNom').value.trim()) return msgEn(msg, 'Ponle un nombre al bloque', false);
      if (!dias.length) return msgEn(msg, 'Elige al menos un día', false);
      try {
        const r = await api(editando ? `/api/v1/comerciales/bloques/${editando}` : '/api/v1/comerciales/bloques', json(editando ? 'PUT' : 'POST', {
          nombre: $('ccfBqNom').value.trim(), audios: elegidas.map(p => p.audio_id), modo: modo(), hora: $('ccfBqHora').value, dias,
          cortar: modo() === 'termina' && $('ccfBqCortar').checked }));
        msgEn(msg, `✓ ${editando ? 'Cambios guardados' : 'Programado'}: ${r.empieza} → ${r.termina} (${mmss(r.duracion)}). Ya está en Relojes › Eventos`);
        editando = null; elegidas = []; $('ccfBqNom').value = ''; modoEdicion(); pintarAct(); pintarLista(); cargarProg();
      } catch (err) { msgEn(msg, err.message, false); }
    });
    try { activas = await api('/api/v1/comerciales/cunas-activas'); } catch (e) {
      activas = []; $('ccfBqAct').innerHTML = `<tr><td class="ccf-vacio">No se pudo leer la biblioteca: ${esc(e.message)}. ¿Reiniciaste ONIX?</td></tr>`; return;
    }
    pintarAct(); pintarLista(); cargarProg();
  }

  // ══════════ Tandas del día (solo lectura): lo que el motor va a sacar, bloque por bloque ══════════
  async function abrirTandasDia() {
    const { cuerpo } = ventana('📋 Tandas del día', 1040);
    const mmss = s => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
    let fecha = new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10), hora = new Date().getHours();
    cuerpo.innerHTML = `<div class="ccf-td-barra"><button class="ccf-btn ccf-btn--mini" data-d="-1">◀</button>
      <input type="date" id="ccfTdFecha"><button class="ccf-btn ccf-btn--mini" data-d="1">▶</button>
      <span class="ccf-hint" style="margin:0">Es lo que el motor va a sacar. <b>Arrastra</b> una cuña para cambiar su orden o pasarla a otro bloque;
      <b>✕</b> la quita solo ese día. Los cambios valen solo para esa hora de ese día.</span></div>
      <div class="ccf-td"><div class="ccf-td-horas" id="ccfTdHoras"></div><div class="ccf-td-bloques" id="ccfTdBloques"></div></div>`;
    const horas = cuerpo.querySelector('#ccfTdHoras'), bloques = cuerpo.querySelector('#ccfTdBloques'), inp = cuerpo.querySelector('#ccfTdFecha');
    let datos = null;
    const pintar = () => {
      horas.innerHTML = datos.horas.map(h => {
        const n = h.bloques.reduce((a, b) => a + b.cunas.length, 0);
        return `<button class="${h.hora === hora ? 'on' : ''}" data-h="${h.hora}"><b>${String(h.hora).padStart(2, '0')}:00</b>
          <span>${h.sin_tandas ? 'sin tandas' : `${h.bloques.length} tandas · ${n} cuñas`}</span></button>`;
      }).join('');
      const h = datos.horas[hora];
      if (h.sin_tandas) { bloques.innerHTML = '<p class="ccf-hint">Esta hora no tiene tandas (así lo marca la pestaña «Breaks por hora»).</p>'; return; }
      const aviso = h.ajustada ? `<div class="ccf-td-ajuste">✋ Esta hora está ordenada a mano.
          <button class="ccf-btn ccf-btn--mini" data-auto="1">↺ Volver al orden automático</button></div>` : '';
      const quit = h.quitadas.length ? `<div class="ccf-td-quitadas">Quitadas este día:
          ${h.quitadas.map(q => `<button class="ccf-btn ccf-btn--mini" data-devolver="${q.pauta_id}" title="Devolverla a la hora">↩ ${esc(q.cliente)}</button>`).join(' ')}</div>` : '';
      bloques.innerHTML = aviso + h.bloques.map(b => `<section class="ccf-bloque ${b.excede ? 'excede' : ''}">
        <header><b>:${String(b.minuto).padStart(2, '0')}</b>
          ${b.inmediata ? '<span title="Corta la canción">⚡ inmediata</span>' : ''}${b.cierre ? '<span title="Termina en su minuto">⇥ al cierre</span>' : ''}
          <em>${b.cunas.length} cuñas · ${mmss(b.duracion)}${b.excede ? ` · pasa del máximo (${mmss(datos.maximo)})` : ''}</em></header>
        <ol data-m="${b.minuto}">${b.cunas.map(c => `<li class="${c.comercial ? 'com' : 'fija'}${c.fijada ? ' fijada' : ''}"
            ${c.comercial ? `draggable="true" data-id="${c.pauta_id}" title="Arrastra para mover"` : 'title="ID y cortinas: van en cada tanda"'}>
          <span class="cli">${esc(c.cliente || c.spot)}${c.mujer ? ' <i class="ccf-voz" title="Voz de mujer">♀</i>' : ''}</span>
          <span class="spot">${esc(c.spot)}</span><span class="rub">${esc(c.rubro)}</span><span class="dur">${mmss(c.duracion)}</span>
          ${c.comercial ? `<button class="ccf-td-x" data-quitar="${c.pauta_id}" title="Quitar solo este día">✕</button>` : '<span></span>'}</li>`).join('')}
          ${b.cunas.some(c => c.comercial) ? '' : '<li class="vacio">Suelta aquí una cuña</li>'}</ol></section>`).join('') + quit;
    };
    const cargar = async () => {
      inp.value = fecha; bloques.innerHTML = '<p class="ccf-hint">Cargando…</p>';
      try { datos = await api(`/api/v1/comerciales/tandas-dia?fecha=${fecha}`); pintar(); }
      catch (e) { bloques.innerHTML = `<p class="ccf-msg err">${esc(e.message)}</p>`; }
    };
    inp.addEventListener('change', () => { if (inp.value) { fecha = inp.value; cargar(); } });

    // ── Orden a mano: se guarda la hora entera (cada bloque con sus cuñas en orden) ──
    const guardar = async (quitadas) => {
      const bl = {};
      bloques.querySelectorAll('ol[data-m]').forEach(ol => { bl[ol.dataset.m] = [...ol.querySelectorAll('li[data-id]')].map(li => +li.dataset.id); });
      try {
        await api('/api/v1/comerciales/tandas-dia/ajuste', json('PUT', { fecha, hora, bloques: bl, quitadas }));
        await cargar();
      } catch (e) { alert(e.message); cargar(); }
    };
    const quitadasDe = () => datos.horas[hora].quitadas.map(q => q.pauta_id);
    let arrastrada = null;
    bloques.addEventListener('dragstart', e => {
      arrastrada = e.target.closest('li[data-id]');
      if (!arrastrada) return;
      arrastrada.classList.add('arrastrando'); e.dataTransfer.effectAllowed = 'move';
    });
    bloques.addEventListener('dragend', () => { arrastrada?.classList.remove('arrastrando'); bloques.querySelectorAll('.sobre').forEach(x => x.classList.remove('sobre')); });
    bloques.addEventListener('dragover', e => {
      const ol = e.target.closest('ol[data-m]');
      if (!arrastrada || !ol) return;
      e.preventDefault();
      bloques.querySelectorAll('.sobre').forEach(x => x.classList.remove('sobre'));
      ol.closest('.ccf-bloque').classList.add('sobre');
    });
    bloques.addEventListener('drop', e => {
      const ol = e.target.closest('ol[data-m]');
      if (!arrastrada || !ol) return;
      e.preventDefault();
      const destino = e.target.closest('li[data-id]');
      if (destino && destino !== arrastrada) {               // antes o después según la mitad donde se suelta
        const r = destino.getBoundingClientRect();
        destino.parentNode.insertBefore(arrastrada, e.clientY > r.top + r.height / 2 ? destino.nextSibling : destino);
      } else if (!destino) {
        const ultima = [...ol.querySelectorAll('li[data-id]')].pop();
        ol.insertBefore(arrastrada, ultima ? ultima.nextSibling : ol.querySelector('li.fija') || null);
      }
      guardar(quitadasDe());
    });
    bloques.addEventListener('click', async e => {
      const q = e.target.closest('[data-quitar]'), dv = e.target.closest('[data-devolver]'), au = e.target.closest('[data-auto]');
      if (q) { q.closest('li').remove(); guardar([...quitadasDe(), +q.dataset.quitar]); }
      else if (dv) guardar(quitadasDe().filter(i => i !== +dv.dataset.devolver));
      else if (au && confirm('¿Volver al orden automático en esta hora? Se pierden los cambios a mano.')) {
        try { await api(`/api/v1/comerciales/tandas-dia/ajuste?fecha=${fecha}&hora=${hora}`, { method: 'DELETE' }); cargar(); }
        catch (err) { alert(err.message); }
      }
    });
    cuerpo.addEventListener('click', e => {
      const d = e.target.closest('[data-d]'), h = e.target.closest('[data-h]');
      if (d) { const t = new Date(fecha + 'T12:00:00'); t.setDate(t.getDate() + +d.dataset.d); fecha = t.toISOString().slice(0, 10); cargar(); }
      else if (h && datos) { hora = +h.dataset.h; pintar(); }
    });
    cargar();
  }

  document.addEventListener('click', e => {
    if (e.target.closest('#btnCfgComerciales')) abrirConfig('general');
    else if (e.target.closest('#btnChequeo')) abrirChequeo();
    else if (e.target.closest('#btnTandasDia')) abrirTandasDia();
    else if (e.target.closest('#btnBloques')) abrirBloques();
  });
  Catalogo.refrescar();

  // ── Columnas a elección de la lista de spots (como «☰ Columnas» de la Biblioteca Musical) ──
  (function columnas() {
    const COLS = [
      { k: 'pin', t: '📌 Posición fija' }, { k: 'cliente', t: 'Cliente' }, { k: 'agencia', t: 'Agencia' }, { k: 'rubro', t: 'Rubro' },
      { k: 'contrato', t: 'Contrato', oculta: true }, { k: 'versiones', t: 'Versiones', oculta: true },
      { k: 'inicia', t: 'Inicia' }, { k: 'hasta', t: 'Hasta' }, { k: 'menc', t: 'Menciones por día' },
      { k: 'prior', t: 'Prioridad' }, { k: 'estado', t: 'Estado' },
    ];
    const CLAVE = 'cm_cols';
    let visibles;
    try { visibles = new Set(JSON.parse(localStorage.getItem(CLAVE)) || COLS.filter(c => !c.oculta).map(c => c.k)); }
    catch (_) { visibles = new Set(COLS.filter(c => !c.oculta).map(c => c.k)); }
    const estilo = document.createElement('style');
    estilo.id = 'cmColsEstilo';
    document.head.appendChild(estilo);
    const aplicar = () => {
      estilo.textContent = COLS.filter(c => !visibles.has(c.k)).map(c => `#pautasTable [data-col="${c.k}"] { display: none; }`).join('\n');
      try { localStorage.setItem(CLAVE, JSON.stringify([...visibles])); } catch (_) { }
    };
    aplicar();
    document.addEventListener('click', e => {
      const menu = document.getElementById('cmMenuCols');
      if (!menu) return;
      if (e.target.closest('#btnColumnas')) {
        if (!menu.hidden) { menu.hidden = true; return; }
        menu.innerHTML = '<b>Columnas visibles</b>' + COLS.map(c =>
          `<label><input type="checkbox" value="${c.k}" ${visibles.has(c.k) ? 'checked' : ''}> ${c.t}</label>`).join('')
          + '<button type="button" class="cm-reiniciar" data-cols-reset="1">Restablecer</button>';
        menu.hidden = false;
      } else if (e.target.closest('[data-cols-reset]')) {
        visibles = new Set(COLS.filter(c => !c.oculta).map(c => c.k)); aplicar(); menu.hidden = true;
      } else if (!e.target.closest('#cmMenuCols')) menu.hidden = true;
    });
    document.addEventListener('change', e => {
      if (!e.target.closest('#cmMenuCols')) return;
      e.target.checked ? visibles.add(e.target.value) : visibles.delete(e.target.value);
      aplicar();
    });
  })();

  global.ComercialesConfig = { abrirConfig, abrirChequeo, abrirTandasDia, abrirBloques };
})(window);
