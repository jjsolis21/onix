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
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

log = logging.getLogger("Planner")

# Filtros de una fila → columna de la tabla audios
FILTRO_COLUMNA = {
    "cat1": "subgenero", "cat2": "categoria", "cat3": "cat3",
    "voz": "genero_vocal", "origen": "origen",
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


def _item(tipo: str, inicio: datetime, duracion: float, **kw) -> dict:
    return {"uid": nuevo_uid(), "tipo": tipo, "inicio": inicio.isoformat(timespec="seconds"),
            "duracion": round(duracion, 1), **kw}


class Planner:
    def __init__(self, db_path):
        self.db_path = str(db_path)

    def _conn(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    # ── Carga de relojes y configuración ────────────────────────────────────

    def _cargar(self, conn) -> None:
        self.cfg = {r["clave"]: r["valor"] for r in conn.execute("SELECT clave, valor FROM config")}
        self.relojes = {}
        for r in conn.execute("SELECT * FROM relojes"):
            try:
                comp = json.loads(r["compensacion"] or "{}")
            except ValueError:
                comp = {}
            self.relojes[r["id"]] = {"id": r["id"], "nombre": r["nombre"], "color": r["color"],
                                     "compensacion": comp if isinstance(comp, dict) else {},
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
                })
        self.grilla = {(g["dia"], g["hora"]): g["reloj_id"]
                       for g in conn.execute("SELECT dia, hora, reloj_id FROM reloj_grilla")}
        # Restricciones por canción (si la BD no las tiene aún, no se aplican)
        try:
            self._con_tope = {r[0]: r[1] for r in conn.execute(
                "SELECT id, max_por_dia FROM audios WHERE activo = 1 AND max_por_dia > 0")}
            self._tiene_restricciones = True
        except sqlite3.OperationalError:
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
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("SELECT prioridad FROM audios LIMIT 1")
            self._tiene_prioridad = True
        except sqlite3.OperationalError:
            self._tiene_prioridad = False
        try:
            self.eventos = [dict(e) for e in conn.execute(
                "SELECT * FROM reloj_eventos WHERE activo = 1 ORDER BY hora, id")]
        except sqlite3.OperationalError:       # BD sin la migración 16
            self.eventos = []

    def _icfg(self, clave: str, defecto: float) -> float:
        try:
            return float(self.cfg.get(clave, defecto))
        except (TypeError, ValueError):
            return float(defecto)

    def reloj_de(self, t: datetime) -> Optional[dict]:
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
            tanda = self._proxima_tanda(conn, t, tandas_minutos, tandas_hechas)
            inmediata = bool(tanda and tanda["minuto"] in inmediatas)
            if tanda and (tanda["hora"] <= t or (not inmediata and tanda["hora"] <= t + timedelta(seconds=tol))):
                tandas_hechas.add((tanda["slot"], tanda["minuto"]))
                if tanda["duracion"] > 0:
                    nuevos.append(_item("tanda", t, tanda["duracion"], minuto=tanda["minuto"],
                                        slot=tanda["slot"], cunas=tanda["cunas"],
                                        inmediata=inmediata,
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
                    # Ninguna fila del reloj tiene audios: reloj implícito
                    audio = self._elegir(conn, {"tipo_audio": "Musica", "filtros": {}, "audio_id": None},
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
                if mejor is None or hora < mejor["hora"]:
                    mejor = {"hora": hora, "key": key, "id": e["id"], "nombre": e["nombre"],
                             "inmediato": bool(e["inmediato"]), "reloj": reloj}
        return mejor

    def _poner_evento(self, conn, ev, t, nuevos, previos, en_plan, excluir, crossfade) -> datetime:
        """Pone las filas del reloj del evento una sola vez, en orden."""
        reloj, primero = ev["reloj"], True
        for i, fila in enumerate(reloj["filas"]):
            audio = self._elegir(conn, fila, t, previos, en_plan, excluir)
            if not audio:
                continue
            dur = duracion_efectiva(audio, crossfade)
            nuevos.append(_item(
                "audio", t, dur, audio_id=audio["id"], titulo=audio["titulo"],
                artista=audio["artista"], tipo_audio=audio["tipo_audio"],
                genero_vocal=audio.get("genero_vocal"), duracion_total=audio.get("duracion") or 0,
                reloj_id=reloj["id"], reloj=reloj["nombre"], color=reloj["color"],
                fila=None, compensacion=False, cortada=False,
                evento=ev["nombre"], evento_key=ev["key"],
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

    def _proxima_tanda(self, conn, t: datetime, minutos: list[int], hechas: set) -> Optional[dict]:
        """
        Próxima tanda (de esta hora o la siguiente) aún no planificada.
        Las tandas "al cierre" (config tandas_cierre) terminan en su minuto:
        su hora de arranque depende de cuánto duran sus cuñas.
        """
        from backend.audio.audio_engine import inicio_tanda, parse_minutos
        fila = conn.execute("SELECT valor FROM config WHERE clave = 'tandas_cierre'").fetchone()
        cierre = set(parse_minutos(fila[0])) if fila and (fila[0] or "").strip() else set()
        for base in (t.replace(minute=0, second=0, microsecond=0),
                     t.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)):
            slot = base.strftime("%Y-%m-%d-%H")
            candidatas = []
            for m in minutos:
                if (slot, m) in hechas:
                    continue
                cunas, dur = self._cunas_tanda(conn, base, m, minutos)
                hora = base + timedelta(seconds=inicio_tanda(m, dur, m in cierre))
                if hora < t - timedelta(minutes=30):
                    continue
                candidatas.append({"hora": hora, "minuto": m, "slot": slot, "cunas": cunas,
                                   "duracion": dur, "cierre": m in cierre})
            if candidatas:
                return min(candidatas, key=lambda c: c["hora"])
        return None

    def _cunas_tanda(self, conn, base: datetime, minuto: int, minutos: list[int]):
        """
        Cuántas cuñas y cuántos segundos tendrá la tanda, con el mismo reparto
        que el motor: comerciales repartidas entre las tandas de la hora; ID y
        cortinas en cada tanda (las opcionales solo si hay comerciales).
        """
        from backend.audio.audio_engine import planificar_tandas
        key = f"{(base.weekday() + 1) % 7}-{base.hour}"
        dia = base.date().isoformat()
        secciones = {r[0]: (bool(r[1]), bool(r[2])) for r in conn.execute(
            "SELECT numero, opcional, comercial FROM tanda_secciones")}
        pautas, fijas = [], []
        for r in conn.execute(
            """
            SELECT p.id, p.rubro, p.matriz, a.duracion, COALESCE(p.seccion, 3) AS seccion FROM pautas p
            JOIN audios a ON a.id = p.audio_id AND a.activo = 1
            WHERE p.activo = 1
              AND (COALESCE(p.fecha_inicio,'') = '' OR p.fecha_inicio <= ?)
              AND (COALESCE(p.fecha_fin,'')    = '' OR p.fecha_fin    >= ?)
            ORDER BY COALESCE(p.prioridad, 0) DESC, p.id ASC
            """, (dia, dia)):
            try:
                matriz = json.loads(r["matriz"] or "{}")
            except ValueError:
                continue
            if isinstance(matriz, dict) and matriz.get(key):
                opcional, comercial = secciones.get(r["seccion"], (False, True))
                p = {"pauta_id": r["id"], "rubro": r["rubro"], "dur": r["duracion"] or 30,
                     "opcional": opcional}
                (pautas if comercial else fijas).append(p)
        lista = planificar_tandas(pautas, sorted(minutos)).get(minuto, []) if pautas else []
        lista += [f for f in fijas if lista or not f["opcional"]]
        return len(lista), float(sum(p["dur"] for p in lista))

    # ── Selección ───────────────────────────────────────────────────────────

    def _where_filtros(self, filtros: dict) -> tuple[list[str], list]:
        clauses, params = [], []
        for clave, col in FILTRO_COLUMNA.items():
            valor = filtros.get(clave)
            if valor not in (None, ""):
                clauses.append(f"UPPER(COALESCE(a.{col}, '')) = UPPER(?)")
                params.append(str(valor))
        if filtros.get("energia_min") not in (None, ""):
            clauses.append("COALESCE(a.energia, 0) >= ?")
            params.append(int(filtros["energia_min"]))
        if filtros.get("energia_max") not in (None, ""):
            clauses.append("COALESCE(a.energia, 99) <= ?")
            params.append(int(filtros["energia_max"]))
        return clauses, params

    def _elegir(self, conn, fila: dict, t: datetime, previos: list, en_plan: set,
                excluir: set, dur_objetivo: Optional[tuple] = None) -> Optional[dict]:
        tipo = fila.get("tipo_audio") or "Musica"
        if fila.get("audio_id"):
            row = conn.execute("SELECT * FROM audios WHERE id = ? AND activo = 1",
                               (fila["audio_id"],)).fetchone()
            return dict(row) if row else None

        base = ["a.activo = 1", "a.tipo_audio = ?"]
        params: list = [tipo]
        if tipo == "Musica":
            base.append("a.id NOT IN (SELECT audio_id FROM pautas WHERE audio_id IS NOT NULL)")
            # Bandeja de entrada: la música sin aprobar no sale al aire sola
            base.append("COALESCE(a.aprobada, 1) = 1")
        # Restricciones de la canción (Jazler): reglas duras, no se relajan
        if self._tiene_restricciones:
            hora, dia = t.hour, (t.weekday() + 1) % 7
            base.append("(a.restr_desde IS NULL OR a.restr_hasta IS NULL OR a.restr_desde = a.restr_hasta OR"
                        " (a.restr_desde < a.restr_hasta AND ? >= a.restr_desde AND ? < a.restr_hasta) OR"
                        " (a.restr_desde > a.restr_hasta AND (? >= a.restr_desde OR ? < a.restr_hasta)))")
            params += [hora, hora, hora, hora]
            base.append("(COALESCE(a.restr_dias, '') = '' OR instr(',' || a.restr_dias || ',', ?) > 0)")
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
        orden = "a.ultima_reproduccion ASC NULLS FIRST, a.id ASC"
        if self._tiene_prioridad:
            # Prioridad (1 Alta · 2 Normal · 3 Baja): una alta cuenta como si
            # hubiera sonado 12 h antes (sale antes); una baja, 12 h después.
            orden = ("julianday(COALESCE(a.ultima_reproduccion, '1970-01-01'))"
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
