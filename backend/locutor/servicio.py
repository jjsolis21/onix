"""
servicio.py — Locutor IA de Ónix FM
Cada ciclo mira el programa que ya tiene el motor y, donde toca hablar entre dos canciones:
  elegir tipo → escribir (IA, o plantilla si falla) → validar «cero datos inventados» →
  aprobación (auto | manual) → voz → audio `Locucion` → insertarlo tras la canción.

Regla de oro: la voz se genera ANTES de salir al aire, nunca en el momento. Si algo no llega a tiempo o falla,
esa intervención se descarta y suena la música; jamás silencio ni audio a medias.
El motor no se toca: el Locutor solo usa `programa()` y `plan_insertar()`, como el operador.
"""

import logging
import re
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

from backend.ia import proveedores
from backend.locutor import almacen, datos as datos_mod, guion as g, noticias, plantillas
from backend.locutor.metadatos import limpiar_pista
from backend.locutor.clima import clima_actual
from backend.locutor.validador import validar_guion

log = logging.getLogger("locutor")

# entrada del patrón de la hora → tipo de intervención
ENTRADAS = {"apertura": "apertura", "noticia": "noticia", "patrocinio": "patrocinio", "hora": "hora_clima",
            "hora_clima": "hora_clima", "complacencia": "complacencia", "cancion": "corta", "corta": "corta",
            "identificacion": "identificacion"}
ADELANTO_MIN = 45        # cuántos minutos hacia adelante del programa se atienden
MARGEN_SEG = 90          # tiempo mínimo que debe faltar para la canción tras la cual entra la voz
ACTIVOS = ("borrador", "aprobada", "lista", "al_aire", "fallo")   # estados que cuentan para no repetir un hueco
_ETIQ = re.compile(r"\s*\[(?:excited|emphasis|break)\]\s*", re.I)


def _es_musica(it: dict) -> bool:
    return it.get("tipo") == "audio" and (it.get("tipo_audio") or "Musica") == "Musica"


def _es_voz(it: dict) -> bool:
    return it.get("tipo") == "audio" and it.get("tipo_audio") == "Locucion"


def _momento(hora: int) -> str:
    if 5 <= hora < 12:
        return "mañana"
    if 12 <= hora < 19:
        return "tarde"
    return "noche" if hora >= 19 else "madrugada"


def _hora_item(it: dict, ahora: datetime) -> Optional[datetime]:
    try:
        h = datetime.strptime(it["hora"], "%H:%M:%S").time()
    except (KeyError, ValueError, TypeError):
        return None
    dt = datetime.combine(ahora.date(), h, tzinfo=ahora.tzinfo)
    if dt < ahora - timedelta(hours=12):
        dt += timedelta(days=1)
    return dt


def _en_franja(franjas: list, hhmm: str) -> bool:
    if not franjas:
        return True
    return any((d <= hhmm < h) if d <= h else (hhmm >= d or hhmm < h) for d, h in franjas)


# ── Qué hueco y qué tipo ────────────────────────────────────────────────────

def huecos(items: list, cada: int) -> list:
    """Índices i de canciones tras las que toca hablar: entre dos canciones y `cada` canciones sin voz."""
    out = []
    for i in range(len(items) - 1):
        if not (_es_musica(items[i]) and _es_musica(items[i + 1])):
            continue
        n = 0                                   # canciones desde la última voz (o desde el inicio del programa)
        for j in range(i, -1, -1):
            if _es_voz(items[j]):
                break
            if _es_musica(items[j]):
                n += 1
        if n >= cada:
            out.append(i)
    return out


def _minutos_desde(c, tipo: str) -> float:
    r = c.execute(
        "SELECT TIMESTAMPDIFF(SECOND, MAX(creado_en), NOW()) / 60.0 FROM locutor_intervenciones"
        " WHERE tipo = ? AND estado IN ('borrador','aprobada','lista','al_aire')", (tipo,)).fetchone()
    return float("inf") if r[0] is None else float(r[0])


def _patrocinios_vigentes(c, hoy: str) -> list:
    return c.execute("SELECT * FROM locutor_patrocinios WHERE activo = 1 AND desde <= ? AND hasta >= ? ORDER BY id",
                     (hoy, hoy)).fetchall()


