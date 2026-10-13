/**
 * voicetracker.js — Ónix FM Studio · Voice Tracker (pestaña 🎙 VOZ del panel derecho)
 *
 * Lo que dice el locutor entre dos canciones, como el Voice Tracker de Jazler:
 *   A) se graba con el micrófono (sale en WAV, sin pasar por formatos que el motor no lee), o
 *   B) se escribe (a mano o con IA) y se convierte en voz de IA.
 * La locución entra DESPUÉS de la canción elegida en la cola/programa (o al final de la cola) y al aire
 * suena SOBRE el final de esa canción, con la música bajada; la siguiente arranca al terminar la voz.
 *
 * Usa del Studio: $, esc, fmt, toast, WS, Estado, Decks (studio.js).
 * API: /api/v1/voicetrack (grabar · texto · ia · voces · lista)
 */
const VoiceTracker = (() => {
  const raiz = $("voiceTracker");
  const MAX_SEG = 240;                       // la API acepta ~4 minutos
  let montado = false, voces = [], textoIA = false;
  let rec = null;                            // grabación en curso: {ctx, stream, nodo, partes, hz, t0, timer}
  let toma = null;                           // última toma grabada: {wav: Blob, url, seg}
  let previa = null;                         // <audio> para escuchar la toma (en esta PC, no al aire)

  function montar() {
    raiz.innerHTML = `
      <div class="vt-ctx" id="vtCtx"></div>
      <section class="vt-sec">
        <h4>🎙 Grabar con el micrófono</h4>
        <div class="vt-fila">
          <button class="vt-rec" id="vtRec" title="Grabar / detener">● GRABAR</button>
          <span class="vt-reloj" id="vtReloj">0:00</span>
          <span class="vt-vu"><i id="vtVu"></i></span>
        </div>
        <input class="vt-in" id="vtTitulo" maxlength="120" placeholder="Nombre (opcional): «Saludo 3 pm»">
        <div class="vt-fila">
          <button class="vt-btn" id="vtEscuchar" disabled title="Escuchar la toma en esta PC (no sale al aire)">▶ Escuchar</button>
          <button class="vt-btn vt-btn--pri" id="vtGuardar" disabled>✓ Guardar e insertar</button>
          <button class="vt-btn" id="vtDescartar" disabled title="Borrar esta toma">✕</button>
        </div>
      </section>
      <section class="vt-sec">
        <h4>✨ Voz de IA</h4>
        <textarea class="vt-in" id="vtTexto" rows="3" maxlength="1500" placeholder="Lo que dirá el locutor…"></textarea>
        <div class="vt-fila">
          <input class="vt-in" id="vtIndic" maxlength="200" placeholder="Indicación: «saluda a Caracas»">
          <button class="vt-btn" id="vtEscribir" title="Escribir el texto con IA a partir de las canciones">✍ Escribir</button>
        </div>
        <div class="vt-fila">
          <select class="vt-in" id="vtVoz" title="Voz"></select>
          <button class="vt-btn vt-btn--pri" id="vtCrear">🔊 Crear voz e insertar</button>
        </div>
        <p class="vt-nota" id="vtNotaIA"></p>
      </section>
      <section class="vt-sec">
        <h4>Recientes</h4>
        <div id="vtLista" class="vt-lista"></div>
      </section>`;
    $("vtRec").addEventListener("click", () => rec ? detener() : grabar());
    $("vtEscuchar").addEventListener("click", escuchar);
    $("vtDescartar").addEventListener("click", descartar);
    $("vtGuardar").addEventListener("click", guardarToma);
    $("vtEscribir").addEventListener("click", escribir);
    $("vtCrear").addEventListener("click", crearIA);
    $("vtLista").addEventListener("click", accionLista);
    montado = true;
  }

  // ── Contexto: entre qué canciones entra ──────────────────────────────────
  function contexto() {
    const v = Decks.vecinos();
    const t = x => x ? `«${esc(x.titulo)}»` : "—";
    $("vtCtx").innerHTML = v.anterior || v.siguiente
      ? `Entra después de ${t(v.anterior)}<br>y antes de ${t(v.siguiente)}${v.elegida ? "" : " <small>(elige una canción en la cola para otro lugar)</small>"}`
      : `Elige en la cola la canción después de la cual va la voz.`;
    return v;
  }

  // ── A) Grabar con el micrófono → WAV 16 bits mono ────────────────────────
  async function grabar() {
    if (!navigator.mediaDevices?.getUserMedia) {
      toast("El navegador no permite el micrófono aquí: abre el Studio como http://localhost (en la PC de la radio) o con https", "error", 9000);
      return;
    }
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false } });
    } catch (err) {
      toast(`No se pudo abrir el micrófono: ${err.message}`, "error", 7000);
      return;
    }
    descartar();
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const fuente = ctx.createMediaStreamSource(stream);
    const nodo = ctx.createScriptProcessor(4096, 1, 1);
    const partes = [];
    nodo.onaudioprocess = e => {
      const d = e.inputBuffer.getChannelData(0);
      partes.push(new Float32Array(d));
      let pico = 0;
      for (let i = 0; i < d.length; i += 16) pico = Math.max(pico, Math.abs(d[i]));
      $("vtVu").style.width = `${Math.min(100, pico * 140)}%`;
      $("vtVu").classList.toggle("alto", pico > 0.9);
    };
    fuente.connect(nodo);
    nodo.connect(ctx.destination);                 // necesario para que procese (no suena: la salida es silencio)
    rec = { ctx, stream, nodo, fuente, partes, hz: ctx.sampleRate, t0: Date.now() };
    rec.timer = setInterval(() => {
      const seg = (Date.now() - rec.t0) / 1000;
      $("vtReloj").textContent = fmt(seg);
      if (seg >= MAX_SEG) detener();
    }, 250);
    $("vtRec").textContent = "■ DETENER"; $("vtRec").classList.add("grabando");
  }

  function detener() {
    if (!rec) return;
    const r = rec; rec = null;
    clearInterval(r.timer);
    r.nodo.disconnect(); r.fuente.disconnect();
    r.stream.getTracks().forEach(t => t.stop());
    r.ctx.close();
    $("vtRec").textContent = "● GRABAR"; $("vtRec").classList.remove("grabando");
    $("vtVu").style.width = "0";
    const n = r.partes.reduce((a, p) => a + p.length, 0);
    if (n < r.hz * 0.5) { toast("Grabación demasiado corta", "error", 3000); return; }
    const wav = aWav(r.partes, n, r.hz);
    toma = { wav, url: URL.createObjectURL(wav), seg: n / r.hz };
    $("vtReloj").textContent = fmt(toma.seg);
    ["vtEscuchar", "vtGuardar", "vtDescartar"].forEach(id => { $(id).disabled = false; });
  }

  function aWav(partes, n, hz) {
    const buf = new ArrayBuffer(44 + n * 2), v = new DataView(buf);
    const txt = (o, s) => [...s].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
    txt(0, "RIFF"); v.setUint32(4, 36 + n * 2, true); txt(8, "WAVE"); txt(12, "fmt ");
    v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
    v.setUint32(24, hz, true); v.setUint32(28, hz * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
    txt(36, "data"); v.setUint32(40, n * 2, true);
    let o = 44;
    for (const p of partes) for (let i = 0; i < p.length; i++, o += 2) {
      const x = Math.max(-1, Math.min(1, p[i]));
      v.setInt16(o, x < 0 ? x * 0x8000 : x * 0x7fff, true);
    }
    return new Blob([buf], { type: "audio/wav" });
  }

  function escuchar() {
    if (!toma) return;
    if (previa && !previa.paused) { previa.pause(); $("vtEscuchar").textContent = "▶ Escuchar"; return; }
    previa = previa || new Audio();
    previa.src = toma.url; previa.currentTime = 0; previa.play();
    previa.onended = () => { $("vtEscuchar").textContent = "▶ Escuchar"; };
    $("vtEscuchar").textContent = "■ Parar";
  }

  function descartar() {
    if (previa) previa.pause();
    if (toma) URL.revokeObjectURL(toma.url);
    toma = null;
    if (!montado) return;
    $("vtReloj").textContent = "0:00"; $("vtEscuchar").textContent = "▶ Escuchar";
    ["vtEscuchar", "vtGuardar", "vtDescartar"].forEach(id => { $(id).disabled = true; });
  }

  async function guardarToma() {
    if (!toma) return;
    const fd = new FormData();
    fd.append("file", toma.wav, "voicetrack.wav");
    fd.append("titulo", $("vtTitulo").value.trim());
    $("vtGuardar").disabled = true;
    try {
      const r = await fetch("/api/v1/voicetrack/grabar", { method: "POST", body: fd });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(typeof d.detail === "string" ? d.detail : `Error ${r.status}`);
      insertar(d.audio_id, d.titulo);
      descartar(); $("vtTitulo").value = "";
      cargarLista();
    } catch (err) {
      toast(`No se guardó la grabación: ${err.message}`, "error", 7000);
      $("vtGuardar").disabled = false;
    }
  }

  // ── B) Texto y voz de IA ─────────────────────────────────────────────────
  async function escribir() {
    const v = contexto();
    $("vtEscribir").disabled = true; $("vtEscribir").textContent = "…";
    try {
      const r = await fetch("/api/v1/voicetrack/texto", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ anterior_id: v.anterior?.id || null, siguiente_id: v.siguiente?.id || null, indicacion: $("vtIndic").value }) });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || `Error ${r.status}`);
      $("vtTexto").value = d.texto;
      $("vtNotaIA").textContent = d.origen === "ia" ? "Texto escrito con IA: revísalo antes de crear la voz." :
        `Sin IA (${d.aviso || "no respondió"}): se usó la frase de plantilla.`;
    } catch (err) { toast(`No se pudo escribir el texto: ${err.message}`, "error", 6000); }
    finally { $("vtEscribir").disabled = false; $("vtEscribir").textContent = "✍ Escribir"; }
  }

  async function crearIA() {
    const texto = $("vtTexto").value.trim();
    if (texto.length < 2) { toast("Escribe lo que dirá el locutor", "error", 3000); return; }
    $("vtCrear").disabled = true; $("vtCrear").textContent = "Creando voz…";
    try {
      const r = await fetch("/api/v1/voicetrack/ia", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ texto, voz: $("vtVoz").value, titulo: $("vtTitulo").value.trim() }) });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(typeof d.detail === "string" ? d.detail : `Error ${r.status}`);
      insertar(d.audio_id, d.titulo);
      $("vtTexto").value = "";
      cargarLista();
    } catch (err) { toast(`No se creó la voz: ${err.message}`, "error", 9000); }
    finally { $("vtCrear").disabled = !voces.length; $("vtCrear").textContent = "🔊 Crear voz e insertar"; }
  }

  async function cargarVoces() {
    try {
      const d = await fetch("/api/v1/voicetrack/voces").then(r => r.json());
      voces = d.voces || []; textoIA = !!d.texto_ia;
    } catch { voces = []; }
    $("vtVoz").innerHTML = voces.length
      ? voces.map(v => `<option value="${esc(v.voz)}">${esc(v.nombre || v.voz)} · ${({ gemini: "Gemini", openai: "OpenAI", fish: "Fish Audio" })[v.proveedor] || v.proveedor}</option>`).join("")
      : `<option value="">Sin voz de IA</option>`;
    $("vtCrear").disabled = !voces.length;
    $("vtNotaIA").textContent = voces.length ? "" :
      "Para la voz de IA, el administrador debe cargar una clave de Fish Audio, Gemini u OpenAI en Admin › IA." +
      (textoIA ? "" : " Sin claves, «Escribir» usa una frase de plantilla.");
  }

  // ── Insertar y lista de recientes ────────────────────────────────────────
  function insertar(audioId, titulo) {
    Decks.agregar(audioId, `🎙 ${titulo}`);
    setTimeout(contexto, 600);
  }

  async function cargarLista() {
    try {
      const l = await fetch("/api/v1/voicetrack?limit=30").then(r => r.json());
      $("vtLista").innerHTML = l.length ? l.map(v => `
        <div class="vt-item" data-id="${v.id}" data-titulo="${esc(v.titulo)}">
          <span class="vt-item-t" title="${esc(v.titulo)}">${esc(v.titulo)}<small>${esc(v.artista || "")} · ${fmt(v.duracion)}${v.ultima_reproduccion ? " · ya sonó" : ""}</small></span>
          <button data-a="cue" title="Preescucha (tarjeta de cabina)">🎧</button>
          <button data-a="ins" title="Insertar después de la canción elegida">＋</button>
          <button data-a="del" title="A la Papelera">🗑</button>
        </div>`).join("") : `<p class="vt-nota">Todavía no hay locuciones.</p>`;
    } catch { $("vtLista").innerHTML = `<p class="vt-nota">No se pudo cargar la lista.</p>`; }
  }

  async function accionLista(e) {
    const b = e.target.closest("button[data-a]"); if (!b) return;
    const fila = b.closest(".vt-item"), id = +fila.dataset.id, titulo = fila.dataset.titulo;
    if (b.dataset.a === "cue") WS.send({ cmd: "cue", audio_id: id });
    if (b.dataset.a === "ins") insertar(id, titulo);
    if (b.dataset.a === "del" && confirm(`¿Mandar «${titulo}» a la Papelera?`)) {
      await fetch(`/api/v1/voicetrack/${id}`, { method: "DELETE" });
      cargarLista();
    }
  }

  function mostrar() {
    if (!montado) montar();
    contexto(); cargarVoces(); cargarLista();
    if (typeof LocutorIA !== "undefined") LocutorIA.mostrar(raiz);   // Locutor IA: por aprobar y listas
  }

  // La selección de la cola cambia lo que muestra «Entra después de…»
  document.addEventListener("click", e => { if (montado && !raiz.hidden && e.target.closest(".dk-item, .deck-item, [data-uid]")) setTimeout(contexto, 50); });

  return { mostrar };
})();
