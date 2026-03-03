/**
 * ============================================================
 * MODIFICACIÓN MÍNIMA EN admin-app.js
 *
 * Sólo se añaden:
 *   1. La función helper loadScript()     (si aún no existe)
 *   2. El case 'pautas' en el switch de navegación
 *
 * El resto de admin-app.js queda INTACTO.
 * ============================================================
 */

// ── 1. HELPER DE CARGA DINÁMICA DE SCRIPTS ─────────────────
//    Agregar UNA SOLA VEZ, cerca del top de admin-app.js
//    (omitir si ya existe una función equivalente)

function loadScript(src, onLoad) {
  const script   = document.createElement('script');
  script.src     = src;
  script.async   = true;
  script.onload  = onLoad || null;
  script.onerror = () => console.error('[admin-app] No se pudo cargar:', src);
  document.head.appendChild(script);
}

// ── 2. CASE EN EL SWITCH DE NAVEGACIÓN ─────────────────────
//    Añadir dentro del switch existente, junto a los otros cases

// ╔══════════════════════════════════════════════════════════╗
// ║  ANTES (fragmento del switch existente):                 ║
// ║                                                          ║
// ║  switch (section) {                                      ║
// ║    case 'dashboard': loadDashboard(); break;             ║
// ║    case 'audios':    loadAudios();    break;             ║
// ║    // ... otros cases ...                                ║
// ║    default: load404(); break;                            ║
// ║  }                                                       ║
// ╚══════════════════════════════════════════════════════════╝

// ╔══════════════════════════════════════════════════════════╗
// ║  DESPUÉS — solo se AÑADE el nuevo case:                  ║
// ╚══════════════════════════════════════════════════════════╝

switch (section) {
  case 'dashboard': loadDashboard(); break;
  case 'audios':    loadAudios();    break;

  // ── SECCIÓN 03: PAUTAS ── ▼ ÚNICO BLOQUE NUEVO ▼
  case 'pautas':
    loadSection('sections/03-pautas/programacion-pautas.html', () => {
      // Carga el controller solo si aún no está en memoria
      if (!window.PautasController) {
        loadScript('sections/03-pautas/pautas-controller.js', () => {
          window.PautasController.init();
        });
      } else {
        // Ya fue cargado en una visita anterior → solo re-inicializar
        window.PautasController.init();
      }
    });
    break;
  // ── FIN SECCIÓN 03 ──

  default: load404(); break;
}


// ── 3. REFERENCIA: función loadSection() ───────────────────
//    Si admin-app.js aún no tiene una función para inyectar
//    HTML en el área de contenido, usa este modelo:
//    (omitir si ya existe)

function loadSection(htmlPath, onReady) {
  const contentArea = document.getElementById('viewport-main'); // ← ID real del Shell Ónix FM
  fetch(htmlPath)
    .then(res => {
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.text();
    })
    .then(html => {
      contentArea.innerHTML = html;
      if (typeof onReady === 'function') onReady();
    })
    .catch(err => {
      console.error('[admin-app] Error al cargar sección:', err);
      contentArea.innerHTML = '<p>Error cargando la sección.</p>';
    });
}