def _mensaje_listo(c) -> Optional[Any]:
    return c.execute("SELECT * FROM locutor_mensajes WHERE estado = 'aprobado' ORDER BY prioridad DESC, id LIMIT 1"
                     ).fetchone()


def elegir_tipo(c, reglas: dict, ahora: datetime) -> Optional[str]:
    permitidos = reglas["tipos_permitidos"] or list(almacen.TIPOS)
    hoy = ahora.strftime("%Y-%m-%d")

    def hay_material(tipo: str) -> bool:
        if tipo == "noticia":
            return noticias.siguiente(c, reglas["noticias_max_horas"]) is not None
        if tipo == "patrocinio":
            return bool(_patrocinios_vigentes(c, hoy))
        if tipo == "complacencia":
            return _mensaje_listo(c) is not None
        return True

    elegido = None
    if reglas["patron"]:
        hechas = c.execute(      # con el reloj de la base, el mismo con que se guardó creado_en
            "SELECT COUNT(*) FROM locutor_intervenciones WHERE creado_en >= DATE_SUB(NOW(), INTERVAL ? MINUTE) AND estado IN"
            " ('borrador','aprobada','lista','al_aire','fallo')",       # un intento fallido también avanza el patrón
            (int(ahora.minute),)).fetchone()[0]
        entrada = reglas["patron"][hechas % len(reglas["patron"])]
        elegido = ENTRADAS.get(entrada)
    else:
        if "hora_clima" in permitidos and _minutos_desde(c, "hora_clima") >= 60:
            elegido = "hora_clima"
        elif "patrocinio" in permitidos and _patrocinios_vigentes(c, hoy):
            vig = _patrocinios_vigentes(c, hoy)
            if _minutos_desde(c, "patrocinio") >= 1440 / max(1, sum(p["frecuencia"] for p in vig)):
                elegido = "patrocinio"
        if elegido is None and "noticia" in permitidos and \
                _minutos_desde(c, "noticia") >= max(5, reglas["noticias_cada_min"]):
            elegido = "noticia"
    if elegido and (elegido not in permitidos or not hay_material(elegido)):
        elegido = None
    if elegido:
        return elegido
    if "corta" in permitidos:
        return "corta"
    return "identificacion" if "identificacion" in permitidos else None


# ── Escribir ────────────────────────────────────────────────────────────────

def _datos_de(c, pista: Optional[dict], audio_id: Optional[int], buscar: Callable) -> list:
    """Datos verificados de esa canción y su artista (vacío si no hay o si algo falla: entonces va la corta)."""
    if not pista:
        return []
    anio = None
    if audio_id:
        try:
            r = c.execute("SELECT fecha_lanzamiento FROM audios WHERE id = ?", (audio_id,)).fetchone()
            anio = r[0] if r else None
        except Exception:
            pass
    try:
        return buscar(c, pista["artista"], pista["titulo"], anio)
    except Exception as e:
        log.warning("datos de «%s»: %s", pista.get("titulo"), e)
        return []


