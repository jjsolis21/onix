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
                 excluir: set = frozenset(), tandas_emitidas: set = frozenset()) -> list[dict]:
        """
        Devuelve los ítems nuevos que cubren [desde, hasta). `plan` es lo ya
        planificado (cuenta para las reglas), `historial` lo que ya sonó:
        [{"audio_id", "artista", "genero_vocal", "ts"}].
        """
        conn = self._conn()
        try:
            self._cargar(conn)
            return self._extender(conn, plan, desde, hasta, historial, tandas_minutos,
                                  excluir, set(tandas_emitidas))
        finally:
            conn.close()

    def _extender(self, conn, plan, desde, hasta, historial, tandas_minutos, excluir,
                  tandas_emitidas):
        crossfade = self._icfg("crossfade_seg", 4)
        tol = self._icfg("tolerancia_tanda_seg", 90)
        nuevos: list[dict] = []
        # Lo planificado cuenta como "ya sonó" para las reglas
        previos = list(historial) + [
            {"audio_id": p.get("audio_id"), "artista": p.get("artista"),
             "genero_vocal": p.get("genero_vocal"), "tipo_audio": p.get("tipo_audio"),
             "ts": p["inicio"]}
            for p in plan if p["tipo"] == "audio"
        ]
        en_plan = {p.get("audio_id") for p in plan if p["tipo"] == "audio"}
        tandas_hechas = {(p["slot"], p["minuto"]) for p in plan if p["tipo"] == "tanda"} | tandas_emitidas

        t = desde
        hora_actual, fila_idx, vueltas_vacias = None, 0, 0
        while t < hasta and len(nuevos) < 400:
            # ── Tanda pendiente en esta hora ───────────────────────────────
            tanda = self._proxima_tanda(conn, t, tandas_minutos, tandas_hechas)
            if tanda and tanda["hora"] <= t + timedelta(seconds=tol):
                tandas_hechas.add((tanda["slot"], tanda["minuto"]))
                if tanda["duracion"] > 0:
                    nuevos.append(_item("tanda", t, tanda["duracion"], minuto=tanda["minuto"],
                                        slot=tanda["slot"], cunas=tanda["cunas"],
                                        titulo=f"TANDA :{tanda['minuto']:02d}"))
                    t += timedelta(seconds=tanda["duracion"])
                continue

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
            if audio and tanda and fila["tipo_audio"] == "Musica":
                restante = (tanda["hora"] - t).total_seconds()
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

            dur = duracion_efectiva(audio, crossfade)
            nuevos.append(_item(
                "audio", t, dur, audio_id=audio["id"], titulo=audio["titulo"],
                artista=audio["artista"], tipo_audio=audio["tipo_audio"],
                genero_vocal=audio.get("genero_vocal"), duracion_total=audio.get("duracion") or 0,
                reloj_id=reloj["id"] if reloj else None,
                reloj=reloj["nombre"] if reloj else None,
                color=reloj["color"] if reloj else None,
                fila=fila.get("orden"), compensacion=compensado,
            ))
            if audio["tipo_audio"] == "Musica":
                en_plan.add(audio["id"])
            previos.append({"audio_id": audio["id"], "artista": audio["artista"],
                            "genero_vocal": audio.get("genero_vocal"),
                            "tipo_audio": audio["tipo_audio"], "ts": t.isoformat()})
            t += timedelta(seconds=dur)
        return nuevos

    # ── Tandas ──────────────────────────────────────────────────────────────

    def _proxima_tanda(self, conn, t: datetime, minutos: list[int], hechas: set) -> Optional[dict]:
        """Próxima tanda (de esta hora o la siguiente) aún no planificada."""
        for base in (t.replace(minute=0, second=0, microsecond=0),
                     t.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)):
            slot = base.strftime("%Y-%m-%d-%H")
            for m in sorted(minutos):
                hora = base + timedelta(minutes=m)
                if (slot, m) in hechas or hora < t - timedelta(minutes=30):
                    continue
                cunas, dur = self._cunas_tanda(conn, base, m, minutos)
                return {"hora": hora, "minuto": m, "slot": slot, "cunas": cunas, "duracion": dur}
        return None

    def _cunas_tanda(self, conn, base: datetime, minuto: int, minutos: list[int]):
        """Cuántas cuñas y cuántos segundos tendrá la tanda (mismo reparto que el motor)."""
        from backend.audio.audio_engine import planificar_tandas
        key = f"{(base.weekday() + 1) % 7}-{base.hour}"
        dia = base.date().isoformat()
        pautas = []
        for r in conn.execute(
            """
            SELECT p.id, p.rubro, p.matriz, a.duracion FROM pautas p
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
                pautas.append({"pauta_id": r["id"], "rubro": r["rubro"], "dur": r["duracion"] or 30})
        if not pautas:
            return 0, 0.0
        lista = planificar_tandas(pautas, sorted(minutos)).get(minuto, [])
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
        fc, fp = self._where_filtros(fila.get("filtros") or {})
        base += fc
        params += fp
        excl = [i for i in set(excluir) | (en_plan if tipo == "Musica" else set()) if isinstance(i, int)]
        if excl:
            base.append(f"a.id NOT IN ({','.join('?' * len(excl))})")
            params += excl
        if dur_objetivo:
            base.append("COALESCE(a.duracion, 0) BETWEEN ? AND ?")
            params += [dur_objetivo[0], dur_objetivo[1]]

        reglas = []
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
        # Cascada: todas las reglas → sin la de días → sin ninguna
        for k in (len(reglas), max(0, len(reglas) - 1), 0):
            clauses = base + [c for c, _ in reglas[:k]]
            prm = params + [v for _, ps in reglas[:k] for v in ps]
            rows = conn.execute(
                f"SELECT a.* FROM audios a WHERE {' AND '.join(clauses)} ORDER BY {orden} LIMIT ?",
                prm + [n]).fetchall()
            if rows:
                return dict(random.choice(rows))
        return None

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
