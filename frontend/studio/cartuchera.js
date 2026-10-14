/**
 * cartuchera.js — Ónix FM · ventana «Editar» de un botón de la cartuchera, copiada de Jazler SoHo
 *
 * Misma distribución que Soho (Efectos › Editar):
 *   izquierda  · el botón tal como se verá · Color · Colores usados
 *   derecha    · Ruta de archivo [...] · Título del botón · mark in / mark out (<< >> preview) ·
 *                ▶ ■ con barra y tiempo · Volumen
 *   abajo      · Copiar · Pegar · Limpiar · ok · Cancelar
 * El archivo se elige desde el propio botón («...»): se escucha aquí y se guarda con «ok».
 *
 * No depende del Studio: trae sus estilos y sirve igual en Admin › Cartuchera.
 * API: POST /api/v1/cartuchera/{banco}/{slot}/archivo · PATCH y DELETE /api/v1/cartuchera/{banco}/{slot}
 *      PUT /api/v1/cartuchera/{banco}/{slot} (pegar) · GET /api/v1/audios/{id}/stream (escuchar)
 */
const CartEditor = (() => {
  // Lista de colores de Soho (nombre → hex)
  const COLORES = [["#4a4a4a", "Gray"], ["#5a0000", "Maroon"], ["#a31515", "Red"], ["#c4500a", "Orange"],
    ["#9a7500", "Gold"], ["#1f6b2a", "Green"], ["#0f6b6b", "Teal"], ["#0050c8", "Blue"], ["#06065e", "Navy"],
    ["#5b2a86", "Purple"], ["#a02c6d", "Pink"], ["#111111", "Black"]];
  const DEFECTO = COLORES[0][0];
  const PASO = 0.1;                                   // lo que mueven << y >>
  const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const seg = x => `${(Math.max(0, +x || 0)).toFixed(1).padStart(4, "0")}s`;       // 9.7 → "09.7s"
  const json = (m, b) => ({ method: m, headers: { "Content-Type": "application/json" }, body: JSON.stringify(b) });

  async function api(url, opts) {
    const r = await fetch(url, opts);
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(typeof d.detail === "string" ? d.detail : `Error ${r.status}`);
    return d;
  }

  function estilos() {
    if (document.getElementById("ce-css")) return;
    const st = document.createElement("style");
    st.id = "ce-css";
    st.textContent = `
.ce-fondo{position:fixed;inset:0;background:rgba(0,0,0,.72);z-index:9000;display:flex;align-items:center;justify-content:center;font-family:"Segoe UI",system-ui,sans-serif}
.ce{background:#262626;border:1px solid #777;color:#ddd;width:min(800px,96vw);max-height:94vh;overflow:auto;padding:16px 20px 14px;box-sizing:border-box}
.ce *{box-sizing:border-box}
.ce-cab{display:flex;align-items:center;gap:14px;margin-bottom:14px}
.ce-cab svg{width:44px;height:44px;fill:#fff}
.ce-cab h2{margin:0;font-size:38px;font-weight:300;color:#aaa;letter-spacing:0}
.ce-cuerpo{display:grid;grid-template-columns:180px 1fr;gap:26px}
.ce-tile{height:112px;display:flex;flex-direction:column;justify-content:space-between;padding:8px;color:#fff;background:#4a4a4a}
.ce-tile b{font-weight:400;font-size:18px;text-align:center;margin-top:24px;overflow:hidden;text-overflow:ellipsis}
.ce-tile small{font-size:13px}
.ce-izq h4{margin:12px 0 6px;font-size:19px;font-weight:400;color:#fff}
.ce-izq h4.ce-tenue{color:#777;margin-top:18px}
.ce-sel{display:flex;align-items:center;gap:8px;border:1px solid #bbb;background:#111;padding:3px}
.ce-sel i{width:22px;height:22px;flex:none}
.ce-sel select{flex:1;background:#111;color:#ccc;border:0;font:inherit;font-size:14px;padding:4px;color-scheme:dark;outline:0}
.ce-usados{display:flex;flex-wrap:wrap;gap:3px}
.ce-usados button{width:30px;height:30px;border:0;padding:0;cursor:pointer}
.ce-der label.ce-l{display:block;font-size:19px;color:#8a8a8a;margin:0 0 3px}
.ce-linea{display:flex;gap:8px;margin-bottom:10px}
.ce-in{flex:1;min-width:0;background:#0c0c0c;border:0;color:#fff;font:inherit;font-size:14px;padding:7px 9px;outline:0}
.ce-b{background:linear-gradient(#3b3b3b,#1d1d1d);border:1px solid #111;color:#ddd;font:inherit;cursor:pointer}
.ce-b:hover:not(:disabled){filter:brightness(1.25)}
.ce-b:disabled{opacity:.35;cursor:default}
.ce-panel{background:#000;padding:10px}
.ce-marcas{display:flex;justify-content:space-between;gap:20px}
.ce-marca{width:240px;max-width:48%;display:grid;grid-template-columns:1fr 1fr;gap:6px}
.ce-marca .ce-num{grid-column:1/-1;height:78px;font-size:42px;font-weight:300;position:relative;color:#eee}
.ce-marca .ce-num span{position:absolute;left:7px;bottom:4px;font-size:13px;color:#ccc}
.ce-marca .ce-paso{height:30px;text-align:left;padding-left:8px;font-size:13px}
.ce-marca .ce-prev{grid-column:1/-1;height:46px;text-align:left;padding:22px 0 0 8px;font-size:13px}
.ce-trans{display:flex;align-items:stretch;margin-top:8px;background:#151515}
.ce-trans .ce-t{width:58px;height:62px;font-size:28px;color:#fff;line-height:1}
.ce-barra{flex:1;padding:0 0 0 8px;display:flex;flex-direction:column;justify-content:flex-start}
.ce-prog{height:24px;background:#2a2a2a;position:relative;cursor:pointer;margin-top:4px}
.ce-prog i{position:absolute;inset:0 auto 0 0;width:0;background:#5d5d5d}
.ce-prog u{position:absolute;top:0;bottom:0;background:rgba(255,255,255,.14);text-decoration:none}
.ce-prog span{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-weight:700;color:#fff;font-size:13px}
.ce-tiempo{color:#e6b800;font-size:21px;margin-top:6px}
.ce-vol{margin-top:20px;font-size:13px;color:#fff}
.ce-vol input{width:100%;accent-color:#bbb}
.ce-err{color:#ff8080;min-height:18px;margin:8px 0 0;font-size:14px}
.ce-pie{display:flex;gap:8px;margin-top:10px;align-items:stretch}
.ce-pie .ce-acc{width:130px;height:62px;text-align:left;padding:6px;font-size:13px;display:flex;flex-direction:column;justify-content:space-between}
.ce-pie .ce-acc svg{width:28px;height:28px;fill:#fff;align-self:center}
.ce-pie .ce-esp{flex:1}
.ce-ok,.ce-no{width:190px;height:62px;font-size:32px;font-weight:300;border:1px solid #111;color:#ddd;cursor:pointer;font-family:inherit}
.ce-ok{background:linear-gradient(#0a5a0a,#053a05)} .ce-no{background:linear-gradient(#6b0a0a,#450505)}
.ce-ok:hover:not(:disabled),.ce-no:hover{filter:brightness(1.3)} .ce-ok:disabled{opacity:.5}
@media(max-width:820px){.ce-cuerpo{grid-template-columns:1fr}.ce-ok,.ce-no{width:auto;flex:1;font-size:30px}.ce-pie .ce-acc{width:auto;flex:1}}
`;
    document.head.appendChild(st);
  }

  let portapapeles = null;
  try { portapapeles = JSON.parse(localStorage.getItem("onix_cart_copia") || "null"); } catch { /* sin almacenamiento */ }

  /**
   * ctx: { banco, slot, boton (objeto del botón o null si está vacío), usados: [hex…], alGuardar() }
   */
  function abrir(ctx) {
    estilos();
    const s = ctx.boton || {};
    const st = {
      audioId: s.audio_id || null, archivo: null,                       // archivo = File recién elegido (aún sin subir)
      ruta: s.archivo_path || "", titulo: s.etiqueta || s.titulo || "", color: s.color || DEFECTO,
      dur: +s.duracion || 0, in: +s.mark_in || 0, out: +s.mark_out || (+s.duracion || 0),
      vol: s.volumen ?? 100,
    };
    const usados = [...new Set((ctx.usados || []).filter(Boolean))];
    const fondo = document.createElement("div");
    fondo.className = "ce-fondo";
    fondo.innerHTML = `
      <div class="ce" role="dialog" aria-label="Editar botón de la cartuchera">
        <div class="ce-cab">
          <svg viewBox="0 0 64 64"><rect x="2" y="14" width="44" height="13" rx="2"/><rect x="2" y="31" width="44" height="13" rx="2"/><rect x="2" y="48" width="44" height="13" rx="2"/><circle cx="9" cy="20.5" r="2.4" fill="#262626"/><path d="M36 30 56 8l7 7-21 21-9 3z" stroke="#262626" stroke-width="2.5"/></svg>
          <h2>Editar</h2>
        </div>
        <div class="ce-cuerpo">
          <div class="ce-izq">
            <div class="ce-tile" id="ceTile"><b id="ceTileT"></b><small id="ceTileD"></small></div>
            <h4>Color</h4>
            <div class="ce-sel"><i id="ceColorI"></i><select id="ceColor">
              ${COLORES.map(([h, n]) => `<option value="${h}">${n}</option>`).join("")}</select></div>
            <h4 class="ce-tenue">Colores usados</h4>
            <div class="ce-usados" id="ceUsados">${usados.map(h => `<button type="button" data-c="${esc(h)}" style="background:${esc(h)}" title="${esc(h)}"></button>`).join("")}</div>
          </div>
          <div class="ce-der">
            <label class="ce-l">Ruta de archivo</label>
            <div class="ce-linea"><input class="ce-in" id="ceRuta" readonly placeholder="Pulsa «...» para elegir el archivo del efecto">
              <button class="ce-b" id="ceElegir" style="width:44px" title="Elegir archivo">...</button></div>
            <label class="ce-l">Título del botón</label>
            <div class="ce-linea"><input class="ce-in" id="ceTitulo" maxlength="40"></div>
            <div class="ce-panel">
              <div class="ce-marcas">
                <div class="ce-marca">
                  <button class="ce-b ce-num" id="ceIn" title="Poner la marca in donde va la reproducción"><b id="ceInN" style="font-weight:300"></b><span>mark in</span></button>
                  <button class="ce-b ce-paso" id="ceInMenos">&lt;&lt;</button><button class="ce-b ce-paso" id="ceInMas">&gt;&gt;</button>
                  <button class="ce-b ce-prev" id="ceInPrev">preview</button>
                </div>
                <div class="ce-marca">
                  <button class="ce-b ce-num" id="ceOut" title="Poner la marca out donde va la reproducción"><b id="ceOutN" style="font-weight:300"></b><span>mark out</span></button>
                  <button class="ce-b ce-paso" id="ceOutMenos">&lt;&lt;</button><button class="ce-b ce-paso" id="ceOutMas">&gt;&gt;</button>
                  <button class="ce-b ce-prev" id="ceOutPrev">preview</button>
                </div>
              </div>
              <div class="ce-trans">
                <button class="ce-b ce-t" id="cePlay" title="Escuchar">&#9654;</button>
                <button class="ce-b ce-t" id="ceStop" title="Detener">&#9632;</button>
                <div class="ce-barra">
                  <div class="ce-prog" id="ceProg"><u id="ceZona"></u><i id="ceAv"></i><span id="ceDur"></span></div>
                  <div class="ce-tiempo" id="ceTiempo"></div>
                </div>
              </div>
              <div class="ce-vol">Volumen <span id="ceVolN"></span>%
                <input type="range" id="ceVol" min="0" max="150" step="5"></div>
            </div>
            <p class="ce-err" id="ceErr"></p>
          </div>
        </div>
        <div class="ce-pie">
          <button class="ce-b ce-acc" id="ceCopiar"><svg viewBox="0 0 24 24"><path d="M8 2h8l4 4v12h-4v4H4V8h4zm2 2v12h8V7h-3V4zM6 10v10h8v-2H8v-8z"/></svg>Copiar</button>
          <button class="ce-b ce-acc" id="cePegar"><svg viewBox="0 0 24 24"><path d="M9 2h6v2h3v6h-2V6h-1v1H9V6H8v12h3v2H6V4h3zm4 9h7v11h-7zm2 2v7h3v-7z"/></svg>Pegar</button>
          <button class="ce-b ce-acc" id="ceLimpiar"><svg viewBox="0 0 24 24"><path d="M9 2h6v2h5v2H4V4h5zM6 8h12v14H6zm3 2v10h2V10zm4 0v10h2V10z"/></svg>Limpiar</button>
          <span class="ce-esp"></span>
          <button class="ce-ok" id="ceOk">ok</button>
          <button class="ce-no" id="ceNo">Cancelar</button>
        </div>
      </div>`;
    document.body.appendChild(fondo);
    const q = id => fondo.querySelector("#" + id);
    const audio = new Audio();
    audio.preload = "auto";
    let urlLocal = null, hasta = null;                     // hasta = segundo donde se detiene la escucha

    function fuente() {
      if (urlLocal) { URL.revokeObjectURL(urlLocal); urlLocal = null; }
      if (st.archivo) { urlLocal = URL.createObjectURL(st.archivo); audio.src = urlLocal; }
      else if (st.audioId) audio.src = `/api/v1/audios/${st.audioId}/stream`;
      else audio.removeAttribute("src");
    }
    function pintar() {
      const largo = Math.max(0, (st.out || st.dur) - st.in);
      q("ceTile").style.background = st.color;
      q("ceTileT").textContent = st.titulo || "";
      q("ceTileD").textContent = st.dur ? seg(largo) : "";
      q("ceColor").value = COLORES.some(([h]) => h === st.color) ? st.color : DEFECTO;
      q("ceColorI").style.background = st.color;
      q("ceRuta").value = st.ruta;
      q("ceInN").textContent = seg(st.in);
      q("ceOutN").textContent = seg(st.out || st.dur);
      q("ceDur").textContent = seg(st.dur);
      q("ceVolN").textContent = st.vol;
      q("ceVol").value = st.vol;
      if (st.dur) {
        q("ceZona").style.left = `${st.in / st.dur * 100}%`;
        q("ceZona").style.width = `${largo / st.dur * 100}%`;
      }
      const hay = !!(st.audioId || st.archivo);
      ["ceIn", "ceOut", "ceInMenos", "ceInMas", "ceOutMenos", "ceOutMas", "ceInPrev", "ceOutPrev", "cePlay", "ceStop", "ceCopiar"]
        .forEach(id => { q(id).disabled = !hay; });
      q("ceInMenos").disabled = !hay || st.in <= 0;
      q("ceOutMas").disabled = !hay || (st.out || st.dur) >= st.dur;
      q("cePegar").disabled = !portapapeles;
      q("ceLimpiar").disabled = !ctx.boton;
      q("ceOk").disabled = !hay;
      tiempo();
    }
    function tiempo() {
      const t = audio.currentTime || 0;
      q("ceTiempo").textContent = `${seg(t)}/${seg(st.dur)}`;
      q("ceAv").style.width = st.dur ? `${Math.min(100, t / st.dur * 100)}%` : "0";
    }
    function sonar(desde, fin) {
      if (!audio.src) return;
      hasta = fin ?? null;
      audio.volume = Math.min(1, st.vol / 100);
      try { audio.currentTime = Math.max(0, desde); } catch { /* aún sin metadatos */ }
      audio.play().catch(e => { q("ceErr").textContent = `No se pudo reproducir: ${e.message}`; });
    }
    const parar = () => { audio.pause(); hasta = null; };
    const fijar = (campo, v) => {
      v = Math.round(Math.max(0, Math.min(v, st.dur)) * 10) / 10;
      if (campo === "in") st.in = Math.min(v, Math.max(0, (st.out || st.dur) - PASO));
      else st.out = Math.max(v, st.in + PASO);
      pintar();
    };

    audio.addEventListener("timeupdate", () => { if (hasta != null && audio.currentTime >= hasta) parar(); tiempo(); });
    audio.addEventListener("ended", () => { hasta = null; tiempo(); });
    audio.addEventListener("loadedmetadata", () => {
      if (!Number.isFinite(audio.duration) || audio.duration <= 0) return;
      const completo = !st.dur || (st.out || st.dur) >= st.dur - 0.05;     // la base guarda la duración redondeada
      st.dur = audio.duration;
      if (st.archivo) { st.in = 0; st.out = st.dur; }                       // archivo nuevo: sin marcas
      else if (completo || st.out > st.dur) st.out = st.dur;
      pintar();
    });
    audio.addEventListener("error", () => { if (audio.src) q("ceErr").textContent = "No se pudo abrir el audio para escucharlo aquí."; });

    q("ceElegir").addEventListener("click", () => {
      const inp = document.createElement("input");
      inp.type = "file"; inp.accept = "audio/*,.mp3,.wav,.ogg,.flac,.aac,.m4a,.wma";
      inp.addEventListener("change", () => {
        const f = inp.files[0]; if (!f) return;
        parar();
        st.archivo = f; st.ruta = f.name;
        if (!q("ceTitulo").value.trim()) { st.titulo = f.name.replace(/\.[^.]+$/, "").slice(0, 40); q("ceTitulo").value = st.titulo; }
        q("ceErr").textContent = "";
        fuente(); pintar();
      });
      inp.click();
    });
    q("ceTitulo").addEventListener("input", () => { st.titulo = q("ceTitulo").value; q("ceTileT").textContent = st.titulo; });
    q("ceColor").addEventListener("change", () => { st.color = q("ceColor").value; pintar(); });
    q("ceUsados").addEventListener("click", e => { const b = e.target.closest("button[data-c]"); if (b) { st.color = b.dataset.c; pintar(); } });
    q("ceIn").addEventListener("click", () => fijar("in", audio.currentTime || 0));
    q("ceOut").addEventListener("click", () => fijar("out", audio.currentTime || st.dur));
    q("ceInMenos").addEventListener("click", () => fijar("in", st.in - PASO));
    q("ceInMas").addEventListener("click", () => fijar("in", st.in + PASO));
    q("ceOutMenos").addEventListener("click", () => fijar("out", (st.out || st.dur) - PASO));
    q("ceOutMas").addEventListener("click", () => fijar("out", (st.out || st.dur) + PASO));
    q("ceInPrev").addEventListener("click", () => sonar(st.in, Math.min(st.in + 3, st.out || st.dur)));
    q("ceOutPrev").addEventListener("click", () => sonar(Math.max(st.in, (st.out || st.dur) - 3), st.out || st.dur));
    q("cePlay").addEventListener("click", () => sonar(st.in, st.out || st.dur));
    q("ceStop").addEventListener("click", () => { parar(); try { audio.currentTime = st.in; } catch { /* nada */ } tiempo(); });
    q("ceProg").addEventListener("click", e => {
      if (!st.dur || !audio.src) return;
      const r = q("ceProg").getBoundingClientRect();
      try { audio.currentTime = (e.clientX - r.left) / r.width * st.dur; } catch { /* nada */ }
      tiempo();
    });
    q("ceVol").addEventListener("input", () => { st.vol = +q("ceVol").value; q("ceVolN").textContent = st.vol; audio.volume = Math.min(1, st.vol / 100); });

    function cerrar(guardado) {
      parar(); audio.removeAttribute("src");
      if (urlLocal) URL.revokeObjectURL(urlLocal);
      document.removeEventListener("keydown", tecla, true);
      fondo.remove();
      if (guardado) ctx.alGuardar?.();
    }
    const tecla = e => { if (e.key === "Escape") { e.stopPropagation(); cerrar(false); } };
    document.addEventListener("keydown", tecla, true);
    q("ceNo").addEventListener("click", () => cerrar(false));

    q("ceCopiar").addEventListener("click", () => {
      if (!st.audioId || st.archivo) { q("ceErr").textContent = "Guarda el botón con «ok» antes de copiarlo."; return; }
      portapapeles = { audio_id: st.audioId, etiqueta: st.titulo, color: st.color, mark_in: st.in, mark_out: st.out, volumen: st.vol,
                       duracion: st.dur, archivo_path: st.ruta };
      try { localStorage.setItem("onix_cart_copia", JSON.stringify(portapapeles)); } catch { /* sin almacenamiento */ }
      pintar();
    });
    q("cePegar").addEventListener("click", () => {
      if (!portapapeles) return;
      parar();
      Object.assign(st, { audioId: portapapeles.audio_id, archivo: null, ruta: portapapeles.archivo_path || "", titulo: portapapeles.etiqueta || "",
        color: portapapeles.color || DEFECTO, dur: +portapapeles.duracion || 0, in: +portapapeles.mark_in || 0,
        out: +portapapeles.mark_out || (+portapapeles.duracion || 0), vol: portapapeles.volumen ?? 100 });
      q("ceTitulo").value = st.titulo;
      fuente(); pintar();
    });
    q("ceLimpiar").addEventListener("click", async () => {
      if (!confirm(`¿Limpiar el botón ${ctx.slot}?`)) return;
      try { await api(`/api/v1/cartuchera/${ctx.banco}/${ctx.slot}`, { method: "DELETE" }); cerrar(true); }
      catch (err) { q("ceErr").textContent = err.message; }
    });

    q("ceOk").addEventListener("click", async () => {
      const base = `/api/v1/cartuchera/${ctx.banco}/${ctx.slot}`;
      q("ceOk").disabled = true; q("ceErr").textContent = "";
      try {
        if (st.archivo) {                                             // archivo nuevo: se sube y queda en el botón
          const fd = new FormData();
          fd.append("file", st.archivo); fd.append("etiqueta", st.titulo.trim());
          const r = await api(`${base}/archivo`, { method: "POST", body: fd });
          st.audioId = r.audio_id; st.archivo = null;
        } else if (st.audioId !== (s.audio_id || null)) {             // pegado desde otro botón
          await api(base, json("PUT", { audio_id: st.audioId, etiqueta: st.titulo.trim() }));
        }
        const fin = st.out || st.dur;
        const completo = st.in <= 0 && (!st.dur || fin >= st.dur - 0.05);
        const cuerpo = { etiqueta: st.titulo.trim(), color: st.color === DEFECTO ? "" : st.color, volumen: st.vol };
        if (completo) cuerpo.quitar_marcas = true; else { cuerpo.mark_in = st.in; cuerpo.mark_out = Math.min(fin, st.dur || fin); }
        await api(base, json("PATCH", cuerpo));
        cerrar(true);
      } catch (err) { q("ceErr").textContent = err.message; q("ceOk").disabled = false; }
    });

    q("ceTitulo").value = st.titulo;
    fuente(); pintar();
    (st.audioId ? q("ceTitulo") : q("ceElegir")).focus();
  }

  return { abrir, COLOR_DEFECTO: DEFECTO, seg };
})();