def preparar(c, tipo: str, anterior: Optional[dict], siguiente: Optional[dict], ahora: datetime,
             *, anterior_id: Optional[int] = None, siguiente_id: Optional[int] = None,
             forzar_manual: bool = False, buscar_datos: Optional[Callable] = None) -> dict:
    """
    Escribe y valida el texto de una intervención y la deja `borrador` (si pide aprobación) o `aprobada`.
    Devuelve {"id", "estado", "texto", "tipo"}; si no se puede escribir, estado `fallo` con el motivo.
    """
    emi, per, reg = almacen.emisora(c), almacen.persona_al_aire(c, ahora.strftime("%H:%M")), almacen.reglas(c)
    # Una presentación de canciones lleva un dato verificado cuando lo hay; si no, se queda en la corta de siempre
    datos_ant, datos_sig = [], []
    buscar_datos = buscar_datos or datos_mod.datos_para
    if tipo in ("corta", "contenido") and siguiente and reg.get("datos_curiosos", True):
        datos_ant = _datos_de(c, anterior, anterior_id, buscar_datos)[:2]      # el del artista y el de la canción
        datos_sig = _datos_de(c, siguiente, siguiente_id, buscar_datos)[:2]
    if tipo in ("corta", "contenido"):
        tipo = "contenido" if (datos_ant or datos_sig) else "corta"
    modo = "manual" if forzar_manual else almacen.aprobacion(c).get(tipo, "manual")
    hoy = ahora.strftime("%Y-%m-%d")
    origen_id = None
    ctx = g.Contexto(
        emisora=emi["nombre"], ciudad=emi["ciudad"], genero=emi["genero"],
        hora_local=(f"{ahora.hour % 12 or 12}:{ahora:%M} de la {_momento(ahora.hour)} (dila así, en 12 horas)"
                    if tipo == "hora_clima" else ""),
        momento=_momento(ahora.hour) if tipo in ("hora_clima", "saludo", "apertura") else "",
        recientes=[r[0] for r in c.execute(
            "SELECT texto FROM locutor_intervenciones WHERE texto IS NOT NULL AND estado IN"
            " ('lista','al_aire') ORDER BY id DESC LIMIT 8")],
        hora_hablada=plantillas.hora_hablada(ahora.hour, ahora.minute, _momento(ahora.hour))
        if tipo == "hora_clima" else "")

    def fallo(motivo: str) -> dict:
        iid = almacen.crear_intervencion(c, tipo, estado="fallo", modo=modo, persona_nombre=per["nombre"],
                                         voz=per["voz"], anterior_id=anterior_id, siguiente_id=siguiente_id,
                                         origen_id=origen_id, error=motivo[:300])
        return {"id": iid, "estado": "fallo", "texto": None, "tipo": tipo, "error": motivo}

    if tipo == "hora_clima":
        ctx.clima = clima_actual(emi["ciudad"])
    if tipo in ("corta", "contenido"):
        if not siguiente:
            return fallo("Falta la canción siguiente")
        ctx.anterior, ctx.siguiente = anterior, siguiente
        ctx.datos_anterior, ctx.datos_verificados = datos_ant, datos_sig
    elif tipo == "patrocinio":
        vig = _patrocinios_vigentes(c, hoy)
        if not vig:
            return fallo("Sin patrocinios vigentes")
        n = c.execute("SELECT COUNT(*) FROM locutor_intervenciones WHERE tipo = 'patrocinio'"
                      " AND estado IN ('lista','al_aire')").fetchone()[0]
        elegido = vig[n % len(vig)]
        ctx.patrocinio, origen_id = elegido["texto"], elegido["id"]
    elif tipo == "apertura":
        ctx.siguiente = siguiente
        ctx.anunciantes = [v["nombre"] for v in _patrocinios_vigentes(c, hoy) if v["nombre"].strip()][:4]
    elif tipo == "complacencia":
        m = _mensaje_listo(c)
        if m is None:
            return fallo("Sin saludos por leer")
        origen_id = m["id"]
        ctx.mensaje = {"de": m["de"], "para": m["para"], "lugar": m["lugar"], "texto": m["texto"], "cancion": ""}
    elif tipo == "noticia":
        leidos = noticias.titulares_leidos(c)
        n = None
        for _ in range(15):
            n = noticias.siguiente(c, reg["noticias_max_horas"])
            if n is None or n["prioridad"] or not noticias.es_repetida(n["titulo"], leidos):
                break
            c.execute("UPDATE locutor_noticias SET estado = 'descartada' WHERE id = ?", (n["id"],))
        c.commit()
        if n is None:
            return fallo("Sin noticias nuevas")
        origen_id = n["id"]
        ctx.noticia = {"fuente": n["fuente"] or "", "titulo": n["titulo"], "resumen": n["resumen"],
                       "cierre": n["cierre"] or ""}

    objetivo, maximo = g.DURACIONES[tipo]
    if tipo == "patrocinio":
        objetivo = max(objetivo, len(ctx.patrocinio.split()) / g.PALABRAS_POR_SEGUNDO * 1.25 + 2)
    permitido = "\n".join([ctx.datos(), per["nombre"], *per["muletillas"], ahora.strftime("%I:%M")])
    aviso = None
    try:
        gu = g.generar_guion(per, ctx, tipo, objetivo)
        texto, origen_texto = gu.texto, "ia"
    except g.ErrorGuion as e:
        n = c.execute("SELECT COUNT(*) FROM locutor_intervenciones WHERE tipo = ?", (tipo,)).fetchone()[0]
        texto = plantillas.guion_de_emergencia(tipo, per, ctx, n)
        if texto is None:
            return fallo(f"Sin IA y sin plantilla para «{tipo}»: {e}")
        aviso, origen_texto = f"Texto de emergencia (falló la IA: {e})", "plantilla"
    turno = c.execute("SELECT COUNT(*) FROM locutor_intervenciones WHERE tipo = ?", (tipo,)).fetchone()[0]
    texto = g.moderar_etiquetas(texto, turno, tipo)
    if ctx.mensaje and reg.get("cierre_complacencia") and ctx.mensaje.get("cancion"):
        texto = f"{texto.rstrip()} [break] {reg['cierre_complacencia'].replace('{nombre}', ctx.mensaje['de']).strip()}"
    if ctx.noticia and ctx.noticia.get("cierre"):
        cierre = g.para_voz(ctx.noticia["cierre"])
        texto = f"{texto.rstrip()} [break] {cierre}" + ("" if cierre.endswith((".", "!", "?")) else ".")
    problemas = [] if tipo == "patrocinio" and origen_texto == "ia" and False else \
        __import__("backend.locutor.validador", fromlist=["validar_guion"]).validar_guion(
            texto, permitido, per["prohibidos"])
    if problemas:
        if origen_texto == "ia":    # una sola vuelta de emergencia si el modelo inventó algo
            alt = plantillas.guion_de_emergencia(tipo, per, ctx, turno)
            if alt:
                texto, aviso = alt, "Texto de emergencia (la IA incluyó datos fuera de los metadatos)"
                problemas = []
        if problemas:
            return fallo("Guion rechazado: " + "; ".join(problemas))
    iid = almacen.crear_intervencion(
        c, tipo, estado="borrador" if modo == "manual" else "aprobada", modo=modo, persona_nombre=per["nombre"],
        voz=per["voz"], texto=texto, anterior_id=anterior_id, siguiente_id=siguiente_id, origen_id=origen_id,
        error=aviso)
    if origen_id and tipo == "noticia":
        c.execute("UPDATE locutor_noticias SET estado = 'leida' WHERE id = ?", (origen_id,))
    if origen_id and tipo == "complacencia":
        c.execute("UPDATE locutor_mensajes SET estado = 'leido', prioridad = 0 WHERE id = ?", (origen_id,))
    c.commit()
    return {"id": iid, "estado": "borrador" if modo == "manual" else "aprobada", "texto": texto, "tipo": tipo}


