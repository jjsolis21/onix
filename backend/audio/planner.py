"""
planner.py — Ónix FM
Planificador de relojes (modo automático estilo Jazler SoHo).

QUÉ HACE
    Arma por adelantado lo que va a sonar (por defecto las próximas 2 horas)
    siguiendo los RELOJES de cada hora:

      reloj  = filas en orden. Cada fila pide "un audio de este tipo con estos
               filtros" (p. ej. Música · cat2=ÉXITO, o Efecto · cat1=ID) o un
               audio fijo. Al terminar las filas el reloj vuelve a empezar;
               cada hora nueva empieza en la primera fila de su reloj.
      grilla = qué reloj rige cada hora de cada día (7 × 24). Una hora sin
               reloj usa el "reloj implícito": solo música, sin filtros.

    EVENTOS a hora fija (Jazler "Force Clocks"): un reloj que suena UNA vez
    a una hora exacta (HH:MM) en los días elegidos. Sus filas se ponen en
    orden y después sigue el reloj de la grilla donde iba. Si es "inmediato"
    corta la canción anterior y entra en punto; si no, entra al terminar la
    canción que suene (con compensación, igual que una tanda).

    Las TANDAS (tandas_minutos) se insertan como marcadores con su duración
    estimada. COMPENSACIÓN: si la canción elegida haría llegar la tanda
    tarde (más que tolerancia_tanda_seg), se busca otra cuya duración encaje
    en el tiempo que queda (filtro de compensación del reloj).

REGLAS DE SELECCIÓN (música)
    · separación de artista (ventana_artista_min), contando lo ya planificado
    · no más de max_mismo_genero canciones seguidas con la misma voz
    · la misma canción no se repite dentro del plan
    · no repetir la canción en la misma franja de los días anteriores
      (separacion_cancion_dias / separacion_cancion_ventana_min)
    · orden: la menos tocada primero; aleatoriedad N = al azar entre N
    Si nada cumple, las reglas se relajan en cascada antes de rendirse.

El plan es una lista de dicts (ver _item). El motor la consume en orden; el
operador puede quitar o mover ítems desde el Studio.
"""

import itertools
import json
import logging
import random
from datetime import datetime, timedelta
from typing import Optional

log = logging.getLogger("Planner")

# Filtros de una fila → columna de la tabla audios
FILTRO_COLUMNA = {
    "cat1": "subgenero", "cat2": "categoria", "cat3": "cat3",
    "voz": "genero_vocal", "origen": "origen",
    "ident": "categoria_ident",      # categoría de jingles / sweepers / tips (Admin › Identificadores)
    "epoca": "epoca",                # Época / década (Actual, 2010s, 2000s, 90s, 80s, 70s, Oldies)
}
TIPOS_RELOJ = ("Musica", "Efecto", "Sweeper", "Tips", "Programa")

_uids = itertools.count(1)


def nuevo_uid() -> int:
    return next(_uids)


def duracion_efectiva(track: dict, crossfade: float) -> float:
    """
    Segundos desde que el audio arranca hasta que arranca el siguiente
    (mismo cálculo de punto de mezcla que usa el motor).
    """
    dur = float(track.get("duracion") or 0)
    cue_in = float(track.get("cue_inicio") or 0)
    if dur <= 0:
        return 180.0                      # sin duración conocida: estimación prudente
    mix = float(track.get("cue_mezcla") or track.get("outro") or 0)
    if not (cue_in < mix < dur):
        fade_out = float(track.get("fade_out") or 0)
        lead = fade_out if 0 < fade_out < dur else min(crossfade, dur / 4)
        mix = max(dur - lead, 0.0)
    return max(mix - cue_in, 1.0)


# «Tips» sobre la música (estilo Jazler SoHo): la fila de Tips del reloj no suena sola, se monta encima.
#   intro_inicio → desde el inicio de la canción siguiente
#   intro_final  → termina justo cuando entra la voz de la canción siguiente (intro − duración del tip)
#   puente       → arranca cuando a la canción le queda la mitad del tip (tip de 6 s ⇒ a 3 s del final) y sigue sobre la siguiente
TIP_MODOS = ("intro_inicio", "intro_final", "puente")


def calcular_tip(modo: str, tip_dur: float, cue_in: float, intro: float, fin: float) -> tuple[float, Optional[float]]:
    """
    (disparo, límite) en segundos del archivo de la canción a la que va pegado el tip.
    `límite` = dónde el tip ya tiene que haberse ido (la entrada de la voz); None = sin límite en esta canción.
    `fin` = dónde termina de sonar la canción (su duración o su marca de fin): el puente se mide desde ahí.
    Sin intro marcado el tip va al inicio. Un tip más largo que el intro se recorta con fundido en el límite.
    """
    tip_dur = max(0.0, float(tip_dur or 0))
    hay_intro = intro > cue_in
    if modo == "puente":
        return max(cue_in, fin - tip_dur / 2), None
    if modo == "intro_final" and hay_intro:
        return max(cue_in, intro - tip_dur), intro
    return cue_in, (intro if hay_intro else None)


def _item(tipo: str, inicio: datetime, duracion: float, **kw) -> dict:
    return {"uid": nuevo_uid(), "tipo": tipo, "inicio": inicio.isoformat(timespec="seconds"),
            "duracion": round(duracion, 1), **kw}


