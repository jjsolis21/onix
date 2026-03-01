// ONIX/frontend/admin/js/admin-app.js

const Shell = {
    // Configuración base
    config: {
        basePath: 'sections/',
        containerId: 'module-view', // El ID del main en tu index.html
        activeClass: 'active'
    },

    init() {
        console.log("Ónix FM Shell — Sistema Iniciado");
        this.bindEvents();
        // Cargar por defecto la sección 01
        this.loadSection('01-estado-global');
    },

    bindEvents() {
        document.querySelectorAll('.nav-item').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const module = btn.getAttribute('data-module');
                if (module) {
                    // Actualizar UI del menú
                    document.querySelectorAll('.nav-item').forEach(b => b.classList.remove('active'));
                    btn.classList.add('active');

                    // Mapeo de nombres a carpetas
                    const routes = {
                        'dashboard': '01-estado-global',
                        'biblioteca': '02-biblioteca-musical',
                        'pautas': '03-programacion-pautas',
                        'cartuchera': '04-editor-cartuchera',
                        'logs': '05-historial-emision',
                        'engine': '06-motor-audio'
                    };

                    this.loadSection(routes[module]);
                }
            });
        });
    },

    async loadSection(sectionName) {
        const container = document.getElementById(this.config.containerId);
        if (!container) return;

        try {
            const url = `${this.config.basePath}${sectionName}/${sectionName.split('-').slice(1).join('-')}.html`;
            const response = await fetch(url);
            if (!response.ok) throw new Error(`No se pudo cargar la pieza: ${sectionName}`);

            const html = await response.text();
            container.innerHTML = html;

            // Actualizar título en el Topbar si existe
            const titleEl = document.getElementById('module-title');
            if (titleEl) titleEl.textContent = sectionName.replace(/-/g, ' ').toUpperCase().slice(3);

        } catch (error) {
            container.innerHTML = `<div class="error">Error al engranar pieza: ${error.message}</div>`;
        }
    }
};

document.addEventListener('DOMContentLoaded', () => Shell.init());