# ── Locutar e insertar ──────────────────────────────────────────────────────

def locutar(c, iid: int) -> dict:
    """Voz del texto aprobado → audio `Locucion` en la biblioteca → estado `lista`. No lo inserta."""
    from backend.api import voicetrack
    r = c.execute("SELECT * FROM locutor_intervenciones WHERE id = ?", (iid,)).fetchone()
    if r is None:
        raise KeyError(iid)
    if r["estado"] not in ("aprobada", "fallo") or not r["texto"]:
        raise ValueError(f"La intervención está en «{r['estado']}»: no se puede locutar")
    per = almacen.persona(c)
    texto = g.aplicar_pronunciacion(r["texto"], per.get("pronunciacion") or {})
    voz = r["voz"] or per.get("voz") or ""
    if not voz.startswith("fish:"):          # solo Fish entiende las etiquetas de voz; las demás las leerían
        texto = _ETIQ.sub(" ", texto)
    texto = " ".join(texto.split())[:1500]
    try:
        v = proveedores.generar_voz(texto, voz, (per.get("personalidad") or "")[:300])
    except proveedores.IAError as e:
        almacen.cambiar_estado(c, iid, "fallo", error=f"Voz: {e}"[:300])
        return {"id": iid, "estado": "fallo", "error": str(e)}
    titulo = f"Locutor · {r['tipo']} · {' '.join(r['texto'].split())[:50]}"
    out = voicetrack._guardar(c, v["wav"], titulo, r["persona"] or "LOCUTOR IA")
    almacen.cambiar_estado(c, iid, "lista", audio_id=out["audio_id"], duracion_seg=out["duracion"],
                           proveedor_voz=v["proveedor"], bytes_tts=len(texto.encode("utf-8")))
    return {"id": iid, "estado": "lista", "audio_id": out["audio_id"], "duracion": out["duracion"]}


