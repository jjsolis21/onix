/* Iconos propios (SVG en /static/studio/icons/) para los botones de reproducción del Admin.
   Uso: ICO("play2") → <img> en línea. Nombres: play2, pause2, stop2, atras2, avanzar2. */
window.ICO = (nombre, clase = "") =>
  `<img class="ico ${clase}" src="/static/studio/icons/${nombre}.svg" alt="" draggable="false">`;
