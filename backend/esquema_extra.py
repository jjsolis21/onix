"""
esquema_extra.py — Ónix FM
Tablas que no son del esquema base: Comerciales (clientes, agencias, rubros), ajustes de las tandas y Locutor IA.
Una sola fuente: las usan scripts/init_db.py (instalación nueva) y los módulos que las leen.
"""

ESQUEMA_COMERCIALES = """
CREATE TABLE IF NOT EXISTS comerciales_clientes (
    id INTEGER PRIMARY KEY, nombre TEXT NOT NULL COLLATE NOCASE UNIQUE,
    contacto TEXT NOT NULL DEFAULT '', telefono TEXT NOT NULL DEFAULT '', email TEXT NOT NULL DEFAULT '',
    notas TEXT NOT NULL DEFAULT '', creado_en TEXT NOT NULL DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS comerciales_rubros (nombre TEXT PRIMARY KEY COLLATE NOCASE);
CREATE TABLE IF NOT EXISTS comerciales_conflictos_rubros (
    a TEXT NOT NULL, b TEXT NOT NULL, PRIMARY KEY (a, b));
CREATE TABLE IF NOT EXISTS comerciales_agencias (
    id INTEGER PRIMARY KEY, nombre TEXT NOT NULL COLLATE NOCASE UNIQUE,
    contacto TEXT NOT NULL DEFAULT '', telefono TEXT NOT NULL DEFAULT '', email TEXT NOT NULL DEFAULT '',
    notas TEXT NOT NULL DEFAULT '', creado_en TEXT NOT NULL DEFAULT (datetime('now')));
"""

ESQUEMA_AJUSTES = """
CREATE TABLE IF NOT EXISTS tanda_ajustes (
    fecha TEXT NOT NULL, hora INTEGER NOT NULL, minuto INTEGER NOT NULL, ids TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (fecha, hora, minuto));
CREATE TABLE IF NOT EXISTS tanda_quitadas (
    fecha TEXT NOT NULL, hora INTEGER NOT NULL, pauta_id INTEGER NOT NULL,
    PRIMARY KEY (fecha, hora, pauta_id));
"""

ESQUEMA_LOCUTOR = """
CREATE TABLE IF NOT EXISTS locutor_config (
    clave TEXT PRIMARY KEY,
    valor TEXT NOT NULL DEFAULT 'null'
);
CREATE TABLE IF NOT EXISTS locutor_turnos (
    id           INTEGER PRIMARY KEY,
    desde        TEXT NOT NULL,           -- HH:MM; puede cruzar la medianoche
    hasta        TEXT NOT NULL,
    nombre       TEXT NOT NULL,
    voz          TEXT NOT NULL,           -- "fish:<id>", o nombre de voz de Gemini / OpenAI
    personalidad TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS locutor_patrocinios (
    id         INTEGER PRIMARY KEY,
    nombre     TEXT NOT NULL DEFAULT '',  -- anunciante, para nombrarlo en la apertura
    texto      TEXT NOT NULL,
    desde      TEXT NOT NULL,             -- AAAA-MM-DD
    hasta      TEXT NOT NULL,
    frecuencia INTEGER NOT NULL DEFAULT 1,  -- menciones por día
    activo     INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS locutor_fuentes (
    id          INTEGER PRIMARY KEY,
    nombre      TEXT NOT NULL,            -- como se nombra al aire: "según <nombre>"
    url         TEXT NOT NULL UNIQUE,     -- RSS o Atom
    cierre      TEXT NOT NULL DEFAULT '', -- frase con que se cierra una noticia de esa fuente
    activo      INTEGER NOT NULL DEFAULT 1,
    revisada_en TEXT,
    error       TEXT
);
CREATE TABLE IF NOT EXISTS locutor_noticias (
    id           INTEGER PRIMARY KEY,
    fuente_id    INTEGER REFERENCES locutor_fuentes(id) ON DELETE CASCADE,
    guid         TEXT NOT NULL UNIQUE,
    titulo       TEXT NOT NULL,
    resumen      TEXT NOT NULL DEFAULT '',
    enlace       TEXT,
    publicada_en TEXT,
    estado       TEXT NOT NULL DEFAULT 'nueva',   -- nueva | leida | descartada
    prioridad    INTEGER NOT NULL DEFAULT 0,
    creado_en    TEXT NOT NULL DEFAULT (datetime('now'))
);
-- Saludos y complacencias de oyentes (todavía sin canal de entrada: se cargan a mano desde el panel)
CREATE TABLE IF NOT EXISTS locutor_mensajes (
    id        INTEGER PRIMARY KEY,
    de        TEXT NOT NULL,
    para      TEXT NOT NULL DEFAULT '',
    lugar     TEXT NOT NULL DEFAULT '',
    texto     TEXT NOT NULL DEFAULT '',
    cancion   TEXT NOT NULL DEFAULT '',
    estado    TEXT NOT NULL DEFAULT 'pendiente',  -- pendiente | aprobado | rechazado | leido
    motivo    TEXT,
    origen    TEXT NOT NULL DEFAULT 'panel',
    prioridad INTEGER NOT NULL DEFAULT 0,
    audio_id  INTEGER,                            -- canción dedicada hallada en la biblioteca
    creado_en TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS locutor_intervenciones (
    id            INTEGER PRIMARY KEY,
    tipo          TEXT NOT NULL,
    estado        TEXT NOT NULL DEFAULT 'borrador',
    modo          TEXT NOT NULL DEFAULT 'auto',   -- auto | manual (cómo se decidió su aprobación)
    persona       TEXT NOT NULL DEFAULT '',
    voz           TEXT NOT NULL DEFAULT '',
    texto         TEXT,
    audio_id      INTEGER,                        -- audios.id de la locución ya generada
    duracion_seg  REAL,
    anterior_id   INTEGER,                        -- canción tras la cual entra
    siguiente_id  INTEGER,
    origen_id     INTEGER,                        -- noticia / mensaje / patrocinio que la originó
    proveedor_voz TEXT,
    bytes_tts     INTEGER NOT NULL DEFAULT 0,
    error         TEXT,
    motivo        TEXT,
    creado_en     TEXT NOT NULL DEFAULT (datetime('now')),
    decidido_en   TEXT,
    al_aire_en    TEXT
);
CREATE INDEX IF NOT EXISTS idx_locutor_interv_estado ON locutor_intervenciones (estado, creado_en);
-- Datos verificados de artistas y canciones: lo único que el locutor puede contar sobre ellos
CREATE TABLE IF NOT EXISTS locutor_datos (
    id          INTEGER PRIMARY KEY,
    ambito      TEXT NOT NULL,              -- artista | cancion
    clave       TEXT NOT NULL,              -- artista normalizado, o "artista|titulo"
    etiqueta    TEXT NOT NULL DEFAULT '',   -- como se muestra: "Danny Ocean", "Danny Ocean - Me Rehúso"
    dato        TEXT NOT NULL DEFAULT '',
    fuente      TEXT NOT NULL DEFAULT '',   -- wikipedia | musicbrainz | biblioteca | manual
    estado      TEXT NOT NULL DEFAULT 'ok', -- ok | sin_datos | descartado
    revisado_en TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (ambito, clave)
);
"""