# audio_id → momento (monotonic) en que se pidió insertarlo. El motor aplica `plan_insertar` en su siguiente tick
# (~50 ms), así que un `programa()` pedido enseguida todavía no la trae: sin esta memoria se insertaba dos veces
# y la locución se oía repetida.
_colocadas: dict = {}
GUARDA_INSERCION_SEG = 30


def insertar(c, iid: int, engine) -> bool:
    """Mete la locución en el programa, justo después de la canción que la origina."""
    r = c.execute("SELECT * FROM locutor_intervenciones WHERE id = ?", (iid,)).fetchone()
    if not r or not r["audio_id"]:
        return False
    ahora = time.monotonic()
    if ahora - _colocadas.get(r["audio_id"], -1e9) < GUARDA_INSERCION_SEG:
        return True                                   # recién pedida: el motor la está colocando
    items = engine.programa().get("items", [])
    if any(i.get("audio_id") == r["audio_id"] for i in items):
        return True                                   # ya está
    for pos, it in enumerate(items):
        if it.get("tipo") == "audio" and it.get("audio_id") == r["anterior_id"] and _es_musica(it):
            if engine.plan_insertar(r["audio_id"], pos + 1):
                _colocadas[r["audio_id"]] = ahora
                return True
            return False
    return False


# ── Ciclo ───────────────────────────────────────────────────────────────────

