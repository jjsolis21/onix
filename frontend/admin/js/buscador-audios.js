/* ÓNIX FM — Buscador de audios para el Admin (autocompletar contra el servidor)
 *
 * Con decenas de miles de audios no se puede bajar la biblioteca a la pantalla:
 * cada vez que se escribe se consulta /api/v1/audios?q=… y se muestran las
 * mejores coincidencias en una lista que cuelga del campo.
 *
 *   OnixBuscador.attach(input, {
 *     tipos: () => "Efecto,Sweeper,Tips",   // opcional: tipos permitidos (o null = todos)
 *     primero: "Efecto,Sweeper,Tips",       // opcional: estos tipos van antes que la música
 *     onElegir: audio => { … },             // se llama al elegir una fila
 *   });
 *   input.dataset.audioId   → id elegido ("" si el texto se cambió después)
 *   OnixBuscador.etiqueta(a) → "Artista — Título"
 *   OnixBuscador.porIds([1,2]) → Map id → audio (para mostrar nombres ya guardados)
 */
(() => {
  "use strict";
  const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const mmss = t => `${Math.floor((t || 0) / 60)}:${String(Math.floor((t || 0) % 60)).padStart(2, "0")}`;
  const etiqueta = a => `${a.artista} — ${a.titulo}`;
  const cache = new Map();

  const css = document.createElement("style");
  css.textContent = `
    .obq-lista { position: fixed; z-index: 99999; max-height: 320px; overflow-y: auto; min-width: 320px;
      background: #1a1612; border: 1px solid #c9a227; border-radius: 6px; box-shadow: 0 10px 30px rgba(0,0,0,.6); }
    .obq-fila { display: flex; gap: 10px; align-items: baseline; padding: 7px 10px; cursor: pointer; border-bottom: 1px solid rgba(255,255,255,.06); font-size: 13px; color: #eee; }
    .obq-fila:last-child { border-bottom: 0; }
    .obq-fila.sel, .obq-fila:hover { background: rgba(201,162,39,.22); }
    .obq-fila b { flex: 1; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-weight: 600; }
    .obq-fila b small { font-weight: 400; color: #aaa; margin-left: 6px; }
    .obq-fila em { font-style: normal; font-size: 10px; letter-spacing: .08em; color: #c9a227; border: 1px solid #c9a227; border-radius: 3px; padding: 0 4px; }
    .obq-fila span { font-family: monospace; font-size: 11px; color: #aaa; }
    .obq-vacio { padding: 10px; color: #aaa; font-size: 12px; }`;
  document.head.appendChild(css);

  async function pedir(params) {
    const r = await fetch(`/api/v1/audios?${new URLSearchParams(params)}`);
    if (!r.ok) return [];
    const d = (await r.json()).data || [];
    d.forEach(a => cache.set(a.id, a));
    return d;
  }

  async function porIds(ids) {
    const faltan = [...new Set(ids.filter(x => x != null))].filter(id => !cache.has(id));
    if (faltan.length) await pedir({ ids: faltan.join(","), limit: faltan.length });
    return cache;
  }

  function attach(input, opc = {}) {
    let caja = null, filas = [], sel = -1, t = null, n = 0, viejo = false;
    const cerrar = () => { caja?.remove(); caja = null; sel = -1; };

    function pintar(lista) {
      filas = lista;
      if (!caja) { caja = document.createElement("div"); caja.className = "obq-lista"; document.body.appendChild(caja); }
      const r = input.getBoundingClientRect();
      caja.style.left = `${r.left}px`; caja.style.top = `${r.bottom + 2}px`; caja.style.width = `${Math.max(r.width, 320)}px`;
      caja.innerHTML = lista.length ? lista.map((a, i) => `<div class="obq-fila" data-i="${i}">
          <b>${esc(a.titulo)}<small>${esc(a.artista)}</small></b><em>${esc(a.tipo_audio)}</em><span>${mmss(a.duracion)}</span></div>`).join("")
        : `<div class="obq-vacio">Sin coincidencias</div>`;
      sel = -1; viejo = false;
      caja.style.opacity = 1;
    }
    function elegir(i) {
      const a = filas[i];
      if (!a) return;
      input.value = etiqueta(a);
      input.dataset.audioId = a.id;
      cerrar();
      opc.onElegir?.(a);
      input.dispatchEvent(new Event("change", { bubbles: true }));
    }
    async function buscar() {
      const mia = ++n, q = input.value.trim();
      const tipos = opc.tipos ? opc.tipos() : null;
      const base = { limit: 20 };
      if (q) base.q = q;
      let lista;
      if (opc.primero && !tipos) {
        // Primero los tipos pedidos (jingles, efectos…) y luego la música: con 15 000 canciones
        // una palabra común llenaría la lista sola.
        const [a, b] = await Promise.all([pedir({ ...base, limit: 14, tipo: opc.primero }), pedir({ ...base, limit: 8, tipo: "Musica" })]);
        lista = a.concat(b);
      } else {
        lista = await pedir(tipos ? { ...base, tipo: tipos } : base);
      }
      if (mia === n && document.activeElement === input) pintar(lista);
    }

    input.setAttribute("autocomplete", "off");
    input.addEventListener("input", () => {
      input.dataset.audioId = ""; viejo = true;       // lo que se ve es de antes de teclear: no se elige con Enter
      if (caja) caja.style.opacity = .5;
      clearTimeout(t); t = setTimeout(buscar, 180);
    });
    input.addEventListener("focus", () => { if (!input.dataset.audioId) buscar(); });
    input.addEventListener("keydown", e => {
      if (!caja) return;
      const filasDom = [...caja.querySelectorAll(".obq-fila")];
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        if (viejo) return;
        sel = (sel + (e.key === "ArrowDown" ? 1 : -1) + filasDom.length) % Math.max(1, filasDom.length);
        filasDom.forEach((f, i) => f.classList.toggle("sel", i === sel));
        filasDom[sel]?.scrollIntoView({ block: "nearest" });
      } else if (e.key === "Enter" && sel >= 0 && !viejo) { e.preventDefault(); elegir(sel); }
      else if (e.key === "Escape") cerrar();
    });
    input.addEventListener("blur", () => setTimeout(cerrar, 150));
    // mousedown (no click): se elige antes de que el campo pierda el foco
    document.addEventListener("mousedown", e => {
      const f = e.target.closest?.(".obq-fila");
      if (f && caja?.contains(f)) { e.preventDefault(); elegir(+f.dataset.i); }
    });
  }

  window.OnixBuscador = { attach, etiqueta, porIds, cache };
})();
