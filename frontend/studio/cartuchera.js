/**
 * cartuchera.js — Ónix FM Studio · botones de la cartuchera como Soho
 *
 * Clic derecho sobre cualquier botón (vacío o cargado) abre un menú:
 *   · Cargar efecto…  → elige un archivo de la PC y queda en ese botón (se registra como «Efecto»).
 *   · Editar…         → ventana como la de Soho: título, color, marca in / out con preescucha y volumen.
 *   · Vaciar
 * Arrastrar una canción de la biblioteca sobre el botón sigue funcionando (lo maneja studio.js).
 *
 * Usa del Studio: $, esc, fmt, toast, WS.
 * API: PATCH /api/v1/cartuchera/{banco}/{slot} · POST …/archivo · DELETE /api/v1/cartuchera/{banco}/{slot}
 */
const CartMenu = (() => {
  // Colores como la lista de Soho (nombre → hex). "" = el color por defecto del botón.
  const COLORES = [["", "Automático"], ["#7a0a0a", "Maroon"], ["#a31515", "Rojo"], ["#c46210", "Naranja"],
    ["#9a7b0a", "Dorado"], ["#2f7d32", "Verde"], ["#0f6b6b", "Turquesa"], ["#0b3d91", "Azul"], ["#1b1b6b", "Navy"],
    ["#5b2a86", "Púrpura"], ["#a02c6d", "Rosa"], ["#4a4a4a", "Gris"]];
  let menu = null, cerrarFondo = null, previa = null;

  const json = (m, b) => ({ method: m, headers: { "Content-Type": "application/json" }, body: JSON.stringify(b) });
  async function api(url, opts) {
    const r = await fetch(url, opts);
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(typeof d.detail === "string" ? d.detail : `Error ${r.status}`);
    return d;
  }

  function cerrarMenu() {
    menu?.remove(); menu = null;
    if (cerrarFondo) { document.removeEventListener("mousedown", cerrarFondo, true); cerrarFondo = null; }
  }

  /** ctx: { banco, slot, boton (objeto del botón o null), vaciar() } */
  function abrir(ev, ctx) {
    ev.preventDefault();
    cerrarMenu();
    const cargado = !!ctx.boton;
    menu = document.createElement("div");
    menu.className = "cm-menu";
    menu.innerHTML = `
      <button data-a="cargar">📂 Cargar efecto…</button>
      ${cargado ? '<button data-a="editar">✎ Editar…</button><button data-a="vaciar" class="cm-peligro">🗑 Vaciar</button>' : ""}`;
    document.body.appendChild(menu);
    const w = menu.offsetWidth, h = menu.offsetHeight;
    menu.style.left = Math.min(ev.clientX, innerWidth - w - 6) + "px";
    menu.style.top = Math.min(ev.clientY, innerHeight - h - 6) + "px";
    menu.addEventListener("click", e => {
      const b = e.target.closest("button[data-a]"); if (!b) return;
      const a = b.dataset.a;
      cerrarMenu();
      if (a === "cargar") elegirArchivo(ctx);
      if (a === "editar") editor(ctx);
      if (a === "vaciar") ctx.vaciar();
    });
    cerrarFondo = e => { if (!menu?.contains(e.target)) cerrarMenu(); };
    setTimeout(() => document.addEventListener("mousedown", cerrarFondo, true), 0);
  }

  // ── Cargar un archivo en el botón ────────────────────────────────────────
  function elegirArchivo(ctx, alTerminar) {
    const inp = document.createElement("input");
    inp.type = "file"; inp.accept = "audio/*,.mp3,.wav,.ogg,.flac,.aac,.m4a,.wma";
    inp.addEventListener("change", async () => {
      const f = inp.files[0]; if (!f) return;
      toast(`Cargando «${f.name}»…`, "info", 1500);
      const fd = new FormData();
      fd.append("file", f);
      try {
        const r = await api(`/api/v1/cartuchera/${ctx.banco}/${ctx.slot}/archivo`, { method: "POST", body: fd });
        toast(`Botón ${ctx.slot}: «${r.titulo}» (${fmt(r.duracion)})`, "info", 3000);
        alTerminar?.(r);
      } catch (err) { toast(`No se pudo cargar el efecto: ${err.message}`, "error", 7000); }
    });
    inp.click();
  }

  // ── Ventana «Editar» (como Soho) ─────────────────────────────────────────
  function editor(ctx) {
    const s = ctx.boton;
    const dur = +s.duracion || 0;
    const fondo = document.createElement("div");
    fondo.className = "atajos-fondo";
    fondo.innerHTML = `
      <div class="atajos-caja cm-ed" role="dialog" aria-label="Editar botón">
        <h2>Editar botón ${ctx.slot}</h2>
        <div class="cm-fila"><label>Archivo</label><span class="cm-ruta" title="${esc(s.archivo_path || "")}">${esc(s.archivo_path || s.titulo)}</span>
          <button class="sec" id="cmCambiar">Cambiar…</button></div>
        <div class="cm-fila"><label>Título del botón</label><input id="cmTitulo" maxlength="40" value="${esc(s.etiqueta || s.titulo)}"></div>
        <div class="cm-fila"><label>Color</label><div class="cm-colores" id="cmColores">
          ${COLORES.map(([hex, n]) => `<button type="button" class="cm-color ${(s.color || "") === hex ? "sel" : ""}" data-c="${hex}" title="${n}"
            style="${hex ? `background:${hex}` : "background:repeating-linear-gradient(45deg,#333,#333 4px,#444 4px,#444 8px)"}"></button>`).join("")}
        </div></div>
        <div class="cm-marcas">
          <div><label>Marca in (s)</label><input id="cmIn" type="number" min="0" step="0.1" value="${s.mark_in ?? 0}">
            <button class="sec" id="cmPrevIn" title="Escuchar desde la marca in (preescucha)">▶ preview</button></div>
          <div><label>Marca out (s)</label><input id="cmOut" type="number" min="0" step="0.1" value="${s.mark_out ?? (dur || "")}">
            <button class="sec" id="cmPrevOut" title="Escuchar los últimos segundos antes de la marca out">▶ preview</button></div>
        </div>
        <p class="cm-nota">Duración del archivo: ${fmt(dur)} · se reproduce de la marca in a la marca out.
          <a href="#" id="cmQuitarMarcas">Quitar marcas</a></p>
        <div class="cm-fila"><label>Volumen <b id="cmVolN">${s.volumen ?? 100}</b>%</label>
          <input id="cmVol" type="range" min="0" max="150" step="5" value="${s.volumen ?? 100}"></div>
        <p class="cm-err" id="cmErr"></p>
        <div class="cm-pie"><button id="cmGuardar">GUARDAR</button><button class="sec" id="cmCancelar">CANCELAR</button></div>
      </div>`;
    document.body.appendChild(fondo);
    const q = id => fondo.querySelector("#" + id);
    let color = s.color || "";

    const cerrar = () => { WS.send({ cmd: "cue_stop" }); clearTimeout(previa); fondo.remove(); };
    q("cmCancelar").addEventListener("click", cerrar);
    fondo.addEventListener("mousedown", e => { if (e.target === fondo) cerrar(); });
    q("cmColores").addEventListener("click", e => {
      const b = e.target.closest(".cm-color"); if (!b) return;
      color = b.dataset.c;
      fondo.querySelectorAll(".cm-color").forEach(x => x.classList.toggle("sel", x === b));
    });
    q("cmVol").addEventListener("input", () => { q("cmVolN").textContent = q("cmVol").value; });
    q("cmQuitarMarcas").addEventListener("click", e => {
      e.preventDefault(); q("cmIn").value = 0; q("cmOut").value = dur || "";
    });
    q("cmCambiar").addEventListener("click", () => elegirArchivo(ctx, () => cerrar()));
    q("cmPrevIn").addEventListener("click", () => {
      clearTimeout(previa);
      WS.send({ cmd: "cue", audio_id: s.audio_id, desde: Math.max(0, +q("cmIn").value || 0) });
      const fin = (+q("cmOut").value || dur) - (+q("cmIn").value || 0);
      if (fin > 0) previa = setTimeout(() => WS.send({ cmd: "cue_stop" }), fin * 1000);
    });
    q("cmPrevOut").addEventListener("click", () => {
      clearTimeout(previa);
      const out = +q("cmOut").value || dur;
      WS.send({ cmd: "cue", audio_id: s.audio_id, desde: Math.max(0, out - 3) });
      previa = setTimeout(() => WS.send({ cmd: "cue_stop" }), 3000);
    });
    q("cmGuardar").addEventListener("click", async () => {
      const mi = +q("cmIn").value || 0, mo = +q("cmOut").value || 0;
      const sinMarcas = mi <= 0 && (!mo || (dur && mo >= dur - 0.05));
      const cuerpo = { etiqueta: q("cmTitulo").value.trim(), color, volumen: +q("cmVol").value };
      if (sinMarcas) cuerpo.quitar_marcas = true; else { cuerpo.mark_in = mi; cuerpo.mark_out = mo || dur; }
      try {
        await api(`/api/v1/cartuchera/${ctx.banco}/${ctx.slot}`, json("PATCH", cuerpo));
        cerrar();
        toast(`Botón ${ctx.slot} guardado`, "info", 1500);
      } catch (err) { q("cmErr").textContent = err.message; }
    });
    q("cmTitulo").focus();
  }

  return { abrir };
})();