class Planner:
    def __init__(self, db_path):
        self.db_path = str(db_path)
        self.cfg = {}
        self.relojes = {}

    def _conn(self):
        from backend.database import get_connection
        return get_connection()

    # ── Carga de relojes y configuración ────────────────────────────────────

    def _cargar(self, conn) -> None:
        self.cfg = {r["clave"]: r["valor"] for r in conn.execute("SELECT clave, valor FROM config")}
        self.relojes = {}
        for r in conn.execute("SELECT * FROM relojes"):
            try:
                comp = json.loads(r["compensacion"] or "{}")
            except ValueError:
                comp = {}
            try:
                resp = json.loads(r["respaldo"] or "{}") if "respaldo" in r.keys() else {}
            except ValueError:
                resp = {}
            self.relojes[r["id"]] = {"id": r["id"], "nombre": r["nombre"], "color": r["color"],
                                     "compensacion": comp if isinstance(comp, dict) else {},
                                     "respaldo": resp if isinstance(resp, dict) else {},
                                     "filas": []}
        for f in conn.execute("SELECT * FROM reloj_filas ORDER BY reloj_id, orden, id"):
            if f["reloj_id"] in self.relojes:
                try:
                    filtros = json.loads(f["filtros"] or "{}")
                except ValueError:
                    filtros = {}
                self.relojes[f["reloj_id"]]["filas"].append({
                    "orden": f["orden"], "tipo_audio": f["tipo_audio"],
                    "filtros": filtros if isinstance(filtros, dict) else {},
                    "audio_id": f["audio_id"],
                    "tip_modo": (f["tip_modo"] if "tip_modo" in f.keys() else "") or "",
                })
        self.grilla = {(g["dia"], g["hora"]): g["reloj_id"]
                       for g in conn.execute("SELECT dia, hora, reloj_id FROM reloj_grilla")}
        # Día específico: manda sobre la grilla semanal (None = solo música)
        try:
            self.dias_especificos = {(d["fecha"], d["hora"]): d["reloj_id"] for d in conn.execute(
                "SELECT fecha, hora, reloj_id FROM reloj_dias")}
        except Exception:       # BD sin la migración 26
            self.dias_especificos = {}
        # Restricciones por canción (si la BD no las tiene aún, no se aplican)
        try:
            self._con_tope = {r[0]: r[1] for r in conn.execute(
                "SELECT id, max_por_dia FROM audios WHERE activo = 1 AND max_por_dia > 0")}
            self._tiene_restricciones = True
        except Exception:
            self._con_tope, self._tiene_restricciones = {}, False
        self._emitidas_dia = {}
        self._repeticion, self._repeticion_ult = {}, {}
        try:
            for r in conn.execute("SELECT id, repeticion_horas, ultima_reproduccion FROM audios"
                                  " WHERE activo = 1 AND repeticion_horas > 0"):
                self._repeticion[r[0]] = r[1]
                try:
                    ult = datetime.fromisoformat(str(r[2]).replace(" ", "T")) if r[2] else None
                except ValueError:
                    ult = None
                self._repeticion_ult[r[0]] = (r[1], ult)
        except Exception:
            pass
        try:
            conn.execute("SELECT 1 FROM pauta_audios LIMIT 1")
            self._tiene_versiones = True
        except Exception:
            self._tiene_versiones = False
        try:
            conn.execute("SELECT prioridad FROM audios LIMIT 1")
            self._tiene_prioridad = True
        except Exception:
            self._tiene_prioridad = False
        try:
            self.eventos = [dict(e) for e in conn.execute(
                "SELECT * FROM reloj_eventos WHERE activo = 1 ORDER BY hora, id")]
        except Exception:       # BD sin la migración 16
            self.eventos = []
        # Playlists programadas: entran a su fecha y hora como un evento cuyas
        # filas son sus audios, en orden (una sola vez)
        self.playlists_prog = []
        try:
            for p in conn.execute("SELECT id, nombre, programada, inmediato FROM playlists"
                                  " WHERE activo = 1 AND COALESCE(programada, '') <> ''"):
                filas = [{"tipo_audio": r[1], "filtros": {}, "audio_id": r[0], "nota": r[2] or ""}
                         for r in conn.execute(
                    "SELECT a.id, a.tipo_audio, i.nota FROM playlist_items i JOIN audios a ON a.id = i.audio_id"
                    " WHERE i.playlist_id = ? ORDER BY i.posicion", (p["id"],))]
                if filas:
                    self.playlists_prog.append({**dict(p), "filas": filas})
        except Exception:       # BD sin la migración 30
            pass

    def generar_lista(self, reloj_id: Optional[int], horas: float, inicio: Optional[datetime] = None) -> list[dict]:
        """
        Playlist (Jazler "crear playlist"): N horas de audios siguiendo el
        patrón del reloj, fila por fila y en vuelta, con las mismas reglas que
        el aire (artista, voz, no repetir, restricciones). Sin tandas ni
        eventos: eso lo pone el motor al aire. Las filas sin audios usan el
        respaldo del reloj o se saltan.
        """
        inicio = inicio or datetime.now()
        conn = self._conn()
        try:
            self._cargar(conn)
            # reloj_id None = "cualquier reloj programado": el de la grilla en cada hora
            fijo = self.relojes.get(reloj_id) if reloj_id else None
            if reloj_id and (not fijo or not fijo["filas"]):
                return []
            crossfade = self._icfg("crossfade_seg", 4)
            fin = inicio + timedelta(hours=horas)
            t, previos, en_plan, out, vacias, i = inicio, [], set(), [], 0, 0
            hora_actual = None
            sin_reloj = {"id": None, "filas": [{"tipo_audio": "Musica", "filtros": {}, "audio_id": None}], "respaldo": {}}
            while t < fin and len(out) < 2000:
                reloj = fijo or self.reloj_de(t) or sin_reloj
                if not reloj["filas"]:
                    reloj = sin_reloj
                if not fijo and (t.date(), t.hour) != hora_actual:
                    hora_actual, i, vacias = (t.date(), t.hour), 0, 0     # cada hora empieza en su fila 1
                if vacias >= len(reloj["filas"]):
                    if fijo or reloj is sin_reloj:
                        break
                    reloj, vacias = sin_reloj, 0
                respaldo = reloj.get("respaldo") or {}
                fila = reloj["filas"][i % len(reloj["filas"])]
                i += 1
                audio = self._elegir(conn, fila, t, previos, en_plan, set())
                if not audio and respaldo and fila["tipo_audio"] == "Musica" and not fila.get("audio_id"):
                    audio = self._elegir(conn, {"tipo_audio": "Musica", "filtros": respaldo, "audio_id": None},
                                         t, previos, en_plan, set())
                if not audio:
                    vacias += 1
                    continue
                vacias = 0
                out.append({"audio_id": audio["id"], "titulo": audio["titulo"], "artista": audio["artista"],
                            "tipo_audio": audio["tipo_audio"], "duracion": audio.get("duracion") or 0,
                            "inicio": t.isoformat(timespec="seconds")})
                if audio["tipo_audio"] == "Musica":
                    en_plan.add(audio["id"])
                previos.append({"audio_id": audio["id"], "artista": audio["artista"],
                                "genero_vocal": audio.get("genero_vocal"), "tipo_audio": audio["tipo_audio"],
                                "ts": t.isoformat(), "plan": True})
                t += timedelta(seconds=duracion_efectiva(audio, crossfade))
            return out
        finally:
            conn.close()

    def _icfg(self, clave: str, defecto: float) -> float:
        if not self.cfg:
            try:
                conn = self._conn()
                self._cargar(conn)
                conn.close()
            except Exception:
                pass
        try:
            return float(self.cfg.get(clave, defecto))
        except (TypeError, ValueError):
            return float(defecto)

    def reloj_de(self, t: datetime) -> Optional[dict]:
        clave = (t.date().isoformat(), t.hour)
        if clave in getattr(self, "dias_especificos", {}):
            rid = self.dias_especificos[clave]
            return self.relojes.get(rid) if rid else None
        rid = self.grilla.get(((t.weekday() + 1) % 7, t.hour))
        return self.relojes.get(rid) if rid else None

    # ── Plan ────────────────────────────────────────────────────────────────

    def extender(self, plan: list[dict], desde: datetime, hasta: datetime,
                 historial: list[dict], tandas_minutos: list[int],
                 excluir: set = frozenset(), tandas_emitidas: set = frozenset(),
                 ultimo: Optional[dict] = None,
                 eventos_emitidos: set = frozenset()) -> list[dict]:
        """
        Devuelve los ítems nuevos que cubren [desde, hasta). `plan` es lo ya
        planificado (cuenta para las reglas), `historial` lo que ya sonó:
        [{"audio_id", "artista", "genero_vocal", "ts"}]. `ultimo` es lo que suena
        ahora si el plan está vacío ({"inicio", "reloj_id", "fila"}): sirve para
        seguir el reloj desde la fila siguiente en vez de reiniciarlo.
        `eventos_emitidos` son las claves (evento_key) de eventos ya al aire.
        """
        conn = self._conn()
        try:
            self._cargar(conn)
            return self._extender(conn, plan, desde, hasta, historial, tandas_minutos,
                                  excluir, set(tandas_emitidas), ultimo, set(eventos_emitidos))
        finally:
            conn.close()

    def _extender(self, conn, plan, desde, hasta, historial, tandas_minutos, excluir,
                  tandas_emitidas, ultimo=None, eventos_emitidos=frozenset()):
        crossfade = self._icfg("crossfade_seg", 4)
        tol = self._icfg("tolerancia_tanda_seg", 90)
        from backend.audio.audio_engine import parse_minutos
        inm = (self.cfg.get("tandas_inmediatas") or "").strip()
        inmediatas = set(parse_minutos(inm)) if inm else set()
        maximo = self._icfg("tanda_max_seg", 240)
        nuevos: list[dict] = []
        # Lo planificado cuenta como "ya sonó" para las reglas
        previos = list(historial) + [
            {"audio_id": p.get("audio_id"), "artista": p.get("artista"),
             "genero_vocal": p.get("genero_vocal"), "tipo_audio": p.get("tipo_audio"),
             "ts": p["inicio"], "plan": True}
            for p in plan if p["tipo"] == "audio"
        ]
        en_plan = {p.get("audio_id") for p in plan if p["tipo"] == "audio"}
        tandas_hechas = {(p["slot"], p["minuto"]) for p in plan if p["tipo"] == "tanda"} | tandas_emitidas
        eventos_hechos = {p["evento_key"] for p in plan if p.get("evento_key")} | set(eventos_emitidos)

        t = desde
        hora_actual, fila_idx, vueltas_vacias = None, 0, 0
        # Seguir el reloj donde iba (si lo último fue de la misma hora y reloj)
        previo = next((p for p in reversed(plan) if p["tipo"] == "audio"), None) or ultimo
        if previo and previo.get("fila") is not None and previo.get("inicio"):
            ini = datetime.fromisoformat(previo["inicio"])
            reloj_ahora = self.reloj_de(t)
            if ((ini.date(), ini.hour) == (t.date(), t.hour) and reloj_ahora
                    and reloj_ahora["id"] == previo.get("reloj_id")):
                hora_actual, fila_idx = (t.date(), t.hour), int(previo["fila"]) + 1
        sin_musica = 0       # ítems seguidos sin música (protección contra bucles)
        while t < hasta and len(nuevos) < 400:
            # ── Tanda pendiente en esta hora ───────────────────────────────
            tanda = self._proxima_tanda(conn, t, tandas_minutos, tandas_hechas, inmediatas)
            inmediata = bool(tanda and tanda["inmediata"])
            if tanda and (tanda["hora"] <= t or (not inmediata and tanda["hora"] <= t + timedelta(seconds=tol))):
                tandas_hechas.add((tanda["slot"], tanda["minuto"]))
                if tanda["duracion"] > 0:
                    nuevos.append(_item("tanda", t, tanda["duracion"], minuto=tanda["minuto"],
                                        slot=tanda["slot"], cunas=tanda["cunas"],
                                        inmediata=inmediata, cierre=bool(tanda.get("cierre")),
                                        excede=bool(maximo and tanda["duracion"] > maximo),
                                        titulo=f"TANDA :{tanda['minuto']:02d}"))
                    t += timedelta(seconds=tanda["duracion"])
                continue

            # ── Evento a hora fija ─────────────────────────────────────────
            evento = self._proximo_evento(t, desde, eventos_hechos)
            if evento and (evento["hora"] <= t or (not evento["inmediato"]
                                                   and evento["hora"] <= t + timedelta(seconds=tol))):
                eventos_hechos.add(evento["key"])
                t = self._poner_evento(conn, evento, t, nuevos, previos, en_plan, excluir, crossfade)
                continue

            # Próximo hito a hora fija (tanda o evento): a él apuntan la
            # compensación y el corte de la canción si es inmediato
            # Solo una tanda «inmediata» corta la canción. Una «al cierre» sin inmediata espera a que la canción termine
            # (puede pasarse unos segundos de su minuto): cortar música la decide el operador marcando «inmediata».
            hitos = [(tanda["hora"], inmediata)] if tanda else []
            if evento:
                hitos.append((evento["hora"], evento["inmediato"]))
            hito_hora, hito_inm = min(hitos, key=lambda h: h[0]) if hitos else (None, False)

            # ── Reloj de la hora (cada hora empieza en su primera fila) ────
            if (t.date(), t.hour) != hora_actual:
                hora_actual, fila_idx = (t.date(), t.hour), 0
            reloj = self.reloj_de(t)
            filas = reloj["filas"] if reloj and reloj["filas"] else [
                {"orden": 0, "tipo_audio": "Musica", "filtros": {}, "audio_id": None}]
            fila = filas[fila_idx % len(filas)]
            fila_idx += 1

            audio = self._elegir(conn, fila, t, previos, en_plan, excluir)
            respaldo = (reloj or {}).get("respaldo") or {}
            if not audio and respaldo and fila["tipo_audio"] == "Musica" and not fila.get("audio_id"):
                # La fila no consigue canción: el respaldo del reloj en vez de saltarla
                audio = self._elegir(conn, {"tipo_audio": "Musica", "filtros": respaldo, "audio_id": None},
                                     t, previos, en_plan, excluir)
            compensado = False
            if audio and hito_hora and fila["tipo_audio"] == "Musica":
                restante = (hito_hora - t).total_seconds()
                dur = duracion_efectiva(audio, crossfade)
                if dur > restante + tol:
                    alt = self._compensar(conn, reloj, restante, tol, crossfade, t,
                                          previos, en_plan, excluir)
                    if alt:
                        audio, compensado = alt, True

            if not audio:
                vueltas_vacias += 1
                if vueltas_vacias >= len(filas):
                    # Ninguna fila del reloj tiene audios: respaldo y, si no, cualquier música
                    audio = (respaldo and self._elegir(conn, {"tipo_audio": "Musica", "filtros": respaldo,
                                                              "audio_id": None}, t, previos, en_plan, excluir)) \
                        or self._elegir(conn, {"tipo_audio": "Musica", "filtros": {}, "audio_id": None},
                                        t, previos, en_plan, excluir)
                    if not audio:
                        log.warning("[Planner] Sin audios para planificar a las %s", t.strftime("%H:%M"))
                        break
                else:
                    continue
            vueltas_vacias = 0

            sin_musica = 0 if audio["tipo_audio"] == "Musica" else sin_musica + 1
            if sin_musica > max(4, len(filas)):
                log.warning("[Planner] El reloj no consigue música: se corta el plan en %s", t.strftime("%H:%M"))
                break
            if audio["tipo_audio"] == "Tips" and fila.get("tip_modo") in TIP_MODOS:
                # Tip sobre la música: no ocupa tiempo propio, el motor lo monta sobre la canción (ver calcular_tip)
                nuevos.append(_item(
                    "audio", t, 0, audio_id=audio["id"], titulo=audio["titulo"], artista=audio["artista"],
                    tipo_audio="Tips", tip_modo=fila["tip_modo"], duracion_total=audio.get("duracion") or 0,
                    reloj_id=reloj["id"] if reloj else None, reloj=reloj["nombre"] if reloj else None,
                    color=reloj["color"] if reloj else None,
                    fila=(fila_idx - 1) % len(filas) if reloj else None, compensacion=False, cortada=False,
                ))
                previos.append({"audio_id": audio["id"], "artista": audio["artista"], "genero_vocal": None,
                                "tipo_audio": "Tips", "ts": t.isoformat(), "plan": True})
                continue
            dur = duracion_efectiva(audio, crossfade)
            cortada = False
            if hito_inm and t + timedelta(seconds=dur) > hito_hora:
                # Tanda o evento con inicio inmediato: esta canción se corta en su minuto
                dur, cortada = max((hito_hora - t).total_seconds(), 1.0), True
            nuevos.append(_item(
                "audio", t, dur, audio_id=audio["id"], titulo=audio["titulo"],
                artista=audio["artista"], tipo_audio=audio["tipo_audio"],
                genero_vocal=audio.get("genero_vocal"), duracion_total=audio.get("duracion") or 0,
                reloj_id=reloj["id"] if reloj else None,
                reloj=reloj["nombre"] if reloj else None,
                color=reloj["color"] if reloj else None,
                fila=(fila_idx - 1) % len(filas) if reloj else None, compensacion=compensado,
                cortada=cortada,
            ))
            if audio["tipo_audio"] == "Musica":
                en_plan.add(audio["id"])
            previos.append({"audio_id": audio["id"], "artista": audio["artista"],
                            "genero_vocal": audio.get("genero_vocal"),
                            "tipo_audio": audio["tipo_audio"], "ts": t.isoformat(), "plan": True})
            t += timedelta(seconds=dur)
        return nuevos

    # ── Eventos a hora fija ─────────────────────────────────────────────────

    ATRASO_EVENTO = timedelta(minutes=15)    # un evento atrasado aún se pone

    def _proximo_evento(self, t: datetime, desde: datetime, hechos: set) -> Optional[dict]:
        """El evento más próximo (hoy o mañana) que aún no se planificó ni sonó."""
        mejor = None
        for d in (t.date(), t.date() + timedelta(days=1)):
            dia, fecha = (d.weekday() + 1) % 7, d.isoformat()
            for e in self.eventos:
                if str(dia) not in str(e["dias"] or "").split(","):
                    continue
                if (e["fecha_inicio"] and fecha < e["fecha_inicio"]) or (e["fecha_fin"] and fecha > e["fecha_fin"]):
                    continue
                reloj = self.relojes.get(e["reloj_id"])
                if not reloj or not reloj["filas"]:
                    continue
                try:
                    hh, mm = (int(x) for x in str(e["hora"]).split(":")[:2])
                except ValueError:
                    continue
                hora = datetime.combine(d, datetime.min.time()).replace(hour=hh, minute=mm)
                key = f"{fecha} {hh:02d}:{mm:02d}#{e['id']}"
                if key in hechos or hora < desde - self.ATRASO_EVENTO:
                    continue
                fin = None
                hasta = (e["hasta"] if "hasta" in e.keys() else "") or ""
                if hasta:
                    try:
                        h2, m2 = (int(x) for x in hasta.split(":")[:2])
                        fin = hora.replace(hour=h2, minute=m2)
                        if fin <= hora:                  # pasa la medianoche
                            fin += timedelta(days=1)
                    except ValueError:
                        fin = None
                if mejor is None or hora < mejor["hora"]:
                    mejor = {"hora": hora, "key": key, "id": e["id"], "nombre": e["nombre"],
                             "inmediato": bool(e["inmediato"]), "reloj": reloj, "fin": fin}
        for p in getattr(self, "playlists_prog", []):
            try:
                hora = datetime.fromisoformat(p["programada"])
            except ValueError:
                continue
            key = f"{p['programada']}#pl{p['id']}"
            if key in hechos or hora < desde - self.ATRASO_EVENTO or hora.date() > t.date() + timedelta(days=1):
                continue
            if mejor is None or hora < mejor["hora"]:
                mejor = {"hora": hora, "key": key, "id": None, "nombre": f"Playlist {p['nombre']}",
                         "inmediato": bool(p["inmediato"]), "fin": None,
                         "reloj": {"id": None, "nombre": p["nombre"], "color": "#9d4edd", "filas": p["filas"]}}
        return mejor

    def _poner_evento(self, conn, ev, t, nuevos, previos, en_plan, excluir, crossfade) -> datetime:
        """
        Pone las filas del reloj del evento en orden: una vuelta, o con "dura
        hasta" (ev["fin"]) las repite hasta esa hora. La última canción se
        busca para que termine cerca de la hora de fin (± tolerancia); si no
        hay ninguna que encaje, termina antes si falta menos de media canción
        o, si no, deja la que toca (se pasa menos de media canción).
        """
        reloj, primero = ev["reloj"], True
        fin = ev.get("fin")
        tol = self._icfg("tolerancia_tanda_seg", 90)
        filas = reloj["filas"]
        idx, vacias = 0, 0
        while True:
            if fin is None and idx >= len(filas):
                break
            if fin is not None and (t >= fin - timedelta(seconds=min(tol, 30)) or vacias >= len(filas)):
                break
            fila = filas[idx % len(filas)]
            idx += 1
            audio = self._elegir(conn, fila, t, previos, en_plan, excluir)
            if audio and fin is not None:
                restante = (fin - t).total_seconds()
                if duracion_efectiva(audio, crossfade) > restante + tol and fila["tipo_audio"] == "Musica":
                    lo, hi = max(1, restante - tol + crossfade), restante + tol + crossfade
                    alt = (self._elegir(conn, fila, t, previos, en_plan, excluir, dur_objetivo=(lo, hi))
                           or self._elegir(conn, {**fila, "filtros": {}}, t, previos, en_plan, excluir,
                                           dur_objetivo=(lo, hi)))
                    if alt:
                        audio = alt
                    elif restante < duracion_efectiva(audio, crossfade) / 2:
                        break      # ninguna encaja y falta poco: termina antes en vez de pasarse
            if not audio:
                vacias += 1
                continue
            vacias = 0
            dur = duracion_efectiva(audio, crossfade)
            nuevos.append(_item(
                "audio", t, dur, audio_id=audio["id"], titulo=audio["titulo"],
                artista=audio["artista"], tipo_audio=audio["tipo_audio"],
                genero_vocal=audio.get("genero_vocal"), duracion_total=audio.get("duracion") or 0,
                reloj_id=reloj["id"], reloj=reloj["nombre"], color=reloj["color"],
                fila=None, compensacion=False, cortada=False,
                evento=ev["nombre"], evento_key=ev["key"], nota=fila.get("nota") or "",
                # Solo el primero lleva la hora fija (el motor corta en punto si es inmediato)
                evento_hora=ev["hora"].isoformat(timespec="seconds") if primero else None,
                inmediato=ev["inmediato"] if primero else False,
            ))
            primero = False
            if audio["tipo_audio"] == "Musica":
                en_plan.add(audio["id"])
            previos.append({"audio_id": audio["id"], "artista": audio["artista"],
                            "genero_vocal": audio.get("genero_vocal"),
                            "tipo_audio": audio["tipo_audio"], "ts": t.isoformat(), "plan": True})
            t += timedelta(seconds=dur)
        if primero:
            log.warning("[Planner] Evento '%s': su reloj no tiene audios disponibles", ev["nombre"])
        return t

    # ── Tandas ──────────────────────────────────────────────────────────────

    def _proxima_tanda(self, conn, t: datetime, minutos: list[int], hechas: set,
                       inmediatas: set = frozenset()) -> Optional[dict]:
        """
        Próxima tanda (de esta hora o la siguiente) aún no planificada.
        Las tandas "al cierre" (config tandas_cierre) terminan en su minuto:
        su hora de arranque depende de cuánto duran sus cuñas. Con grilla por
        día y hora (config tandas_grilla) cada hora usa sus propios minutos.
        """
        from backend.audio.audio_engine import cargar_grilla, inicio_tanda, parse_minutos, tandas_de_hora
        fila = conn.execute("SELECT valor FROM config WHERE clave = 'tandas_cierre'").fetchone()
        cierre_base = parse_minutos(fila[0]) if fila and (fila[0] or "").strip() else []
        grilla = cargar_grilla(self.cfg.get("tandas_grilla", ""))
        hora_t = t.replace(minute=0, second=0, microsecond=0)
        for base in (hora_t - timedelta(hours=1), hora_t, hora_t + timedelta(hours=1)):
            previa = base < hora_t               # solo una tanda «al cierre» que se quedó esperando a la canción
            slot = base.strftime("%Y-%m-%d-%H")
            minutos_h, inm_h, cie_h = tandas_de_hora(grilla, (base.weekday() + 1) % 7, base.hour,
                                                     minutos, sorted(inmediatas), cierre_base)
            cierre, inm_h = set(cie_h), set(inm_h)
            candidatas = []
            for m in minutos_h:
                if (slot, m) in hechas:
                    continue
                cunas, dur = self._cunas_tanda(conn, base, m, minutos_h)
                hora = base + timedelta(seconds=inicio_tanda(m, dur, m in cierre))
                if hora < t - timedelta(minutes=30):
                    continue
                if previa and not (m in cierre and dur > 0 and hora <= t
                                   and (t - hora).total_seconds() <= dur + 600):
                    continue
                candidatas.append({"hora": hora, "minuto": m, "slot": slot, "cunas": cunas,
                                   "duracion": dur, "cierre": m in cierre, "inmediata": m in inm_h})
            if candidatas:
                return min(candidatas, key=lambda c: c["hora"])
        return None

    def _cunas_tanda(self, conn, base: datetime, minuto: int, minutos: list[int]):
        """
        Cuántas cuñas y cuántos segundos tendrá la tanda, con el mismo reparto
        que el motor: comerciales repartidas entre las tandas de la hora; ID y
        cortinas en cada tanda (las opcionales solo si hay comerciales).
        """
        lista = self.lista_tanda(conn, base, minuto, minutos)
        return len(lista), float(sum(p["dur"] for p in lista))

    @staticmethod
    def _hora_en_dayparting(hora: int, rubro_str: str) -> bool:
        """
        Determina si la hora dada corresponde al bloque horario recomendado para el rubro
        (Gastronomía, Entretenimiento, Comercio general/Oficina).
        """
        r = str(rubro_str or "").strip().upper()
        # Gastronomía (Restaurantes, Abastos, Cafés, Comida): 6:00-9:00 AM y 11:00 AM-2:00 PM (14:00)
        if any(w in r for w in ("RESTAURAN", "GASTRONOM", "ABASTO", "CAFE", "COMIDA", "PANADERIA", "ALIMENT")):
            return (6 <= hora <= 9) or (11 <= hora <= 14)
        # Entretenimiento (Licorerías, Discotecas, Bares, Fiestas): 6:00 PM (18:00) - 2:00 AM
        if any(w in r for w in ("DISCO", "LICOR", "BAR", "FIESTA", "ENTRETENIMIENTO", "NOCHE", "EVENTO")):
            return (hora >= 18) or (hora <= 2)
        # Comercio general (Ferreterías, Farmacias, Tiendas, Servicios): 8:00 AM - 6:00 PM (18:00)
        return 8 <= hora <= 18

    def lista_tanda(self, conn, base: datetime, minuto: int, minutos: list[int], simular: bool = False) -> list[dict]:
        """Las cuñas de la tanda, en el orden en que saldrán (misma cuenta que el motor)."""
        from backend.audio.audio_engine import (aplicar_ajustes, fijar_conflictos_rubros, fijar_regla_voces,
                                                leer_ajustes, leer_conflictos_rubros, planificar_tandas, version_rotada)
        fijar_conflictos_rubros(leer_conflictos_rubros(conn))
        fila_voz = conn.execute("SELECT valor FROM config WHERE clave = 'regla_voces_mujer'").fetchone()
        fijar_regla_voces((fila_voz[0] if fila_voz else "1") not in ("0", "false", ""))

        # Espaciado mínimo configurable entre menciones de la misma cuña (defecto 2 horas)
        fila_esp = conn.execute("SELECT valor FROM config WHERE clave = 'spacing_menciones_horas'").fetchone()
        min_spacing_horas = float(fila_esp[0]) if fila_esp and fila_esp[0] else 2.0

        key = f"{(base.weekday() + 1) % 7}-{base.hour}"
        dia = base.date().isoformat()
        slot_actual = base.strftime("%Y-%m-%d-%H")

        secciones = {r[0]: (bool(r[1]), bool(r[2])) for r in conn.execute(
            "SELECT numero, opcional, comercial FROM tanda_secciones")}

        voz_de = {r[0]: r[1] for r in conn.execute(
            "SELECT id, genero_vocal FROM audios WHERE id IN (SELECT audio_id FROM pauta_audios)")}
        versiones: dict[int, list[tuple[int, float]]] = {}
        for pa in conn.execute(
                "SELECT pa.pauta_id, a.id, a.duracion FROM pauta_audios pa"
                " JOIN audios a ON a.id = pa.audio_id AND a.activo = 1 ORDER BY pa.pauta_id, pa.orden"):
            versiones.setdefault(pa[0], []).append((pa[1], pa[2] or 30))
        salidas = dict(conn.execute(
            "SELECT pauta_id, COUNT(*) FROM historial_emision WHERE pauta_id IS NOT NULL GROUP BY pauta_id"
        ).fetchall()) if versiones else {}

        ajustes, quitadas = leer_ajustes(conn, dia, base.hour)

        # Menciones emitidas hoy
        hoy = dict(conn.execute(
            "SELECT pauta_id, COUNT(*) FROM historial_emision WHERE pauta_id IS NOT NULL"
            " AND slot LIKE ? GROUP BY pauta_id", (dia + "-%",)).fetchall())

        # Última emisión de cada pauta hoy (para la regla de Spacing Temporal)
        ultimas_salidas = {}
        for r_h in conn.execute(
            "SELECT pauta_id, MAX(iniciado_en) AS ult FROM historial_emision"
            " WHERE pauta_id IS NOT NULL AND slot LIKE ? GROUP BY pauta_id", (dia + "-%",)):
            try:
                ultimas_salidas[r_h[0]] = datetime.fromisoformat(str(r_h[1]))
            except Exception:
                pass

        # Pautas en deuda hoy no recuperadas (Prioridad Máxima de Recuperación Comercial)
        pautas_deuda_hoy = set()
        try:
            for r_d in conn.execute(
                "SELECT DISTINCT pauta_id FROM pautas_deuda WHERE fecha = ? AND recuperada = 0", (dia,)):
                pautas_deuda_hoy.add(r_d[0])
        except Exception:
            pass

        pautas, fijas = [], []
        for r in conn.execute(
            """
            SELECT p.id, p.rubro, p.matriz, a.duracion, COALESCE(p.seccion, 3) AS seccion,
                   p.menciones_diarias, p.cliente, p.posicion, p.orden, a.titulo,
                   a.genero_vocal AS voz, p.hora_inicio_permitida, p.hora_fin_permitida,
                   COALESCE(p.prioridad, 0) AS prioridad
            FROM pautas p
            JOIN audios a ON a.id = p.audio_id AND a.activo = 1
            WHERE p.activo = 1
              AND (COALESCE(p.fecha_inicio,'') = '' OR p.fecha_inicio <= ?)
              AND (COALESCE(p.fecha_fin,'')    = '' OR p.fecha_fin    >= ?)
            ORDER BY COALESCE(p.prioridad, 0) DESC, p.id ASC
            """, (dia, dia)):

            pid = r["id"]
            if pid in quitadas:
                continue

            # ── 1. Tope de Menciones Diarias ─────────────────────────────────
            menciones_max = r["menciones_diarias"]
            if menciones_max and hoy.get(pid, 0) >= menciones_max:
                continue

            # ── 2. Asignación por Horario (Matriz explícita o Dayparting) ─────
            matriz_raw = r["matriz"] or "{}"
            try:
                matriz = json.loads(matriz_raw) if isinstance(matriz_raw, str) else (matriz_raw or {})
            except ValueError:
                matriz = {}

            tiene_matriz = bool(isinstance(matriz, dict) and any(k for k in matriz.keys() if not k.startswith("__")))
            en_hora = False
            matriz_asignada_exp = False

            if tiene_matriz:
                en_hora = bool(matriz.get(key))
                matriz_asignada_exp = en_hora
            else:
                # Si no tiene matriz manual, aplicar Dayparting automático o rango permitido
                h_ini = r["hora_inicio_permitida"]
                h_fin = r["hora_fin_permitida"]
                if h_ini is not None and h_fin is not None:
                    if h_ini <= h_fin:
                        en_hora = (h_ini <= base.hour <= h_fin)
                    else:  # cruza medianoche (ej. 20 a 4)
                        en_hora = (base.hour >= h_ini or base.hour <= h_fin)
                else:
                    # Sin matriz y sin rango de horas: la pauta no tiene horario, no suena (decisión del dueño).
                    # Antes se le inventaba uno según el rubro; un cliente de una emisora comercial paga por un horario concreto.
                    en_hora = False

            # ── 3. Pautas en Deuda: entran prioritariamente aunque la hora no coincida ──
            es_deuda = (pid in pautas_deuda_hoy)
            if not en_hora and not es_deuda:
                continue

            # ── 4. Distribución Inteligente de Menciones Diarias (Spacing) ─────
            # Reglas profesionales:
            # a) Si está asignada en la matriz explícita (contrato horario específico): NUNCA se bloquea.
            # b) Si es una pauta en deuda: NUNCA se bloquea por espaciado (recuperación prioritaria).
            # c) En simulación / vista previa: NUNCA se bloquea para ver la oferta completa.
            # d) Para pautas auto-asignadas: el espaciado es dinámico = (ventana_horas / menciones_max) * 0.75.
            #    Ejemplo: 10 menciones en 12h -> spacing ~54min (permite salir cada hora sin atascarse).
            if not simular and not matriz_asignada_exp and not es_deuda:
                ult_dt = ultimas_salidas.get(pid)
                if ult_dt:
                    h_ini = r["hora_inicio_permitida"]
                    h_fin = r["hora_fin_permitida"]
                    if h_ini is not None and h_fin is not None:
                        vent_h = (h_fin - h_ini) if h_fin >= h_ini else (24 - h_ini + h_fin)
                    else:
                        vent_h = 16  # Ventana diurna habitual (ej. 6:00 a 22:00)

                    if menciones_max and menciones_max > 0:
                        spacing_seg = max(1800, (vent_h * 3600.0 / menciones_max) * 0.75)
                    else:
                        spacing_seg = min_spacing_horas * 3600.0

                    if (base - ult_dt).total_seconds() < (spacing_seg - 300):
                        continue

            opcional, comercial = secciones.get(r["seccion"], (False, True))
            dur = r["duracion"] or 30
            voz = r["voz"]
            if pid in versiones:
                ids = [v[0] for v in versiones[pid]]
                toca = version_rotada(ids, salidas.get(pid, 0))
                dur = dict(versiones[pid]).get(toca, dur)
                voz = voz_de.get(toca, voz)

            # Si es deuda, subimos su prioridad efectiva para que el despachador la coloque arriba
            prioridad_efectiva = (r["prioridad"] or 0) + (10 if es_deuda else 0)

            p = {
                "pauta_id": pid, "rubro": r["rubro"], "dur": dur, "opcional": opcional,
                "cliente": r["cliente"], "spot": r["titulo"], "seccion": r["seccion"],
                "posicion": r["posicion"], "orden": r["orden"], "comercial": comercial,
                "voz": voz, "prioridad": prioridad_efectiva, "es_deuda": es_deuda,
            }
            (pautas if comercial else fijas).append(p)

        plan = planificar_tandas(pautas, sorted(minutos)) if pautas else {}
        if ajustes and plan:
            por_id = {p["pauta_id"]: p for p in pautas}
            ids = aplicar_ajustes({m: [p["pauta_id"] for p in lst] for m, lst in plan.items()}, ajustes)
            plan = {m: [por_id[i] for i in lst] for m, lst in ids.items()}
        fijadas = set(ajustes.get(minuto, []))
        lista = [{**p, "fijada": p["pauta_id"] in fijadas} for p in plan.get(minuto, [])]
        lista += [f for f in fijas if lista or not f["opcional"]]
        return sorted(lista, key=lambda p: p["seccion"] or 0)


    # ── Selección ───────────────────────────────────────────────────────────

    def _where_filtros(self, filtros: dict) -> tuple[list[str], list]:
        clauses, params = [], []
        for clave, col in FILTRO_COLUMNA.items():
            valor = filtros.get(clave)
            if valor not in (None, ""):
                if clave == "epoca" and str(valor).strip().upper() in ("TODAS", "TODOS", "ALL"):
                    continue
                clauses.append(f"UPPER(COALESCE(a.{col}, '')) = UPPER(?)")
                params.append(str(valor))
        if filtros.get("energia_min") not in (None, ""):
            clauses.append("COALESCE(a.energia, 0) >= ?")
            params.append(int(filtros["energia_min"]))
        if filtros.get("energia_max") not in (None, ""):
            clauses.append("COALESCE(a.energia, 99) <= ?")
            params.append(int(filtros["energia_max"]))
        # Año de lanzamiento (los 4 primeros dígitos de fecha_lanzamiento). Una canción sin año no entra
        # cuando la fila pide un rango: no se puede saber si lo cumple.
        anio = "CAST(SUBSTR(COALESCE(a.fecha_lanzamiento, ''), 1, 4) AS INTEGER)"
        if filtros.get("anio_min") not in (None, ""):
            clauses.append(f"{anio} >= ?")
            params.append(int(filtros["anio_min"]))
        if filtros.get("anio_max") not in (None, ""):
            clauses.append(f"{anio} BETWEEN 1 AND ?")
            params.append(int(filtros["anio_max"]))
        # Prioridad de la canción: 1 Alta · 2 Normal (también las que no la tienen) · 3 Baja
        if filtros.get("prioridad") not in (None, ""):
            clauses.append("COALESCE(a.prioridad, 2) = ?")
            params.append(int(filtros["prioridad"]))
        return clauses, params

    def _elegir(self, conn, fila: dict, t: datetime, previos: list, en_plan: set,
                excluir: set, dur_objetivo: Optional[tuple] = None) -> Optional[dict]:
        tipo = fila.get("tipo_audio") or "Musica"
        if fila.get("audio_id"):
            row = conn.execute("SELECT * FROM audios WHERE id = ? AND activo = 1",
                               (fila["audio_id"],)).fetchone()
            return dict(row) if row else None

        base = ["a.activo = 1", "a.tipo_audio = ?", "COALESCE(a.ambito, '') != 'cartuchera'"]
        params: list = [tipo]
        if tipo == "Musica":
            base.append("a.id NOT IN (SELECT audio_id FROM pautas WHERE audio_id IS NOT NULL)")
            # Las versiones que rotan de un spot tampoco son música
            if self._tiene_versiones:
                base.append("a.id NOT IN (SELECT audio_id FROM pauta_audios)")
            # Bandeja de entrada: la música sin aprobar no sale al aire sola
            base.append("COALESCE(a.aprobada, 1) = 1")
        # Restricciones de la canción (Jazler): reglas duras, no se relajan
        if self._tiene_restricciones:
            hora, dia = t.hour, (t.weekday() + 1) % 7
            base.append("(a.restr_desde IS NULL OR a.restr_hasta IS NULL OR a.restr_desde = a.restr_hasta OR"
                        " (a.restr_desde < a.restr_hasta AND ? >= a.restr_desde AND ? < a.restr_hasta) OR"
                        " (a.restr_desde > a.restr_hasta AND (? >= a.restr_desde OR ? < a.restr_hasta)))")
            params += [hora, hora, hora, hora]
            base.append("(COALESCE(a.restr_dias, '') = '' OR INSTR(CONCAT(',', a.restr_dias, ','), ?) > 0)")
            params.append(f",{dia},")
            # Tiempo de repetición propio de la canción (Jazler): N horas sin repetir
            if self._repeticion:
                bloqueadas = set()
                for p in previos:
                    h = self._repeticion.get(p.get("audio_id"))
                    if h:
                        try:
                            ts = datetime.fromisoformat(str(p["ts"]).replace(" ", "T"))
                        except ValueError:
                            continue
                        if abs(t - ts) < timedelta(hours=h):
                            bloqueadas.add(p["audio_id"])
                for aid, (h, ult) in self._repeticion_ult.items():
                    if ult and t - ult < timedelta(hours=h):
                        bloqueadas.add(aid)
                if bloqueadas:
                    base.append(f"a.id NOT IN ({','.join('?' * len(bloqueadas))})")
                    params += list(bloqueadas)
            topados = self._topados(conn, t, previos)
            if topados:
                base.append(f"a.id NOT IN ({','.join('?' * len(topados))})")
                params += list(topados)
        fc, fp = self._where_filtros(fila.get("filtros") or {})
        base += fc
        params += fp
        excl_fallidos = [i for i in set(excluir) if isinstance(i, int)]
        if excl_fallidos:
            base.append(f"a.id NOT IN ({','.join('?' * len(excl_fallidos))})")
            params += excl_fallidos
        if dur_objetivo:
            base.append("COALESCE(a.duracion, 0) BETWEEN ? AND ?")
            params += [dur_objetivo[0], dur_objetivo[1]]

        reglas = []
        if tipo == "Musica" and en_plan:
            # Última regla en relajarse: no repetir canciones dentro del plan
            ids = [i for i in en_plan if isinstance(i, int)]
            reglas.append((f"a.id NOT IN ({','.join('?' * len(ids))})", ids))
        if tipo == "Musica":
            ventana = self._icfg("ventana_artista_min", 60)
            limite = t - timedelta(minutes=ventana)
            artistas = {p["artista"] for p in previos
                        if p.get("artista") and p.get("tipo_audio", "Musica") == "Musica"
                        and datetime.fromisoformat(p["ts"]) >= limite}
            if artistas:
                reglas.append((f"a.artista NOT IN ({','.join('?' * len(artistas))})", list(artistas)))
            max_genero = int(self._icfg("max_mismo_genero", 2))
            musica = [p for p in previos if p.get("tipo_audio", "Musica") == "Musica"]
            if max_genero > 0 and len(musica) >= max_genero:
                voces = {p.get("genero_vocal") for p in musica[-max_genero:]}
                if len(voces) == 1 and None not in voces:
                    reglas.append(("COALESCE(a.genero_vocal,'') != ?", [voces.pop()]))
            dias = int(self._icfg("separacion_cancion_dias", 1))
            win = int(self._icfg("separacion_cancion_ventana_min", 60))
            if dias > 0 and win > 0:
                franjas = []
                fparams = []
                for d in range(1, dias + 1):
                    centro = t - timedelta(days=d)
                    franjas.append("h.iniciado_en BETWEEN ? AND ?")
                    fparams += [(centro - timedelta(minutes=win)).strftime("%Y-%m-%d %H:%M:%S"),
                                (centro + timedelta(minutes=win)).strftime("%Y-%m-%d %H:%M:%S")]
                reglas.append((f"a.id NOT IN (SELECT h.audio_id FROM historial_emision h WHERE "
                               f"h.audio_id IS NOT NULL AND ({' OR '.join(franjas)}))", fparams))
        else:
            # Jingles/sweepers: no repetir el último de su tipo
            ultimo = next((p["audio_id"] for p in reversed(previos)
                           if p.get("tipo_audio") == tipo and p.get("audio_id")), None)
            if ultimo:
                reglas.append(("a.id != ?", [ultimo]))

        n = max(1, int(self._icfg("aleatoriedad", 3)))
        orden = "a.ultima_reproduccion ASC, a.id ASC"
        if self._tiene_prioridad:
            # Prioridad (1 Alta · 2 Normal · 3 Baja): una alta cuenta como si
            # hubiera sonado 12 h antes (sale antes); una baja, 12 h después.
            orden = ("(UNIX_TIMESTAMP(COALESCE(a.ultima_reproduccion, '1970-01-01 00:00:00')) / 86400.0)"
                     " + (COALESCE(a.prioridad, 2) - 2) * 0.5 ASC, a.id ASC")
        # Cascada: se sueltan las reglas de la última a la primera; la primera
        # (no repetir dentro del plan) es la que más se sostiene.
        # Con canciones ya planificadas, el paso "sin ninguna regla" lo cubre la
        # vuelta de abajo (repite la más antigua del plan, no la recién puesta).
        ultimo_k = 1 if (tipo == "Musica" and en_plan) else 0
        for k in range(len(reglas), ultimo_k - 1, -1):
            clauses = base + [c for c, _ in reglas[:k]]
            prm = params + [v for _, ps in reglas[:k] for v in ps]
            rows = conn.execute(
                f"SELECT a.* FROM audios a WHERE {' AND '.join(clauses)} ORDER BY {orden} LIMIT ?",
                prm + [n]).fetchall()
            if rows:
                return dict(random.choice(rows))
        if tipo == "Musica" and en_plan:
            # Biblioteca chica: repetir la que hace más tiempo salió en el plan
            ultima_vez = {}
            for idx, p in enumerate(previos):
                if p.get("audio_id") in en_plan:
                    ultima_vez[p["audio_id"]] = idx
            for aid in sorted(ultima_vez, key=ultima_vez.get):    # la que salió hace más
                row = conn.execute(f"SELECT a.* FROM audios a WHERE {' AND '.join(base)} AND a.id = ?",
                                   params + [aid]).fetchone()
                if row:
                    return dict(row)
        return None

    def _topados(self, conn, t: datetime, previos: list) -> set:
        """Canciones con max_por_dia que ya llegaron a su tope ese día (emitidas + planificadas)."""
        con_tope = self._con_tope
        if not con_tope:
            return set()
        dia = t.date().isoformat()
        cuenta: dict = {}
        # Emitidas ese día (historial) + planificadas para ese día (solo las del plan:
        # `previos` también trae historial y se contaría dos veces)
        if dia not in self._emitidas_dia:
            self._emitidas_dia[dia] = {r[0]: r[1] for r in conn.execute(
                "SELECT audio_id, COUNT(*) FROM historial_emision WHERE substr(iniciado_en, 1, 10) = ?"
                " AND audio_id IS NOT NULL GROUP BY audio_id", (dia,))}
        for aid, n in self._emitidas_dia[dia].items():
            if aid in con_tope:
                cuenta[aid] = n
        for p in previos:
            if p.get("plan") and p.get("audio_id") in con_tope and str(p.get("ts", ""))[:10] == dia:
                cuenta[p["audio_id"]] = cuenta.get(p["audio_id"], 0) + 1
        return {aid for aid, n in cuenta.items() if n >= con_tope[aid]}

    def _compensar(self, conn, reloj, restante, tol, crossfade, t, previos, en_plan, excluir):
        """Canción que termina cerca del minuto de la tanda (± tolerancia)."""
        if restante <= 0:
            return None
        filtros = (reloj or {}).get("compensacion") or {}
        fila = {"tipo_audio": "Musica", "filtros": filtros, "audio_id": None}
        # duración total ≈ efectiva + crossfade
        lo, hi = max(1, restante - tol + crossfade), restante + tol + crossfade
        return (self._elegir(conn, fila, t, previos, en_plan, excluir, dur_objetivo=(lo, hi))
                or (self._elegir(conn, {**fila, "filtros": {}}, t, previos, en_plan, excluir,
                                 dur_objetivo=(lo, hi)) if filtros else None))

    def obtener_compensacion_brecha(
        self,
        brecha: float,
        t_inicio: datetime,
        previos: list,
        en_plan: set,
        excluir: set,
        tol: float = 90.0,
        crossfade: float = 4.0,
    ) -> list[dict]:
        """
        Selecciona uno o más audios de la biblioteca para cubrir una brecha de tiempo
        producida por la eliminación manual de pistas o un salto, evitando el desfase
        de la tanda comercial próxima.
        """
        conn = self._conn()
        try:
            self._cargar(conn)
            reloj = self.reloj_de(t_inicio)
            items_comp = []
            restante = float(brecha)
            t_act = t_inicio

            # Iterar mientras la brecha amerite canciones (>= 120s)
            while restante >= 120.0 and len(items_comp) < 4:
                audio = self._compensar(conn, reloj, restante, tol, crossfade, t_act, previos, en_plan, excluir)
                if not audio and restante > 260.0:
                    audio = self._elegir(conn, {"tipo_audio": "Musica", "filtros": {}, "audio_id": None},
                                         t_act, previos, en_plan, excluir, dur_objetivo=(180, 260))
                if not audio:
                    audio = self._elegir(conn, {"tipo_audio": "Musica", "filtros": {}, "audio_id": None},
                                         t_act, previos, en_plan, excluir)
                if not audio:
                    break

                dur = duracion_efectiva(audio, crossfade)
                it = _item(
                    "audio", t_act, dur,
                    audio_id=audio["id"], titulo=audio["titulo"], artista=audio["artista"],
                    tipo_audio=audio["tipo_audio"], genero_vocal=audio.get("genero_vocal"),
                    duracion_total=audio.get("duracion") or 0,
                    reloj_id=reloj["id"] if reloj else None,
                    reloj="Compensación", color="#e67e22", fila=None,
                    compensacion=True, cortada=False, nota="Ajuste por tanda"
                )
                items_comp.append(it)
                en_plan.add(audio["id"])
                previos.append({"audio_id": audio["id"], "artista": audio["artista"],
                                "genero_vocal": audio.get("genero_vocal"),
                                "tipo_audio": audio["tipo_audio"], "ts": t_act.isoformat(), "plan": True})
                t_act += timedelta(seconds=dur)
                restante -= dur

                if abs(restante) <= tol:
                    break

            # Si queda un remanente menor (ej. 45 a 119s), buscar Sweeper o separador
            if restante >= 45.0 and len(items_comp) < 5:
                fila_corta = {"tipo_audio": "Sweeper", "filtros": {}, "audio_id": None}
                audio_corto = self._elegir(conn, fila_corta, t_act, previos, en_plan, excluir,
                                           dur_objetivo=(max(10, int(restante - 30)), int(restante + 30)))
                if not audio_corto:
                    fila_corta = {"tipo_audio": "Tips", "filtros": {}, "audio_id": None}
                    audio_corto = self._elegir(conn, fila_corta, t_act, previos, en_plan, excluir)

                if audio_corto:
                    dur_c = duracion_efectiva(audio_corto, crossfade)
                    it_c = _item(
                        "audio", t_act, dur_c,
                        audio_id=audio_corto["id"], titulo=audio_corto["titulo"], artista=audio_corto["artista"],
                        tipo_audio=audio_corto["tipo_audio"], genero_vocal=audio_corto.get("genero_vocal"),
                        duracion_total=audio_corto.get("duracion") or 0,
                        reloj_id=None, reloj="Separador", color="#f39c12", fila=None,
                        compensacion=True, cortada=False, nota="Ajuste fino"
                    )
                    items_comp.append(it_c)

            return items_comp
        finally:
            conn.close()

    # ── Re-planificación Dinámica ─────────────────────────────────────────────

    def recalcular_cola_dinamica(
        self,
        plan_actual: list[dict],
        historial: list[dict],
        tandas_minutos: list[int],
        tiempo_restante_actual: float,
        excluir: set = frozenset(),
        tandas_emitidas: set = frozenset(),
        eventos_emitidos: set = frozenset(),
    ) -> list[dict]:
        """
        Re-planificación dinámica: recalcula el programa desde el instante en
        que termina lo que suena ahora, preservando las horas exactas de las
        tandas comerciales.

        Cuándo se llama:
          · El operador elimina o mueve una pista en el Studio.
          · El motor detecta aire muerto y salta una canción.
          · Un archivo corrupto se excluye y el motor avanza.

        Args:
            plan_actual:          Ítems pendientes DESPUÉS de lo que suena ahora
                                  (la deque _programa del motor, como lista de dicts).
            historial:            Últimos audios ya emitidos (para respetar ventanas
                                  de artista, separación de canciones, etc.).
            tandas_minutos:       Lista de minutos de la hora donde caen las tandas
                                  ([15, 45], etc.).
            tiempo_restante_actual: Segundos que le quedan a la canción que suena
                                  ahora. El recálculo empieza al finalizar esa canción.
            excluir:              IDs de audios que el motor ya marcó como fallidos.
            tandas_emitidas:      Set de (slot, minuto) de tandas ya al aire o en
                                  el plan que no hay que duplicar.
            eventos_emitidos:     Claves de eventos a hora fija ya emitidos.

        Returns:
            Lista de ítems nuevos (misma estructura que _extender) que reemplaza
            al plan_actual. El motor debe vaciar su _programa y cargar estos.
        """
        conn = self._conn()
        try:
            self._cargar(conn)

            crossfade = self._icfg("crossfade_seg", 4)
            horas_plan = self._icfg("horas_planificadas", 2)

            # ── Punto de partida: cuando termina lo que suena ahora ──────────
            ahora = datetime.now()
            desde = ahora + timedelta(seconds=max(0.0, tiempo_restante_actual))

            # Límite: no calcular más allá de horas_planificadas
            hasta = ahora + timedelta(hours=horas_plan)
            hasta = max(hasta, desde + timedelta(minutes=5))  # mínimo 5 min útiles

            # Solo excluimos las tandas que YA fueron emitidas al aire
            tandas_conocidas = set(tandas_emitidas)

            # ── Analizar cuánto tiempo hay hasta la próxima tanda ───────────
            from backend.audio.audio_engine import parse_minutos
            inm_str = (self.cfg.get("tandas_inmediatas") or "").strip()
            inmediatas = set(parse_minutos(inm_str)) if inm_str else set()

            proxima_tanda = self._proxima_tanda(conn, desde, tandas_minutos, tandas_conocidas, inmediatas)
            tiempo_hasta_tanda = (
                (proxima_tanda["hora"] - desde).total_seconds()
                if proxima_tanda else None
            )

            log.info(
                "[Planner] Recalculando cola: desde=%s hasta=%s | "
                "próxima tanda en %.0fs | ítems en plan actual=%d",
                desde.strftime("%H:%M:%S"),
                hasta.strftime("%H:%M:%S"),
                tiempo_hasta_tanda if tiempo_hasta_tanda is not None else -1,
                len(plan_actual),
            )

            # ── Construir historial enriquecido con el plan actual ───────────
            # Los ítems de audio del plan actual cuentan como "recientes" para
            # las reglas (ventana de artista, no repetir, etc.)
            previos_del_plan = [
                {
                    "audio_id": p.get("audio_id"),
                    "artista":  p.get("artista"),
                    "genero_vocal": p.get("genero_vocal"),
                    "tipo_audio": p.get("tipo_audio"),
                    "ts": p.get("inicio", ahora.isoformat()),
                    "plan": True,
                }
                for p in plan_actual
                if p.get("tipo") == "audio"
            ]
            historial_extendido = list(historial) + previos_del_plan

            # ── Generar el nuevo tramo de programa ───────────────────────────
            # Pasamos plan=[] porque ya convertimos los previos en historial;
            # así _extender calcula desde "desde" con plena libertad.
            nuevos = self._extender(
                conn,
                plan=[],                      # plan vacío: el recálculo parte limpio
                desde=desde,
                hasta=hasta,
                historial=historial_extendido,
                tandas_minutos=tandas_minutos,
                excluir=set(excluir),
                tandas_emitidas=tandas_conocidas,
                ultimo=None,
                eventos_emitidos=set(eventos_emitidos),
            )

            # ── Compensación de hueco si sobró tiempo ─────────────────────────
            # Si eliminaron una canción larga y hay un hueco antes de la próxima
            # tanda, _extender ya lo rellena automáticamente. Pero si el tiempo
            # disponible hasta la tanda es < crossfade, no hay nada que meter:
            # el motor simplemente encadenará las pistas normalmente.
            if not nuevos:
                log.warning(
                    "[Planner] Recálculo no encontró audios para el tramo %s → %s. "
                    "El motor usará el respaldo.",
                    desde.strftime("%H:%M:%S"), hasta.strftime("%H:%M:%S")
                )

            log.info(
                "[Planner] Recalculo completado: %d ítems nuevos (audios=%d, tandas=%d)",
                len(nuevos),
                sum(1 for i in nuevos if i.get("tipo") == "audio"),
                sum(1 for i in nuevos if i.get("tipo") == "tanda"),
            )
            return nuevos

        except Exception as exc:
            log.error("[Planner] Error en recalcular_cola_dinamica: %s", exc, exc_info=True)
            return []
        finally:
            conn.close()

    # ── Simulación de Tandas Comerciales (Preview para el Admin) ──────────────

    def simular_tanda(
        self,
        hora: int,
        fecha: str,
        minuto: Optional[int] = None,
        rubro_filtro: Optional[str] = None,
    ) -> dict:
        """
        Simula la tanda o bloque comercial planificado para una fecha y hora dadas,
        sin emitir nada ni alterar el estado del motor ni de la base de datos.

        Args:
            hora:         Hora en formato 0-23.
            fecha:        Fecha en formato 'YYYY-MM-DD'.
            minuto:       Minuto específico (ej. 15 o 45). Si es None, simula todas
                          las tandas de esa hora y las agrupa.
            rubro_filtro: Opcional: filtrar solo cuñas de un rubro específico.

        Returns:
            Dict con:
              - fecha, hora, minutos_configurados
              - tandas: lista de tandas con sus cuñas ordenadas, duración y choques
              - resumen: total_cunas, duracion_total_segundos, duracion_total_formato,
                         hay_hueco (si excede tanda_max_seg o si la tanda está vacía),
                         excede_maximo, total_deudas
        """
        conn = self._conn()
        try:
            self._cargar(conn)
            from backend.audio.audio_engine import (
                cargar_grilla, parse_minutos, tandas_de_hora, inicio_tanda,
                rubros_chocan, es_voz_mujer, _rubro
            )

            dt_base = datetime.fromisoformat(f"{fecha}T{hora:02d}:00:00")
            dia_semana = (dt_base.weekday() + 1) % 7

            grilla = cargar_grilla(self.cfg.get("tandas_grilla", ""))
            inm_base = parse_minutos(self.cfg.get("tandas_inmediatas", ""))
            cie_base = parse_minutos(self.cfg.get("tandas_cierre", ""))
            min_base = parse_minutos(self.cfg.get("tandas_minutos", "15,45")) or [15, 45]

            minutos_h, inm_h, cie_h = tandas_de_hora(
                grilla, dia_semana, hora, min_base, inm_base, cie_base
            )
            cierre_set = set(cie_h)
            inm_set = set(inm_h)

            tanda_max_seg = int(self.cfg.get("tanda_max_seg", 240) or 240)

            # Si el operador pidió un minuto específico, o simulamos todos los de la hora
            minutos_a_simular = [minuto] if (minuto is not None and minuto in minutos_h) else (minutos_h or [15, 45])

            tandas_resultado = []
            gran_total_cunas = 0
            gran_total_segundos = 0.0
            gran_total_deudas = 0
            hay_alerta_global = False

            for m in minutos_a_simular:
                cunas_raw = self.lista_tanda(conn, dt_base, m, minutos_h, simular=True)

                if rubro_filtro and rubro_filtro.strip():
                    cunas_raw = [c for c in cunas_raw if str(c.get("rubro") or "").strip().upper() == rubro_filtro.strip().upper()]

                cierre = m in cierre_set
                dur_tanda = sum(float(c.get("dur") or 30) for c in cunas_raw)
                seg_arranque = inicio_tanda(m, dur_tanda, cierre)
                t_arranque = dt_base + timedelta(seconds=seg_arranque)

                # Calcular horas estimadas para cada cuña e inspeccionar choques
                t_cursor = t_arranque
                cunas_procesadas = []
                choques_detectados = []

                for idx, c in enumerate(cunas_raw):
                    dur_c = float(c.get("dur") or 30)
                    h_est = t_cursor.strftime("%H:%M:%S")

                    # Validar si choca con la anterior
                    choque = False
                    motivo_choque = ""
                    if idx > 0:
                        ant = cunas_raw[idx - 1]
                        if rubros_chocan(_rubro(ant), _rubro(c)):
                            choque = True
                            motivo_choque = f"Conflicto de Rubro con anterior ({ant.get('rubro')})"
                        elif es_voz_mujer(ant.get("voz")) and es_voz_mujer(c.get("voz")):
                            choque = True
                            motivo_choque = "Doble voz femenina consecutiva"

                    if choque:
                        choques_detectados.append({
                            "posicion": idx + 1,
                            "spot": c.get("spot"),
                            "motivo": motivo_choque
                        })

                    if c.get("es_deuda"):
                        gran_total_deudas += 1

                    cunas_procesadas.append({
                        "orden": idx + 1,
                        "pauta_id": c.get("pauta_id"),
                        "cliente": c.get("cliente", ""),
                        "titulo": c.get("spot", ""),
                        "rubro": c.get("rubro", "") or "GENERAL",
                        "duracion": round(dur_c, 1),
                        "duracion_formato": f"{int(dur_c // 60)}:{int(dur_c % 60):02d}",
                        "voz": c.get("voz") or "Hombre",
                        "hora_estimada": h_est,
                        "seccion": c.get("seccion", 3),
                        "es_deuda": bool(c.get("es_deuda")),
                        "posicion_fija": c.get("posicion", 0),
                        "choque": choque,
                        "motivo_choque": motivo_choque,
                    })

                    t_cursor += timedelta(seconds=dur_c)

                excede = dur_tanda > tanda_max_seg
                if excede or choques_detectados:
                    hay_alerta_global = True

                gran_total_cunas += len(cunas_procesadas)
                gran_total_segundos += dur_tanda

                min_dur = int(dur_tanda // 60)
                sec_dur = int(dur_tanda % 60)

                tandas_resultado.append({
                    "minuto": m,
                    "etiqueta": f"Tanda :{m:02d}",
                    "hora_inicio_estimada": t_arranque.strftime("%H:%M:%S"),
                    "cierre": cierre,
                    "inmediata": m in inm_set,
                    "duracion_total_segundos": round(dur_tanda, 1),
                    "duracion_total_formato": f"{min_dur}:{sec_dur:02d}",
                    "excede_tiempo": excede,
                    "tiempo_maximo_permitido": tanda_max_seg,
                    "total_cunas": len(cunas_procesadas),
                    "choques": choques_detectados,
                    "cunas": cunas_procesadas,
                })

            total_min = int(gran_total_segundos // 60)
            total_sec = int(gran_total_segundos % 60)

            return {
                "ok": True,
                "fecha": fecha,
                "hora": hora,
                "minutos_configurados": minutos_h,
                "tandas": tandas_resultado,
                "resumen": {
                    "total_tandas": len(tandas_resultado),
                    "total_cunas": gran_total_cunas,
                    "duracion_total_segundos": round(gran_total_segundos, 1),
                    "duracion_total_formato": f"{total_min}:{total_sec:02d}",
                    "hay_hueco": (gran_total_cunas == 0 or gran_total_segundos > (tanda_max_seg * len(tandas_resultado))),
                    "excede_maximo": any(t["excede_tiempo"] for t in tandas_resultado),
                    "total_deudas": gran_total_deudas,
                    "alertas": hay_alerta_global,
                }
            }

        except Exception as exc:
            log.error("[Planner] Error en simular_tanda: %s", exc, exc_info=True)
            return {
                "ok": False,
                "error": str(exc),
                "fecha": fecha,
                "hora": hora,
                "tandas": [],
                "resumen": {
                    "total_tandas": 0, "total_cunas": 0,
                    "duracion_total_segundos": 0.0, "duracion_total_formato": "0:00",
                    "hay_hueco": True, "excede_maximo": False, "total_deudas": 0, "alertas": True
                }
            }
        finally:
            conn.close()
