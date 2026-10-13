/**
 * locutor.js — Ónix FM Studio · Locutor IA (bloque al inicio de la pestaña 🎙 VOZ)
 *
 * Muestra lo que el Locutor IA va a decir y pide aprobación:
 *   · «Por aprobar»: texto editable → ✓ genera la voz y la mete en el programa tras su canción, ✕ descarta.
 *   · «Listas»: ya locutadas y esperando su turno (🎧 preescucha en la tarjeta de cabina).
 *   · «Probar»: pide un texto de un tipo para las canciones elegidas en la cola (queda en borrador).
 * Encender/apagar y la configuración están en Admin › Locutor IA.
 *
 * Usa del Studio: $, esc, fmt, toast, WS, Decks (studio.js). Lo monta VoiceTracker.mostrar().
 * API: /api/v1/locutor/{estado,intervenciones}
 */
const LocutorIA = (() => {
  const TIPOS = { corta: "Corta", hora_clima: "Hora y clima", identificacion: "Identificación", apertura: "Apertura",
    patrocinio: "Patrocinio", noticia: "Noticia", complacencia: "Saludo" };
  let caja = null, timer = null, ocupado = false;
  const json = (m, b) => ({ method: m, headers: { "Content-Type": "application/json" }, body: JSON.stringify(b) });

  async function api(url, opts) {
    const r = await fetch(url, opts);
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(typeof d.detail === "string" ? d.detail : `Error ${r.status}`);
    return d;
  }

  function montar(raiz) {
    caja = document.createElement("section");
    caja.className = "vt-sec lia";
    caja.innerHTML = `
      <h4>🤖 Locutor IA <span class="lia-estado" id="liaEstado">…</span></h4>
      <div class="vt-fila">
        <select class="vt-in" id="liaTipo" title="Qué quieres que escriba">
          ${Object.entries(TIPOS).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}
        </select>
        <button class="vt-btn" id="liaProbar" title="Escribir ahora para las canciones elegidas en la cola (no sale al aire hasta que lo apruebes)">✍ Probar</button>
      </div>
      <div id="liaPorAprobar" class="vt-lista"></div>
      <h4 class="lia-sub">Listas para salir</h4>
      <div id="liaListas" class="vt-lista"></div>`;
    raiz.prepend(caja);
    caja.querySelector("#liaProbar").addEventListener("click", probar);
    caja.addEventListener("click", accion);
  }

  async function refrescar() {
    if (!caja || ocupado) return;
    try {
      const [e, borr, listas] = await Promise.all([
        api("/api/v1/locutor/estado"),
        api("/api/v1/locutor/intervenciones?estado=borrador&limit=20"),
        api("/api/v1/locutor/intervenciones?estado=lista&limit=8")]);
      const st = caja.querySelector("#liaEstado");
      st.textContent = e.activo ? "ENCENDIDO" : "APAGADO";
      st.className = `lia-estado ${e.activo ? "on" : "off"}`;
      st.title = e.claves_ia ? "" : "Sin claves de IA: Admin › IA";
      caja.querySelector("#liaPorAprobar").innerHTML = borr.length ? borr.map(i => `
        <div class="lia-item" data-id="${i.id}">
          <div class="lia-cab"><b>${esc(TIPOS[i.tipo] || i.tipo)}</b>${i.anterior_titulo ? ` · tras «${esc(i.anterior_titulo)}»` : ""}
            <small>${esc(i.persona || "")}${i.error ? " · " + esc(i.error) : ""}</small></div>
          <textarea class="vt-in lia-txt" rows="3" maxlength="1500">${esc(i.texto || "")}</textarea>
          <div class="vt-fila">
            <button class="vt-btn vt-btn--pri" data-a="aprobar">✓ Aprobar y programar</button>
            <button class="vt-btn" data-a="descartar">✕ Descartar</button>
          </div>
        </div>`).join("") : `<p class="vt-nota">Nada por aprobar.</p>`;
      caja.querySelector("#liaListas").innerHTML = listas.length ? listas.map(i => `
        <div class="vt-item" data-id="${i.id}" data-audio="${i.audio_id || ""}">
          <span class="vt-item-t" title="${esc(i.texto || "")}">${esc(i.texto || "")}<small>${esc(TIPOS[i.tipo] || i.tipo)} · ${esc(i.persona || "")} · ${fmt(i.duracion_seg || 0)}</small></span>
          <button data-a="cue" title="Preescucha (tarjeta de cabina)">🎧</button>
          <button data-a="descartar" title="Descartar">✕</button>
        </div>`).join("") : `<p class="vt-nota">Ninguna.</p>`;
    } catch (err) {
      caja.querySelector("#liaPorAprobar").innerHTML = `<p class="vt-nota">No se pudo leer al Locutor IA: ${esc(err.message)}</p>`;
    }
  }

  async function probar() {
    const v = Decks.vecinos();
    const b = caja.querySelector("#liaProbar");
    b.disabled = true; b.textContent = "…";
    try {
      const r = await api("/api/v1/locutor/intervenciones/probar", json("POST", {
        tipo: caja.querySelector("#liaTipo").value, anterior_id: v.anterior?.id || null, siguiente_id: v.siguiente?.id || null }));
      if (r.estado === "fallo") toast(`No se pudo escribir: ${r.error}`, "error", 7000);
      await refrescar();
    } catch (err) { toast(`No se pudo escribir: ${err.message}`, "error", 7000); }
    finally { b.disabled = false; b.textContent = "✍ Probar"; }
  }

  async function accion(e) {
    const b = e.target.closest("button[data-a]"); if (!b) return;
    const fila = b.closest("[data-id]"), id = +fila.dataset.id, a = b.dataset.a;
    if (a === "cue") { if (fila.dataset.audio) WS.send({ cmd: "cue", audio_id: +fila.dataset.audio }); return; }
    ocupado = true; b.disabled = true;
    try {
      if (a === "aprobar") {
        b.textContent = "Creando voz…";
        const texto = fila.querySelector(".lia-txt").value.trim();
        const r = await api(`/api/v1/locutor/intervenciones/${id}/aprobar`, json("POST", { texto }));
        toast(r.en_programa ? "✓ Voz lista y colocada en el programa" : (r.aviso || "Voz lista"), r.en_programa ? "info" : "error", 6000);
      } else if (a === "descartar") {
        await api(`/api/v1/locutor/intervenciones/${id}/descartar`, { method: "POST" });
      }
    } catch (err) { toast(`No se pudo: ${err.message}`, "error", 8000); }
    finally { ocupado = false; refrescar(); }
  }

  function mostrar(raiz) {
    if (!caja) montar(raiz);
    refrescar();
    clearInterval(timer);
    timer = setInterval(() => { if (raiz.hidden) { clearInterval(timer); timer = null; } else refrescar(); }, 15000);
  }

  return { mostrar };
})();
