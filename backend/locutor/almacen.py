"""
almacen.py — Locutor IA de Ónix FM
Tablas y acceso a datos del locutor, en la misma base que el resto de Ónix (una sola emisora, sin "clientes").
Todas las tablas llevan el prefijo `locutor_`. `importar_de_azuracast` trae la configuración del Locutor IA
anterior (turnos, voces, patrocinios, fuentes de noticias, reglas) sin tocar claves ni credenciales.
"""

import json
import sqlite3
from datetime import timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any, Optional

from backend.paths import DB_PATH

TIPOS = ("corta", "contenido", "hora_clima", "patrocinio", "identificacion", "noticia", "apertura", "complacencia")

# Quién decide qué sale al aire: "auto" entra solo; "manual" queda en borrador hasta que alguien lo aprueba.
APROBACION_DEFECTO = {"corta": "auto", "contenido": "manual", "hora_clima": "auto", "identificacion": "auto",
                      "apertura": "auto", "patrocinio": "manual", "noticia": "manual", "complacencia": "manual"}

# Estados de una intervención:
#   borrador   texto escrito, espera aprobación (nada de voz todavía: no se gasta TTS en lo que se rechaza)
#   aprobada   texto aprobado, falta generar la voz
#   lista      voz generada y registrada como audio; todavía no suena
#   al_aire    ya sonó
#   descartada rechazada por una persona o por el filtro
#   fallo      no se pudo escribir o locutar (queda el motivo)
ESTADOS = ("borrador", "aprobada", "lista", "al_aire", "descartada", "fallo")

