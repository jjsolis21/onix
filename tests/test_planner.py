"""Relojes y planificador (modo automático estilo Jazler SoHo)."""

import json
import sqlite3
from datetime import datetime

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
