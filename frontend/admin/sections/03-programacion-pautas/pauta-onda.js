/**
 * pauta-onda.js — Ónix FM · onda y marcas de una pauta (pestaña «Audio y versiones»)
 *
 * No es un editor aparte: reutiliza la MISMA ventana que se usa al cargar una canción
 * (editor-cancion.js, función montar), ajustada a lo que necesita una cuña: Intro (cuándo
 * entra la voz), Outro (cuándo termina) y Mezcla (cuándo entra lo siguiente).
 * Expone la misma interfaz que el controlador de pautas ya usaba con la onda anterior.
 */
(function (global) {
  'use strict';

  const MARCAS = ['cue_intro', 'cue_outro', 'cue_mezcla'];
  let cargado = null;

  function conEditor() {
    if (global.EditorCancion && global.EditorCancion.montar) return Promise.resolve();
    if (cargado) return cargado;
    cargado = new Promise((ok, mal) => {
      const css = document.createElement('link'); css.rel = 'stylesheet';
      css.href = 'sections/02-biblioteca-musical/editor-cancion.css?v=7'; document.head.appendChild(css);
      const js = document.createElement('script'); js.src = 'sections/02-biblioteca-musical/editor-cancion.js?v=14';
      js.onload = ok; js.onerror = () => { cargado = null; mal(new Error('No se pudo cargar el editor de onda')); };
      document.head.appendChild(js);
    });
    return cargado;
  }

  const panel = () => document.getElementById('pautaOnda');
  let extra = { inicio_coro: 0, final_coro: 0 };       // marcas de coro que la pauta conserva pero no edita aquí

  async function montar(url, cues, audioId) {
    await conEditor();
    const cont = panel(); if (!cont) return;
    cues = cues || {};
    extra = { inicio_coro: cues.inicio_coro || 0, final_coro: cues.final_coro || 0 };
    cont.hidden = false;
    global.EditorCancion.montar(cont, {
      marcas: MARCAS, audioId: audioId || null, url, antes: 0,    // al escuchar una marca, suena justo desde ella
      valores: { cue_intro: cues.intro, cue_outro: cues.outro, cue_mezcla: cues.mezcla },
    });
  }

  global.PautaOnda = {
    /** Carga (si falta) la ventana de canción: la usan también la biblioteca de cuñas y el selector */
    listo: conEditor,
    /** Se llama al abrir el modal: deja la onda vacía y lista */
    async init() {
      await conEditor().catch(() => { });
      this.destroy();
    },
    loadUrl(url, cues, audioId) { return montar(url, cues, audioId); },
    loadFile(file) { return montar(URL.createObjectURL(file), {}, null); },
    /** Marcas como las espera el controlador (intro / outro / mezcla en segundos) */
    getMarkers() {
      let v = {};
      try { v = global.EditorCancion.valoresMontados(); } catch (_) { }
      return {
        inicio: 0, intro: v.cue_intro || 0, outro: v.cue_outro || 0, mezcla: v.cue_mezcla || 0,
        inicio_coro: extra.inicio_coro, final_coro: extra.final_coro, fade_in: 0, fade_out: 0, volumen: 100,
      };
    },
    destroy() {
      try { global.EditorCancion.cerrarMontado(); } catch (_) { }
      const cont = panel(); if (cont) { cont.innerHTML = ''; cont.hidden = true; }
    },
  };
})(window);