def ciclo(engine, db_path: Any = None, ahora: Optional[datetime] = None,
          *, leer_noticias: Callable = noticias.leer_fuente) -> dict:
    """Una pasada. Devuelve un resumen de lo que hizo (para el estado y los tests)."""
    c = almacen.conectar(db_path)
    try:
        resumen = {"activo": bool(almacen.leer(c, "activo", False)), "hecho": []}
        if not resumen["activo"]:
            return resumen
        emi = almacen.emisora(c)
        ahora = ahora or datetime.now(almacen.zona(emi["zona_horaria"]))
        reg = almacen.reglas(c)
        try:
            noticias.actualizar(c, reg["noticias_vetos"], leer=leer_noticias)
        except Exception as e:                      # una fuente caída no detiene al locutor
            log.warning("noticias: %s", e)
        prog = engine.programa()
        items = [i for i in prog.get("items", []) if i.get("tipo") == "audio"]
        ids_prog = {i.get("audio_id") for i in items}
        resumen["programa"] = len(items)

        # 1) aprobadas (auto o por una persona) → voz → insertar
        for r in c.execute("SELECT id FROM locutor_intervenciones WHERE estado = 'aprobada' ORDER BY id").fetchall():
            res = locutar(c, r["id"])
            if res["estado"] == "lista" and not insertar(c, r["id"], engine):
                almacen.cambiar_estado(c, r["id"], "descartada", motivo="Ya pasó su lugar en el programa")
                res["estado"] = "descartada"
            resumen["hecho"].append(res)

        # 2) las ya locutadas: comprobar que siguen en el programa; si ya sonaron, cerrarlas
        for r in c.execute("SELECT * FROM locutor_intervenciones WHERE estado = 'lista'").fetchall():
            if r["audio_id"] in ids_prog:
                continue
            a = c.execute("SELECT ultima_reproduccion FROM audios WHERE id = ?", (r["audio_id"],)).fetchone()
            if a and a["ultima_reproduccion"]:
                almacen.cambiar_estado(c, r["id"], "al_aire", al_aire_en=a["ultima_reproduccion"])
            elif r["anterior_id"] in ids_prog and insertar(c, r["id"], engine):
                continue
            else:
                viejo = c.execute("SELECT TIMESTAMPDIFF(SECOND, ?, NOW()) / 3600.0", (r["creado_en"],)).fetchone()[0]
                if viejo > 3:
                    almacen.cambiar_estado(c, r["id"], "descartada", motivo="No llegó a sonar")

        # 3) borradores/aprobadas cuya canción ya pasó: ya no tienen lugar
        for r in c.execute("SELECT * FROM locutor_intervenciones WHERE estado = 'borrador'").fetchall():
            if r["anterior_id"] and r["anterior_id"] not in ids_prog and items:
                viejo = c.execute("SELECT TIMESTAMPDIFF(SECOND, ?, NOW()) / 60.0", (r["creado_en"],)).fetchone()[0]
                if viejo > 10:
                    almacen.cambiar_estado(c, r["id"], "descartada", motivo="Ya pasó su lugar en el programa")

        # 4) un hueco nuevo por ciclo
        if not _en_franja(reg["franjas"], ahora.strftime("%H:%M")):
            resumen["hecho"].append({"estado": "fuera_de_franja"})
            return resumen
        cada = max(1, int(reg["cada_canciones"] or 2))
        for i in huecos(items, cada):
            a, b = items[i], items[i + 1]
            t = _hora_item(a, ahora)
            if t is None or t < ahora + timedelta(seconds=MARGEN_SEG) or t > ahora + timedelta(minutes=ADELANTO_MIN):
                continue
            ocupado = c.execute(
                "SELECT 1 FROM locutor_intervenciones WHERE anterior_id = ? AND estado IN"
                " ('borrador','aprobada','lista','al_aire','fallo') AND creado_en >= datetime('now', '-3 hours')"
                " LIMIT 1", (a["audio_id"],)).fetchone()
            if ocupado:
                continue
            tipo = elegir_tipo(c, reg, ahora)
            if tipo is None:
                break
            fin = _hora_item(b, ahora) or t
            anterior = limpiar_pista(a.get("artista"), a.get("titulo"))
            siguiente = limpiar_pista(b.get("artista"), b.get("titulo"))
            if tipo in ("corta", "contenido") and siguiente is None:
                # metadatos sucios: no se nombra una canción de la que no hay nombre confiable
                tipo = "identificacion" if "identificacion" in (reg["tipos_permitidos"] or almacen.TIPOS) else None
                if tipo is None:
                    continue
            res = preparar(c, tipo, anterior, siguiente, fin,
                           anterior_id=a["audio_id"], siguiente_id=b["audio_id"])
            resumen["hecho"].append(res)
            if res["estado"] == "fallo" and tipo not in ("corta", "identificacion"):
                # Sin noticias, sin patrocinio o sin IA para ese tipo: el hueco no se pierde, entra una corta
                alterno = "corta" if siguiente and "corta" in (reg["tipos_permitidos"] or almacen.TIPOS) else None
                if alterno:
                    resumen["hecho"].append(preparar(c, alterno, anterior, siguiente, fin,
                                                     anterior_id=a["audio_id"], siguiente_id=b["audio_id"]))
            break
        return resumen
    finally:
        c.close()


class Servicio:
    """Hilo de fondo: un `ciclo` cada pocos segundos. Un fallo nunca lo detiene."""

    def __init__(self, get_engine: Callable, db_path: Any = None, intervalo: int = 20):
        self._get_engine, self._db, self._intervalo = get_engine, db_path, intervalo
        self._stop = threading.Event()
        self._hilo: Optional[threading.Thread] = None
        self.ultimo: dict = {"ts": None, "resumen": None, "error": None}

    def start(self):
        if self._hilo and self._hilo.is_alive():
            return
        self._stop.clear()
        self._hilo = threading.Thread(target=self._bucle, name="locutor-ia", daemon=True)
        self._hilo.start()

    def stop(self):
        self._stop.set()

    def _bucle(self):
        while not self._stop.wait(self._intervalo):
            self.una_vez()

    def una_vez(self) -> dict:
        try:
            r = ciclo(self._get_engine(), self._db)
            self.ultimo = {"ts": time.time(), "resumen": r, "error": None}
            for h in r.get("hecho", []):
                if h.get("estado") in ("fallo", "descartada"):
                    log.warning("locutor: %s", h)
        except Exception as e:
            log.exception("locutor: ciclo falló")
            self.ultimo = {"ts": time.time(), "resumen": None, "error": f"{type(e).__name__}: {e}"}
        return self.ultimo
