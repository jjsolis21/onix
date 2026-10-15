"""Relojes y planificador (modo automático estilo Jazler SoHo)."""

import json
import sqlite3
from datetime import datetime, timedelta

import pytest

from backend.audio.planner import Planner, duracion_efectiva

LUNES_10 = datetime(2026, 10, 12, 10, 0)     # lunes → día 1 de la grilla


def db(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def audio(path, titulo, artista=None, tipo="Musica", dur=200, **cols):
    conn = db(path)
    campos = {"titulo": titulo, "artista": artista or titulo, "archivo_path": f"x/{titulo}.mp3",
              "tipo_audio": tipo, "duracion": dur, **cols}
    cur = conn.execute(f"INSERT INTO audios ({', '.join(campos)}) VALUES ({', '.join('?' * len(campos))})",
                       list(campos.values()))
    conn.commit()
    conn.close()
    return cur.lastrowid


def reloj(path, nombre, filas, horas=((1, 10),), compensacion=None):
    conn = db(path)
    rid = conn.execute("INSERT INTO relojes (nombre, compensacion) VALUES (?, ?)",
                       (nombre, json.dumps(compensacion or {}))).lastrowid
    for i, f in enumerate(filas):
        conn.execute("INSERT INTO reloj_filas (reloj_id, orden, tipo_audio, filtros, audio_id)"
                     " VALUES (?, ?, ?, ?, ?)",
                     (rid, i, f.get("tipo", "Musica"), json.dumps(f.get("filtros", {})), f.get("audio_id")))
    for dia, hora in horas:
        conn.execute("INSERT OR REPLACE INTO reloj_grilla VALUES (?, ?, ?)", (dia, hora, rid))
    conn.commit()
    conn.close()
    return rid


def cfg(path, **valores):
    conn = db(path)
    for k, v in valores.items():
        conn.execute("INSERT OR REPLACE INTO config VALUES (?, ?)", (k, str(v)))
    conn.commit()
    conn.close()


def planear(path, desde=LUNES_10, minutos=60, tandas=(15, 45), plan=()):
    from datetime import timedelta
    return Planner(path).extender(list(plan), desde, desde + timedelta(minutes=minutos), [], list(tandas))


@pytest.fixture
def base(tmp_db):
    cfg(tmp_db, aleatoriedad=1, crossfade_seg=0)   # determinista y sin solape
    return tmp_db


def test_duracion_efectiva():
    assert duracion_efectiva({"duracion": 200}, 4) == 196
    assert duracion_efectiva({"duracion": 200, "fade_out": 10}, 4) == 190
    assert duracion_efectiva({"duracion": 200, "cue_mezcla": 150, "cue_inicio": 5}, 4) == 145


def test_reloj_sigue_el_orden_de_sus_filas(base):
    for i in range(4):
        audio(base, f"EXITO {i}", categoria="EXITO")
        audio(base, f"NUEVO {i}", categoria="NUEVO")
    jid = audio(base, "ID ESTACION", tipo="Efecto", dur=10)
    reloj(base, "Hora normal", [
        {"tipo": "Efecto", "audio_id": jid},
        {"filtros": {"cat2": "EXITO"}},
        {"filtros": {"cat2": "NUEVO"}},
    ])
    items = [i for i in planear(base, tandas=()) if i["tipo"] == "audio"]
    assert [i["titulo"] for i in items[:3]] == ["ID ESTACION", items[1]["titulo"], items[2]["titulo"]]
    assert items[1]["titulo"].startswith("EXITO") and items[2]["titulo"].startswith("NUEVO")
    # El reloj se repite: fila 0 vuelve a salir
    assert items[3]["titulo"] == "ID ESTACION" and items[3]["reloj"] == "Hora normal"


def test_cada_hora_empieza_en_la_primera_fila(base):
    jid = audio(base, "ID", tipo="Efecto", dur=10)
    for i in range(30):
        audio(base, f"TEMA {i}")
    reloj(base, "Con ID", [{"tipo": "Efecto", "audio_id": jid}, {}, {}, {}, {}, {}, {}, {}, {}, {}, {}, {}, {}, {}, {}, {}, {}, {}],
          horas=((1, 10), (1, 11)))
    items = planear(base, minutos=120, tandas=())
    primeros_11 = [i for i in items if i["inicio"] >= "2026-10-12T11:00"]
    assert primeros_11[0]["titulo"] == "ID"


def test_hora_sin_reloj_usa_solo_musica(base):
    for i in range(5):
        audio(base, f"TEMA {i}")
    audio(base, "JINGLE", tipo="Efecto", dur=10)
    items = planear(base, tandas=())
    assert items and all(i["tipo_audio"] == "Musica" for i in items)


def test_separacion_de_artista_y_sin_repetir(base):
    for i in range(3):
        audio(base, f"A{i}", artista="ARTISTA A")
    for i in range(20):
        audio(base, f"OTRO {i}", artista=f"OTRO {i}")
    cfg(base, ventana_artista_min=60)
    items = planear(base, tandas=())
    artistas = [i["artista"] for i in items]
    assert artistas.count("ARTISTA A") <= 1
    ids = [i["audio_id"] for i in items]
    assert len(ids) == len(set(ids))


def test_no_mas_de_dos_con_la_misma_voz(base):
    for i in range(10):
        audio(base, f"H{i}", genero_vocal="HOMBRE")
        audio(base, f"M{i}", genero_vocal="MUJER")
    cfg(base, max_mismo_genero=2)
    voces = [i["genero_vocal"] for i in planear(base, tandas=())]
    for k in range(len(voces) - 2):
        assert len(set(voces[k:k + 3])) > 1


def _pauta(path, minutos_tanda=(15,), dur=30):
    aid = audio(path, "CUÑA", tipo="Cuña", dur=dur)
    conn = db(path)
    conn.execute("INSERT INTO pautas (cliente, audio_id, matriz) VALUES ('CLIENTE', ?, ?)",
                 (aid, json.dumps({"1-10": True})))
    conn.commit()
    conn.close()


def test_tanda_aparece_con_su_duracion_y_compensacion(base):
    _pauta(base)
    # Canciones de 200 s y una de 100 s que "encaja" justo antes de las 10:15
    for i in range(10):
        audio(base, f"LARGA {i}", dur=200)
    audio(base, "CORTA", dur=100)
    cfg(base, tolerancia_tanda_seg=30)
    items = planear(base, minutos=30, tandas=(15,))
    tanda = next(i for i in items if i["tipo"] == "tanda")
    assert tanda["minuto"] == 15 and tanda["cunas"] == 1 and tanda["duracion"] == 30
    # 4 × 200 s = 13:20; la quinta de 200 s llegaría a 16:40 → se compensa con la de 100 s
    antes = items[:items.index(tanda)]
    assert antes[-1]["titulo"] == "CORTA" and antes[-1]["compensacion"] is True
    assert tanda["inicio"] == "2026-10-12T10:15:00"


def test_sin_cunas_no_hay_marcador_de_tanda(base):
    for i in range(10):
        audio(base, f"T{i}")
    assert not [i for i in planear(base) if i["tipo"] == "tanda"]


def test_tanda_ya_emitida_no_se_planifica_otra_vez(base):
    _pauta(base)
    for i in range(10):
        audio(base, f"T{i}")
    from datetime import timedelta
    items = Planner(base).extender([], LUNES_10, LUNES_10 + timedelta(minutes=30), [], [15],
                                   tandas_emitidas={("2026-10-12-10", 15)})
    assert not [i for i in items if i["tipo"] == "tanda"]


def test_biblioteca_chica_repite_canciones_en_vez_de_solo_jingles(base):
    """Con pocas canciones el plan repite música; nunca se llena de jingles."""
    jid = audio(base, "ID", tipo="Efecto", dur=8)
    for i in range(3):
        audio(base, f"TEMA {i}", artista=f"A{i}", dur=200)
    reloj(base, "Con ID", [{"tipo": "Efecto", "audio_id": jid}, {}, {}], horas=((1, 10), (1, 11)))
    items = planear(base, minutos=120, tandas=())
    musica = [i for i in items if i["tipo_audio"] == "Musica"]
    assert len(items) < 80
    assert len(musica) >= 30                      # 2 h de música de 200 s
    for k in range(len(items) - 1):               # nunca dos ID seguidos
        assert not (items[k]["titulo"] == "ID" and items[k + 1]["titulo"] == "ID")
    titulos = [m["titulo"] for m in musica]
    for k in range(len(titulos) - 1):             # ni la misma canción dos veces seguidas
        assert titulos[k] != titulos[k + 1]


def test_extender_a_mitad_de_hora_sigue_el_reloj(base):
    """Al completar el plan (o regenerarlo) el reloj no vuelve a la fila 1."""
    from datetime import timedelta
    jid = audio(base, "ID", tipo="Efecto", dur=8)
    for i in range(20):
        audio(base, f"TEMA {i}", artista=f"A{i}", dur=200)
    reloj(base, "Con ID", [{"tipo": "Efecto", "audio_id": jid}, {}, {}, {}, {}, {}])
    p = Planner(base)
    primera = p.extender([], LUNES_10, LUNES_10 + timedelta(minutes=10), [], [])
    assert primera[0]["titulo"] == "ID" and primera[-1]["fila"] == len(primera) - 1
    fin = datetime.fromisoformat(primera[-1]["inicio"]) + timedelta(seconds=primera[-1]["duracion"])
    segunda = p.extender(primera, fin, fin + timedelta(minutes=10), [], [])
    assert segunda[0]["titulo"] != "ID"
    assert segunda[0]["fila"] == (primera[-1]["fila"] + 1) % 6
    # Plan vacío pero sonando la fila 2 de este reloj: sigue en la 3
    rid = primera[0]["reloj_id"]
    tercera = p.extender([], fin, fin + timedelta(minutes=5), [], [],
                         ultimo={"inicio": fin.isoformat(), "reloj_id": rid, "fila": 2})
    assert tercera[0]["fila"] == 3



def test_tanda_inmediata_corta_la_cancion_en_el_plan(base):
    _pauta(base)
    for i in range(10):
        audio(base, f"LARGA {i}", dur=500)
    cfg(base, tandas_inmediatas="15", tolerancia_tanda_seg=0)
    items = planear(base, minutos=30, tandas=(15,))
    tanda = next(i for i in items if i["tipo"] == "tanda")
    previa = items[items.index(tanda) - 1]
    assert tanda["inicio"] == "2026-10-12T10:15:00" and tanda["inmediata"] is True
    assert previa["cortada"] is True


def test_estimacion_de_tanda_incluye_id_y_cortinas(base):
    _pauta(base, dur=30)                                    # comercial
    aid = audio(base, "ID", tipo="Cuña", dur=5)
    ap = audio(base, "APERTURA", tipo="Cuña", dur=7)
    conn = db(base)
    for a, sec in ((aid, 1), (ap, 4)):                        # 4 = Cortina de salida (opcional)
        conn.execute("INSERT INTO pautas (cliente, audio_id, matriz, seccion) VALUES ('EST', ?, ?, ?)",
                     (a, json.dumps({"1-10": True}), sec))
    conn.commit()
    conn.close()
    for i in range(10):
        audio(base, f"T{i}", dur=200)
    tandas = [i for i in planear(base, minutos=60, tandas=(15, 45)) if i["tipo"] == "tanda"]
    t15 = next(t for t in tandas if t["minuto"] == 15)
    t45 = next(t for t in tandas if t["minuto"] == 45)
    assert (t15["cunas"], t15["duracion"]) == (3, 42)       # comercial + ID + apertura
    assert (t45["cunas"], t45["duracion"]) == (1, 5)        # sin comercial: solo el ID


# ── Eventos a hora fija (Jazler "Force Clocks") ─────────────────────────────

def evento(path, nombre, reloj_id, hora, dias="0,1,2,3,4,5,6", inmediato=0, **cols):
    conn = db(path)
    campos = {"nombre": nombre, "reloj_id": reloj_id, "hora": hora, "dias": dias,
              "inmediato": inmediato, **cols}
    conn.execute(f"INSERT INTO reloj_eventos ({', '.join(campos)}) VALUES ({', '.join('?' * len(campos))})",
                 list(campos.values()))
    conn.commit()
    conn.close()


def _con_programa_especial(base):
    for i in range(20):
        audio(base, f"TEMA {i}", artista=f"A{i}", dur=200)
    ap = audio(base, "APERTURA ESPECIAL", tipo="Efecto", dur=10)
    prog = audio(base, "PROGRAMA GRABADO", tipo="Programa", dur=600)
    return reloj(base, "Especial", [{"tipo": "Efecto", "audio_id": ap},
                                    {"tipo": "Programa", "audio_id": prog}], horas=())


def test_evento_suena_una_vez_a_su_hora_y_vuelve_la_grilla(base):
    rid = _con_programa_especial(base)
    evento(base, "Programa del lunes", rid, "10:20")
    items = planear(base, minutos=60, tandas=())
    titulos = [i["titulo"] for i in items]
    assert titulos.count("APERTURA ESPECIAL") == 1 and titulos.count("PROGRAMA GRABADO") == 1
    k = titulos.index("APERTURA ESPECIAL")
    assert items[k + 1]["titulo"] == "PROGRAMA GRABADO" and items[k]["evento"] == "Programa del lunes"
    # No inmediato: entra al terminar la canción, cerca de su hora (compensación)
    ini = datetime.fromisoformat(items[k]["inicio"])
    assert abs((ini - datetime(2026, 10, 12, 10, 20)).total_seconds()) <= 90
    assert items[k + 2]["tipo_audio"] == "Musica"            # luego, la grilla


def test_evento_inmediato_corta_la_cancion(base):
    rid = _con_programa_especial(base)
    evento(base, "En punto", rid, "10:21", inmediato=1)     # 6 × 200 s = 10:20 → la 7.ª se corta
    items = planear(base, minutos=60, tandas=())
    k = next(i for i, it in enumerate(items) if it.get("evento"))
    assert items[k]["inicio"] == "2026-10-12T10:21:00" and items[k]["inmediato"] is True
    assert items[k - 1]["cortada"] is True


def test_evento_respeta_dias_fechas_y_lo_ya_emitido(base):
    rid = _con_programa_especial(base)
    evento(base, "Solo martes", rid, "10:20", dias="2")
    evento(base, "Vencido", rid, "10:30", fecha_fin="2026-10-01")
    evento(base, "Futuro", rid, "10:40", fecha_inicio="2026-11-01")
    assert not [i for i in planear(base, tandas=()) if i.get("evento")]
    evento(base, "Hoy", rid, "10:20")
    from datetime import timedelta
    ya = Planner(base).extender([], LUNES_10, LUNES_10 + timedelta(hours=1), [], [],
                                eventos_emitidos={"2026-10-12 10:20#4"})
    assert not [i for i in ya if i.get("evento")]


def test_musica_sin_aprobar_no_sale_al_aire(base):
    for i in range(6):
        audio(base, f"APROBADA {i}", artista=f"A{i}")
    for i in range(6):
        audio(base, f"BANDEJA {i}", artista=f"B{i}", aprobada=0)
    titulos = [i["titulo"] for i in planear(base, tandas=())]
    assert titulos and not any(t.startswith("BANDEJA") for t in titulos)


def test_restricciones_por_cancion(base):
    for i in range(12):
        audio(base, f"LIBRE {i}", artista=f"L{i}")
    audio(base, "SOLO NOCHE", artista="N", restr_desde=20, restr_hasta=6)      # 20 h → 6 h
    audio(base, "SOLO SABADO", artista="S", restr_dias="6")
    audio(base, "MAÑANERA", artista="M", restr_desde=6, restr_hasta=12)
    titulos = [i["titulo"] for i in planear(base, minutos=120, tandas=())]      # lunes 10–12 h
    assert "SOLO NOCHE" not in titulos and "SOLO SABADO" not in titulos and "MAÑANERA" in titulos
    from datetime import datetime as dt
    noche = [i["titulo"] for i in planear(base, desde=dt(2026, 10, 12, 22, 0), minutos=60, tandas=())]
    assert "SOLO NOCHE" in noche and "MAÑANERA" not in noche


def test_maximo_por_dia(base):
    cfg(base, separacion_cancion_dias=0, ventana_artista_min=0)
    audio(base, "TOPE 2", artista="T", max_por_dia=2)
    for i in range(3):
        audio(base, f"OTRA {i}", artista=f"O{i}")
    titulos = [i["titulo"] for i in planear(base, minutos=180, tandas=())]
    assert 1 <= titulos.count("TOPE 2") <= 2


def test_prioridad_alta_sale_antes(base):
    cfg(base, aleatoriedad=1)
    audio(base, "NORMAL", artista="N", ultima_reproduccion="2026-10-10 10:00:00")
    audio(base, "ALTA", artista="A", ultima_reproduccion="2026-10-10 18:00:00", prioridad=1)   # sonó 8 h después
    primero = [i for i in planear(base, minutos=10, tandas=()) if i["tipo"] == "audio"][0]["titulo"]
    assert primero == "ALTA"             # 18:00 − 12 h = 06:00: va antes que la normal de las 10:00


def test_tiempo_de_repeticion_por_cancion(base):
    cfg(base, separacion_cancion_dias=0, ventana_artista_min=0)
    audio(base, "CADA 2 HORAS", artista="R", repeticion_horas=2)
    for i in range(3):
        audio(base, f"RELLENO {i}", artista=f"F{i}")
    titulos = [i["titulo"] for i in planear(base, minutos=180, tandas=())]
    assert titulos.count("CADA 2 HORAS") <= 2
    items = [i for i in planear(base, minutos=180, tandas=()) if i["titulo"] == "CADA 2 HORAS"]
    from datetime import datetime as dt
    for x, y in zip(items, items[1:]):
        assert (dt.fromisoformat(y["inicio"]) - dt.fromisoformat(x["inicio"])).total_seconds() >= 7200


def test_tanda_usa_la_duracion_de_la_version_que_toca(tmp_db):
    a = audio(tmp_db, "spot_a", tipo="Cuña", dur=30)
    b = audio(tmp_db, "spot_b", tipo="Cuña", dur=60)
    conn = db(tmp_db)
    pid = conn.execute("INSERT INTO pautas (cliente, audio_id, matriz) VALUES ('X', ?, ?)",
                       (a, json.dumps({"1-10": True}))).lastrowid
    conn.executemany("INSERT INTO pauta_audios (pauta_id, audio_id, orden) VALUES (?, ?, ?)",
                     [(pid, a, 0), (pid, b, 1)])
    conn.commit()
    pl = Planner(tmp_db)
    assert pl._cunas_tanda(conn, LUNES_10, 0, [0]) == (1, 30.0)      # toca la A
    conn.execute("INSERT INTO historial_emision (titulo, pauta_id, slot) VALUES ('s', ?, '2026-10-11-10')", (pid,))
    conn.commit()
    assert pl._cunas_tanda(conn, LUNES_10, 0, [0]) == (1, 60.0)      # ahora la B
    conn.close()


def test_dia_especifico_manda_sobre_la_grilla(base):
    for i in range(4):
        audio(base, f"EXITO {i}", categoria="EXITO")
        audio(base, f"FIESTA {i}", categoria="FIESTA")
    reloj(base, "Normal", [{"filtros": {"cat2": "EXITO"}}])
    especial = reloj(base, "Especial", [{"filtros": {"cat2": "FIESTA"}}], horas=())
    conn = db(base)
    conn.execute("INSERT INTO reloj_dias (fecha, hora, reloj_id) VALUES ('2026-10-12', 10, ?)", (especial,))
    conn.commit()
    conn.close()
    items = [i for i in planear(base, tandas=()) if i["tipo"] == "audio"]
    assert items and all(i["titulo"].startswith("FIESTA") for i in items)


def test_fila_sin_canciones_usa_el_respaldo_del_reloj(base):
    for i in range(4):
        audio(base, f"CLASICO {i}", categoria="CLASICO")
        audio(base, f"OTRO {i}", categoria="OTRO")
    rid = reloj(base, "Con respaldo", [{"filtros": {"cat2": "NO HAY"}}])
    conn = db(base)
    conn.execute("UPDATE relojes SET respaldo = ? WHERE id = ?", (json.dumps({"cat2": "CLASICO"}), rid))
    conn.commit()
    conn.close()
    items = [i for i in planear(base, tandas=()) if i["tipo"] == "audio"]
    assert items and all(i["titulo"].startswith("CLASICO") for i in items[:4])


def test_evento_con_dura_hasta_repite_su_reloj_hasta_esa_hora(base):
    for i in range(20):
        audio(base, f"TEMA {i}", artista=f"A{i}", dur=200)
    for i in range(12):
        audio(base, f"ESPECIAL {i}", artista=f"E{i}", categoria="ESPECIAL", dur=180 + i * 10)
    rid = reloj(base, "Especial", [{"filtros": {"cat2": "ESPECIAL"}}], horas=())
    evento(base, "Especial 45 min", rid, "10:10", inmediato=1, hasta="10:40")
    items = planear(base, minutos=60, tandas=())
    ev = [i for i in items if i.get("evento")]
    assert len(ev) > 1 and all(i["titulo"].startswith("ESPECIAL") for i in ev)
    fin_ev = datetime.fromisoformat(ev[-1]["inicio"]) + timedelta(seconds=ev[-1]["duracion"])
    assert abs((fin_ev - datetime(2026, 10, 12, 10, 40)).total_seconds()) <= 90
    despues = items[items.index(ev[-1]) + 1]
    assert not despues.get("evento") and despues["titulo"].startswith("TEMA")     # vuelve la grilla


def test_playlist_programada_entra_a_su_hora(base):
    for i in range(10):
        audio(base, f"TEMA {i}", artista=f"A{i}", dur=200)
    x = audio(base, "PL UNO", dur=100)
    y = audio(base, "PL DOS", dur=100)
    conn = db(base)
    pid = conn.execute("INSERT INTO playlists (nombre, programada, inmediato) VALUES ('Prog', '2026-10-12T10:20', 1)").lastrowid
    conn.executemany("INSERT INTO playlist_items (playlist_id, posicion, audio_id) VALUES (?, ?, ?)",
                     [(pid, 0, x), (pid, 1, y)])
    conn.commit()
    conn.close()
    items = planear(base, minutos=60, tandas=())
    k = next(i for i, it in enumerate(items) if it["titulo"] == "PL UNO")
    assert items[k]["inicio"] == "2026-10-12T10:20:00" and items[k + 1]["titulo"] == "PL DOS"
    # La playlist entra una sola vez (sus temas pueden volver luego por la rotación normal)
    assert sum(1 for it in items if it.get("evento") == "Playlist Prog") == 2


def test_filtros_de_anio_y_prioridad(tmp_db):
    audio(tmp_db, "VIEJA", fecha_lanzamiento="1998-01-01")
    audio(tmp_db, "MEDIA", fecha_lanzamiento="2010", prioridad=1)
    audio(tmp_db, "NUEVA", fecha_lanzamiento="2025", prioridad=3)
    audio(tmp_db, "SIN ANIO")
    conn = db(tmp_db)

    def titulos(filtros):
        clauses, params = Planner(tmp_db)._where_filtros(filtros)
        sql = "SELECT a.titulo FROM audios a WHERE " + " AND ".join(["1 = 1"] + clauses) + " ORDER BY a.titulo"
        return [r[0] for r in conn.execute(sql, params)]

    assert titulos({"anio_min": 2000}) == ["MEDIA", "NUEVA"]
    assert titulos({"anio_max": 2010}) == ["MEDIA", "VIEJA"]             # la que no tiene año no entra
    assert titulos({"anio_min": 2000, "anio_max": 2012}) == ["MEDIA"]
    assert titulos({"prioridad": 1}) == ["MEDIA"]
    assert titulos({"prioridad": 2}) == ["SIN ANIO", "VIEJA"]            # sin prioridad = Normal
    assert titulos({}) == ["MEDIA", "NUEVA", "SIN ANIO", "VIEJA"]
    conn.close()


def test_grilla_de_tandas_por_dia_y_hora_en_el_plan(base):
    _pauta(base)
    for i in range(20):
        audio(base, f"T{i}", dur=200)
    cfg(base, tandas_grilla=json.dumps({"1-10": {"minutos": "30", "inmediatas": "30"}, "1-11": {"off": True}}))
    tandas = [i for i in planear(base, minutos=120, tandas=(15, 45)) if i["tipo"] == "tanda"]
    assert [(t["slot"][-2:], t["minuto"]) for t in tandas] == [("10", 30)]
    assert tandas[0]["inmediata"] is True and tandas[0]["inicio"] == "2026-10-12T10:30:00"


def test_la_tanda_al_cierre_se_marca_en_el_plan_y_entra_antes_de_su_minuto(base):
    """Al cierre en :00 con 31 s de cuñas: el plan la pone a las 10:59:29 (no a las 11:00) y la rotula «cierre»."""
    _pauta(base, dur=31)
    for i in range(30):
        audio(base, f"C{i}", dur=200)
    cfg(base, tandas_cierre="0", tandas_inmediatas="0")          # el ejemplo B: minuto 0 con inicio inmediato y al cierre
    items = planear(base, minutos=75, tandas=(0,))
    t = next(i for i in items if i["tipo"] == "tanda" and i["slot"].endswith("-10"))
    assert t["cierre"] is True and t["inmediata"] is True and t["minuto"] == 0
    assert t["inicio"][:16] == "2026-10-12T10:59" and t["duracion"] == 31


def test_tanda_al_cierre_sin_inmediato_no_se_pierde_del_plan_si_la_cancion_cruza_la_hora(base):
    """Canciones de 200 s que cruzan las 11:00: la tanda de las 10:59:29 queda marcada justo al terminar la canción."""
    _pauta(base, dur=31)
    for i in range(30):
        audio(base, f"C{i}", dur=200)
    cfg(base, tandas_cierre="0", tandas_inmediatas="")
    items = planear(base, minutos=75, tandas=(0,))
    tandas = [i for i in items if i["tipo"] == "tanda"]
    assert len(tandas) == 1 and tandas[0]["cierre"] is True and tandas[0]["slot"].endswith("-10")
    assert tandas[0]["inicio"][11:16] == "11:00"                 # entra al terminar la canción que cruzó la hora