_ESQUEMA = """
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


_FIJAS = {"America/Caracas": timezone(timedelta(hours=-4), "VET")}   # sin DST desde 2016


def zona(nombre: str) -> tzinfo:
    """Zona horaria de la emisora. Windows no trae la base `tzdata`: sin ella se usa la hora fija conocida."""
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(nombre)
    except Exception:
        return _FIJAS.get(nombre) or timezone(timedelta(hours=-4), "VET")


def conectar(db_path: Any = None) -> sqlite3.Connection:
    c = sqlite3.connect(str(db_path or DB_PATH), timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    c.executescript(_ESQUEMA)
    return c


# ── Configuración (clave → JSON) ────────────────────────────────────────────

def leer(c: sqlite3.Connection, clave: str, defecto: Any = None) -> Any:
    r = c.execute("SELECT valor FROM locutor_config WHERE clave = ?", (clave,)).fetchone()
    if not r:
        return defecto
    try:
        return json.loads(r[0])
    except ValueError:
        return defecto


def guardar(c: sqlite3.Connection, clave: str, valor: Any) -> None:
    c.execute("INSERT INTO locutor_config (clave, valor) VALUES (?, ?)"
              " ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor",
              (clave, json.dumps(valor, ensure_ascii=False)))


def persona(c: sqlite3.Connection) -> dict:
    p = {"nombre": "Locutor", "personalidad": "", "muletillas": [], "prohibidos": [], "voz": "", "pronunciacion": {}}
    p.update(leer(c, "persona", {}) or {})
    return p


def reglas(c: sqlite3.Connection) -> dict:
    r = {"tipos_permitidos": [], "franjas": [], "salidas": {}, "noticias_cada_min": 60, "noticias_vetos": [],
         "noticias_max_horas": 18, "patron": [], "cada_canciones": 2, "intervenciones_por_hora": 4,
         "datos_curiosos": True,     # presentar con un dato verificado del artista o la canción cuando lo haya
         "cierre_complacencia": "Claro que sí, aquí te complacemos, {nombre}. ¡Disfrútala!"}
    r.update(leer(c, "reglas", {}) or {})
    return r


def aprobacion(c: sqlite3.Connection) -> dict:
    a = dict(APROBACION_DEFECTO)
    a.update({k: v for k, v in (leer(c, "aprobacion", {}) or {}).items() if k in TIPOS and v in ("auto", "manual")})
    return a


def emisora(c: sqlite3.Connection) -> dict:
    e = {"nombre": "", "ciudad": "", "genero": "", "zona_horaria": "America/Caracas"}
    e.update(leer(c, "emisora", {}) or {})
    if not e["nombre"]:
        try:
            r = c.execute("SELECT valor FROM config WHERE clave = 'emisora_nombre'").fetchone()
            e["nombre"] = (r[0] if r and r[0] else "") or "ÓNIX FM"
        except sqlite3.OperationalError:
            e["nombre"] = "ÓNIX FM"
    return e


def persona_al_aire(c: sqlite3.Connection, hhmm: str) -> dict:
    """El locutor del turno de esa hora; sin turno que la cubra, la persona por defecto."""
    base = persona(c)
    for t in c.execute("SELECT * FROM locutor_turnos ORDER BY desde, id"):
        d, h = t["desde"], t["hasta"]
        if (d <= hhmm < h) if d <= h else (hhmm >= d or hhmm < h):
            return {**base, "nombre": t["nombre"], "voz": t["voz"],
                    "personalidad": t["personalidad"] or base["personalidad"]}
    return base


# ── Intervenciones ──────────────────────────────────────────────────────────

def crear_intervencion(c: sqlite3.Connection, tipo: str, *, estado: str = "borrador", modo: str = "auto",
                       persona_nombre: str = "", voz: str = "", texto: Optional[str] = None,
                       anterior_id: Optional[int] = None, siguiente_id: Optional[int] = None,
                       origen_id: Optional[int] = None, error: Optional[str] = None) -> int:
    if tipo not in TIPOS:
        raise ValueError(f"Tipo desconocido: {tipo}")
    if estado not in ESTADOS:
        raise ValueError(f"Estado desconocido: {estado}")
    cur = c.execute(
        "INSERT INTO locutor_intervenciones (tipo, estado, modo, persona, voz, texto, anterior_id, siguiente_id,"
        " origen_id, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (tipo, estado, modo, persona_nombre, voz, texto, anterior_id, siguiente_id, origen_id, error))
    c.commit()
    return cur.lastrowid


def cambiar_estado(c: sqlite3.Connection, iid: int, estado: str, **campos: Any) -> None:
    if estado not in ESTADOS:
        raise ValueError(f"Estado desconocido: {estado}")
    permitidos = {"texto", "audio_id", "duracion_seg", "proveedor_voz", "bytes_tts", "error", "motivo", "al_aire_en"}
    extra = {k: v for k, v in campos.items() if k in permitidos}
    sets = ", ".join(f"{k} = ?" for k in extra)
    c.execute(f"UPDATE locutor_intervenciones SET estado = ?, decidido_en = datetime('now')"
              f"{', ' + sets if sets else ''} WHERE id = ?", [estado, *extra.values(), iid])
    c.commit()


# ── Importación desde el Locutor IA de AzuraCast ────────────────────────────

_TABLAS_PROPIAS = ("locutor_turnos", "locutor_patrocinios", "locutor_fuentes")


def importar_de_azuracast(c: sqlite3.Connection, origen: Any, slug: str = "solisfm",
                          reemplazar: bool = False) -> dict:
    """
    Copia la configuración del Locutor IA anterior (su `locutor.db`) a este sistema.
    No toca claves, credenciales ni el historial (intervenciones, noticias, mensajes de la otra instalación).
    Si ya hay turnos, patrocinios o fuentes cargados no hace nada, salvo reemplazar=True.
    """
    ruta = Path(origen)
    if not ruta.is_file():
        raise FileNotFoundError(f"No existe {ruta}")
    if not reemplazar and any(c.execute(f"SELECT 1 FROM {t} LIMIT 1").fetchone() for t in _TABLAS_PROPIAS):
        return {"importado": False, "motivo": "Ya hay configuración del locutor cargada"}
    o = sqlite3.connect(f"file:{ruta.as_posix()}?mode=ro", uri=True)
    o.row_factory = sqlite3.Row
    try:
        cli = o.execute("SELECT * FROM clientes WHERE slug = ?", (slug,)).fetchone()
        if not cli:
            raise ValueError(f"No hay una emisora '{slug}' en {ruta.name}")
        cid = cli["id"]
        cols_reglas = {r[1] for r in o.execute("PRAGMA table_info(reglas)")}
        cols_pers = {r[1] for r in o.execute("PRAGMA table_info(personas_dj)")}
        cols_pat = {r[1] for r in o.execute("PRAGMA table_info(patrocinios)")}
        cols_fue = {r[1] for r in o.execute("PRAGMA table_info(fuentes_noticias)")}

        def j(valor, defecto):
            try:
                return json.loads(valor) if valor else defecto
            except ValueError:
                return defecto

        if reemplazar:
            for t in _TABLAS_PROPIAS:
                c.execute(f"DELETE FROM {t}")
        guardar(c, "emisora", {"nombre": cli["nombre"], "ciudad": cli["ciudad"], "genero": cli["genero"],
                               "zona_horaria": cli["zona_horaria"]})

        p = o.execute("SELECT * FROM personas_dj WHERE cliente_id = ?", (cid,)).fetchone()
        if p:
            voz = f"fish:{p['voz_id']}" if p["voz_id"] and (p["proveedor_voz"] or "fish") == "fish" else (p["voz_id"] or "")
            guardar(c, "persona", {
                "nombre": p["nombre"], "personalidad": p["personalidad"], "muletillas": j(p["muletillas"], []),
                "prohibidos": j(p["prohibidos"], []), "voz": voz,
                "pronunciacion": j(p["pronunciacion"], {}) if "pronunciacion" in cols_pers else {}})

        r = o.execute("SELECT * FROM reglas WHERE cliente_id = ?", (cid,)).fetchone()
        if r:
            reg = {"intervenciones_por_hora": r["intervenciones_por_hora"],
                   "tipos_permitidos": j(r["tipos_permitidos"], []), "franjas": j(r["franjas"], [])}
            for campo, defecto in (("salidas", {}), ("noticias_vetos", []), ("patron", [])):
                if campo in cols_reglas:
                    reg[campo] = j(r[campo], defecto)
            for campo in ("noticias_cada_min", "noticias_max_horas", "cada_canciones", "cierre_complacencia"):
                if campo in cols_reglas and r[campo] is not None:
                    reg[campo] = r[campo]
            guardar(c, "reglas", reg)

        # Catálogo de voces registradas (nombre → voz). Son identificadores del proveedor, no claves.
        if o.execute("SELECT 1 FROM sqlite_master WHERE name = 'voces'").fetchone():
            guardar(c, "voces", [
                {"nombre": v["nombre"], "voz": f"fish:{v['voz_id']}" if v["proveedor"] == "fish" else v["voz_id"],
                 "proveedor": v["proveedor"], "genero": v["genero"]}
                for v in o.execute("SELECT nombre, voz_id, proveedor, genero FROM voces ORDER BY nombre")])

        n_turnos = n_pat = n_fue = 0
        for t in o.execute("SELECT * FROM turnos WHERE cliente_id = ? ORDER BY desde", (cid,)):
            vid = t["voz_id"]
            voz = vid if (not vid or ":" in vid) else f"fish:{vid}"   # los ids de Fish son hexadecimales largos
            c.execute("INSERT INTO locutor_turnos (desde, hasta, nombre, voz, personalidad) VALUES (?, ?, ?, ?, ?)",
                      (t["desde"], t["hasta"], t["nombre"], voz, t["personalidad"]))
            n_turnos += 1
        for pa in o.execute("SELECT * FROM patrocinios WHERE cliente_id = ?", (cid,)):
            c.execute("INSERT INTO locutor_patrocinios (nombre, texto, desde, hasta, frecuencia, activo)"
                      " VALUES (?, ?, ?, ?, ?, ?)",
                      (pa["nombre"] if "nombre" in cols_pat else "", pa["texto"], pa["desde"], pa["hasta"],
                       pa["frecuencia"], pa["activo"]))
            n_pat += 1
        for f in o.execute("SELECT * FROM fuentes_noticias WHERE cliente_id = ?", (cid,)):
            c.execute("INSERT OR IGNORE INTO locutor_fuentes (nombre, url, cierre, activo) VALUES (?, ?, ?, ?)",
                      (f["nombre"], f["url"], f["cierre"] if "cierre" in cols_fue else "", f["activo"]))
            n_fue += 1
        c.commit()
        return {"importado": True, "turnos": n_turnos, "patrocinios": n_pat, "fuentes": n_fue,
                "persona": bool(p), "reglas": bool(r)}
    finally:
        o.close()
