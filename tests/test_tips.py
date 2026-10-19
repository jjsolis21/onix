"""
Tips sobre la música (estilo Jazler SoHo): al inicio del intro, al final del intro (termina cuando entra la voz)
o de puente (cruza el cambio de canción: mitad del tip antes de que entre la siguiente y mitad encima de ella). Motor en mock: se mueve la posición a mano.
"""

from backend.audio.audio_engine import CANAL_TIP
from backend.audio.planner import calcular_tip, nuevo_uid

from test_engine import _al, add_audio, add_pauta, db, engine, media  # noqa: F401  (fixtures)
import test_planner as tp


# ── Modelo de tiempos (ejemplos del dueño) ──────────────────────────────────

def test_final_del_intro_intro_20_tip_7_empieza_en_13():
    assert calcular_tip("intro_final", 7, 0, 20, 180) == (13, 20)


def test_inicio_del_intro_suena_desde_el_segundo_cero():
    assert calcular_tip("intro_inicio", 7, 0, 20, 180) == (0, 20)
    assert calcular_tip("intro_inicio", 7, 2.5, 20, 180) == (2.5, 20)      # desde el cue de inicio


def test_puente_tip_de_6_entra_3_segundos_antes_de_que_entre_la_siguiente():
    assert calcular_tip("puente", 6, 0, 20, 180) == (177, None)
    assert calcular_tip("puente", 8, 0, 20, 180) == (176, None)


def test_sin_intro_marcado_va_al_inicio():
    assert calcular_tip("intro_final", 7, 0, 0, 180) == (0, None)
    assert calcular_tip("intro_inicio", 7, 0, 0, 180) == (0, None)


def test_tip_mas_largo_que_el_intro_empieza_al_inicio_y_se_recorta_en_la_voz():
    assert calcular_tip("intro_final", 12, 0, 8, 180) == (0, 8)


# ── Motor ───────────────────────────────────────────────────────────────────

def _volumenes(engine):
    """(canal, volumen) de cada slide_volume, para ver cuándo baja y sube la música."""
    vols = []
    real = engine._audio.slide_volume
    engine._audio.slide_volume = lambda c, v, ms: (vols.append((c, round(v, 3))), real(c, v, ms))[1]
    return vols


def _eventos(engine):
    ev = []
    for n in ("tip_programado", "tip_al_aire", "tip_fin"):
        engine.bus.subscribe(n, lambda e, d: ev.append((e, d)))
    return ev


def _tip_manual(engine, tip, modo):
    assert engine.tip_programar(tip, modo)
    engine._drain_commands()


def test_final_del_intro_en_el_motor_baja_la_musica_y_no_pisa_la_voz(engine, media):
    tema = add_audio(engine, media, "TEMA", seconds=60.0, cue_intro=20.0, cue_mezcla=55.0)
    tip = add_audio(engine, media, "TIP 7", tipo="Tips", seconds=7.0)
    engine._mode = "manual"
    ev, vols = _eventos(engine), _volumenes(engine)
    _tip_manual(engine, tip, "intro_final")
    assert engine.status["tip_pendiente"]["modo"] == "intro_final"

    assert engine._start({"track": engine._fetch_track(tema), "origen": "manual"})
    canal = engine._active_canal
    t = engine.status["tip"]
    assert t["estado"] == "espera" and abs(t["disparo"] - 13.0) < 0.01 and t["limite"] == 20.0
    assert not t["recorta"] and engine.status["tip_pendiente"] is None

    _al(engine, 12.9)
    assert engine._tip["estado"] == "espera" and not engine._audio.is_active(CANAL_TIP)
    _al(engine, 13.0)
    assert engine._tip["estado"] == "sonando" and engine._audio.is_active(CANAL_TIP)
    pleno = engine._target_volume(engine._current_track) / engine._tip_duck
    assert (canal, round(pleno * engine._tip_duck, 3)) in vols     # la música bajó al nivel del tip (no tanto como bajo una voz)

    _al(engine, 20.0 - engine.TIP_CORTE)                           # llega la voz: el tip se despide
    assert engine._tip["saliendo"] and (CANAL_TIP, 0.0) in vols
    assert vols[-1] == (canal, round(pleno, 3))                    # y la música vuelve a pleno
    _al(engine, 20.0)
    assert engine._tip is None and not engine._audio.is_active(CANAL_TIP)
    assert [e for e, _ in ev] == ["tip_programado", "tip_programado", "tip_al_aire", "tip_fin"]
    hist = [r[0] for r in db(engine).execute("SELECT tipo FROM historial_emision ORDER BY id")]
    assert hist == ["musica", "tips"]


def test_inicio_del_intro_sale_con_la_cancion(engine, media):
    tema = add_audio(engine, media, "TEMA", seconds=60.0, cue_intro=20.0, cue_mezcla=55.0)
    tip = add_audio(engine, media, "TIP 5", tipo="Tips", seconds=5.0)
    engine._mode = "manual"
    _tip_manual(engine, tip, "intro_inicio")
    engine._start({"track": engine._fetch_track(tema), "origen": "manual"})
    assert engine._tip["estado"] == "sonando" and engine._tip["disparo"] == 0


def test_tip_mas_largo_que_el_intro_se_recorta_con_fundido(engine, media):
    tema = add_audio(engine, media, "TEMA", seconds=60.0, cue_intro=6.0, cue_mezcla=55.0)
    tip = add_audio(engine, media, "TIP 9", tipo="Tips", seconds=9.0)
    engine._mode = "manual"
    _tip_manual(engine, tip, "intro_final")
    engine._start({"track": engine._fetch_track(tema), "origen": "manual"})
    assert engine._tip["estado"] == "sonando" and engine._tip["recorta"]     # empieza ya, en el segundo 0
    _al(engine, 6.0 - engine.TIP_CORTE)
    assert engine._tip["saliendo"]
    _al(engine, 6.0)
    assert engine._tip is None                                     # a la voz no llega


def test_cancion_sin_intro_el_tip_va_al_inicio(engine, media):
    tema = add_audio(engine, media, "TEMA", seconds=60.0, cue_mezcla=55.0)
    tip = add_audio(engine, media, "TIP", tipo="Tips", seconds=7.0)
    engine._mode = "manual"
    _tip_manual(engine, tip, "intro_final")
    engine._start({"track": engine._fetch_track(tema), "origen": "manual"})
    assert engine._tip["estado"] == "sonando" and engine._tip["disparo"] == 0


def test_tip_de_intro_espera_a_la_cancion_tras_la_tanda(engine, media):
    """La cuña o el jingle que salen antes no se llevan el tip: va sobre la primera canción."""
    spot = add_audio(engine, media, "CUNA", tipo="Cuña", seconds=10.0)
    tema = add_audio(engine, media, "TEMA", seconds=60.0, cue_intro=20.0)
    tip = add_audio(engine, media, "TIP", tipo="Tips", seconds=7.0)
    engine._mode = "manual"
    _tip_manual(engine, tip, "intro_final")
    engine._start({"track": engine._fetch_track(spot), "origen": "pauta"})
    assert engine._tip is None and engine._tip_pend
    engine._start({"track": engine._fetch_track(tema), "origen": "manual"})
    assert engine._tip["item"]["track"]["id"] == tema


def test_una_cuna_que_entra_corta_el_tip_y_entra_a_pleno(engine, media):
    tema = add_audio(engine, media, "TEMA", seconds=60.0, cue_intro=20.0)
    spot = add_audio(engine, media, "CUNA", tipo="Cuña", seconds=10.0)
    tip = add_audio(engine, media, "TIP", tipo="Tips", seconds=7.0)
    engine._mode = "manual"
    _tip_manual(engine, tip, "intro_inicio")
    engine._start({"track": engine._fetch_track(tema), "origen": "manual"})
    assert engine._tip["estado"] == "sonando"
    engine._start({"track": engine._fetch_track(spot), "origen": "pauta"})
    assert engine._tip["saliendo"] and not engine._tip["duck"]
    assert engine._target_volume(engine._current_track) == engine._master_volume * engine._gain_of(engine._current_track)
    engine._tip["corta_en"] = 0
    engine._tick()
    assert engine._tip is None


def test_operador_adelanta_mas_alla_del_intro_el_tip_no_sale(engine, media):
    tema = add_audio(engine, media, "TEMA", seconds=60.0, cue_intro=20.0, cue_mezcla=55.0)
    tip = add_audio(engine, media, "TIP", tipo="Tips", seconds=7.0)
    engine._mode = "manual"
    _tip_manual(engine, tip, "intro_final")
    engine._start({"track": engine._fetch_track(tema), "origen": "manual"})
    _al(engine, 25.0)
    assert engine._tip is None and not engine._audio.is_active(CANAL_TIP)


def test_pausa_y_stop_alcanzan_al_tip(engine, media):
    tema = add_audio(engine, media, "TEMA", seconds=60.0, cue_intro=20.0)
    tip = add_audio(engine, media, "TIP", tipo="Tips", seconds=7.0)
    engine._mode = "manual"
    _tip_manual(engine, tip, "intro_inicio")
    engine._start({"track": engine._fetch_track(tema), "origen": "manual"})
    engine.pausar(True); engine._drain_commands()
    assert not engine._audio.is_playing(CANAL_TIP)
    engine.pausar(False); engine._drain_commands()
    assert engine._audio.is_playing(CANAL_TIP)
    engine.stop_playback(); engine._drain_commands()
    assert engine._tip is None and not engine._audio.is_active(CANAL_TIP)


def _plan(engine, *items):
    with engine._programa_lock:
        engine._programa.clear()
        engine._programa.extend(items)


def _p_audio(audio_id, tipo_audio="Musica", **kw):
    return {"uid": nuevo_uid(), "tipo": "audio", "audio_id": audio_id, "tipo_audio": tipo_audio,
            "duracion": 0 if kw.get("tip_modo") else 50, "inicio": "2026-10-12T10:00:00", **kw}


def test_puente_largo_empieza_antes_de_la_mezcla_y_sigue_sobre_la_siguiente(engine, media):
    a = add_audio(engine, media, "A", seconds=60.0, cue_mezcla=50.0)
    b = add_audio(engine, media, "B", seconds=60.0, cue_intro=10.0)
    tip = add_audio(engine, media, "TIP 24", tipo="Tips", seconds=24.0)
    vols = _volumenes(engine)
    tip_item = _p_audio(tip, "Tips", tip_modo="puente")
    _plan(engine, tip_item, _p_audio(b))
    engine._start({"track": engine._fetch_track(a), "origen": "auto"})
    assert engine._tip["modo"] == "puente" and abs(engine._tip["disparo"] - 38.0) < 0.01   # mezcla 50 − 24/2 = 38

    _al(engine, 38.0)                                              # 12 s antes de que entre B
    assert engine._tip["estado"] == "sonando"
    assert all(i["uid"] != tip_item["uid"] for i in engine._programa)   # sale del programa al sonar
    _al(engine, 50.0)                                              # mezcla: entra B y el tip sigue encima
    assert engine._current_track["id"] == b
    assert engine._tip and engine._tip["estado"] == "sonando" and engine._tip["item"]["track"]["id"] == b
    assert engine._tip["limite"] == 10.0                           # tampoco pisa la voz de B
    canal_b = engine._active_canal
    engine._audio.stop(CANAL_TIP)                                  # el tip termina
    engine._tick()
    assert engine._tip is None
    assert vols[-1] == (canal_b, round(engine._target_volume(engine._current_track), 3))   # B a pleno


def test_puente_no_se_monta_si_lo_que_sigue_no_es_una_cancion(engine, media):
    a = add_audio(engine, media, "A", seconds=60.0, cue_mezcla=50.0)
    voz = add_audio(engine, media, "VT", tipo="Locucion", seconds=6.0)
    tip = add_audio(engine, media, "TIP 24", tipo="Tips", seconds=24.0)
    _plan(engine, _p_audio(tip, "Tips", tip_modo="puente"), _p_audio(voz, "Locucion"))
    engine._start({"track": engine._fetch_track(a), "origen": "auto"})
    _al(engine, 38.0)
    assert engine._tip is None and not engine._audio.is_active(CANAL_TIP)
    _al(engine, 50.0)
    assert engine._current_track["id"] == voz                      # la fila del tip se descarta sin sonar sola


def test_tip_de_intro_del_reloj_va_sobre_la_cancion_siguiente(engine, media):
    a = add_audio(engine, media, "A", seconds=60.0, cue_mezcla=50.0)
    b = add_audio(engine, media, "B", seconds=60.0, cue_intro=20.0)
    tip = add_audio(engine, media, "TIP 7", tipo="Tips", seconds=7.0)
    _plan(engine, _p_audio(tip, "Tips", tip_modo="intro_final"), _p_audio(b))
    engine._start({"track": engine._fetch_track(a), "origen": "auto"})
    assert engine._tip is None                                     # no es puente: no se pega a A
    _al(engine, 50.0)
    assert engine._current_track["id"] == b
    assert engine._tip["estado"] == "espera" and abs(engine._tip["disparo"] - 13.0) < 0.01


def test_puente_manual_sobre_la_cancion_que_suena(engine, media):
    a = add_audio(engine, media, "A", seconds=60.0, cue_mezcla=50.0)
    b = add_audio(engine, media, "B", seconds=60.0)
    tip = add_audio(engine, media, "TIP 24", tipo="Tips", seconds=24.0)
    engine._mode = "manual"
    engine._start({"track": engine._fetch_track(a), "origen": "manual"})
    _tip_manual(engine, tip, "puente")
    assert abs(engine._tip["disparo"] - 38.0) < 0.01
    engine.queue_track(b); engine._drain_commands()
    _al(engine, 38.0)
    assert engine._tip["estado"] == "sonando"


def test_puente_manual_sin_cancion_siguiente_no_sale(engine, media):
    a = add_audio(engine, media, "A", seconds=60.0, cue_mezcla=50.0)
    tip = add_audio(engine, media, "TIP 24", tipo="Tips", seconds=24.0)
    engine._mode = "manual"
    engine._start({"track": engine._fetch_track(a), "origen": "manual"})
    errores = []
    engine.bus.subscribe("playback_error", lambda e, d: errores.append(d))
    _tip_manual(engine, tip, "puente")
    _al(engine, 38.0)
    assert engine._tip is None
    assert errores and "no salió" in errores[-1]["msg"]            # se le avisa al operador, no falla en silencio


def test_tip_programar_rechaza_un_modo_desconocido(engine, media):
    import pytest
    tip = add_audio(engine, media, "TIP", tipo="Tips", seconds=7.0)
    with pytest.raises(ValueError):
        engine.tip_programar(tip, "encima")


# ── Planificador ────────────────────────────────────────────────────────────

def test_fila_de_tips_con_modo_no_ocupa_tiempo_en_el_plan(tmp_db):
    tp.cfg(tmp_db, aleatoriedad=1, crossfade_seg=0)
    for i in range(4):
        tp.audio(tmp_db, f"TEMA {i}", dur=200)
    tip = tp.audio(tmp_db, "TIP", tipo="Tips", dur=8)
    rid = tp.reloj(tmp_db, "Con puente", [{}, {"tipo": "Tips", "audio_id": tip}, {}])
    conn = tp.db(tmp_db)
    conn.execute("UPDATE reloj_filas SET tip_modo = 'puente' WHERE reloj_id = ? AND tipo_audio = 'Tips'", (rid,))
    conn.commit(); conn.close()
    items = [i for i in tp.planear(tmp_db, tandas=()) if i["tipo"] == "audio"]
    assert items[1]["tipo_audio"] == "Tips" and items[1]["tip_modo"] == "puente" and items[1]["duracion"] == 0
    assert items[1]["inicio"] == items[2]["inicio"]               # la canción siguiente no se corre


def test_puente_de_6_segundos_cruza_el_cambio_con_crossfade(engine, media):
    """Caso real: con crossfade, a los 3 s del final la siguiente ya entró. El tip tiene que empezar ANTES de la mezcla."""
    a = add_audio(engine, media, "A", seconds=24.0)                # mezcla = 24 − 4 de crossfade = 20
    b = add_audio(engine, media, "B", seconds=24.0, cue_intro=10.0)
    tip = add_audio(engine, media, "TIP 6", tipo="Tips", seconds=6.0)
    _plan(engine, _p_audio(tip, "Tips", tip_modo="puente"), _p_audio(b))
    engine._start({"track": engine._fetch_track(a), "origen": "auto"})
    assert abs(engine._mix_point - 20.0) < 0.01 and abs(engine._tip["disparo"] - 17.0) < 0.01
    _al(engine, 16.9)
    assert engine._tip["estado"] == "espera"
    _al(engine, 17.0)                                              # 3 s antes de que entre B
    assert engine._tip["estado"] == "sonando" and engine._current_track["id"] == a
    _al(engine, 20.0)                                              # entra B y el tip sigue encima (3 s sobre B)
    assert engine._current_track["id"] == b and engine._tip["estado"] == "sonando"
    assert engine._tip["limite"] == 10.0                           # no pisa la voz de B


def test_el_tip_no_es_la_cancion_siguiente_en_la_vista_del_programa(engine, media):
    """Como SoHo: el tip va encima de la canción que pisa; en el programa no ocupa una fila."""
    a = add_audio(engine, media, "A", seconds=60.0)
    b = add_audio(engine, media, "B", seconds=60.0)
    c = add_audio(engine, media, "C", seconds=60.0)
    tip = add_audio(engine, media, "TIP", tipo="Tips", seconds=6.0)
    d = add_audio(engine, media, "D", seconds=60.0)
    _plan(engine, _p_audio(b, titulo="B"), _p_audio(tip, "Tips", tip_modo="puente", titulo="TIP"), _p_audio(c, titulo="C"),
          _p_audio(tip, "Tips", tip_modo="intro_final", titulo="TIP"), _p_audio(d, titulo="D"))
    engine._start({"track": engine._fetch_track(a), "origen": "manual"})
    filas = engine.programa()["items"]
    assert [i["titulo"] for i in filas] == ["B", "C", "D"]         # sin filas de tip
    assert filas[0]["tip"]["modo"] == "puente"                     # el puente pisa a B (la que sale)
    assert "tip" not in filas[1] and filas[2]["tip"]["modo"] == "intro_final"   # el de intro pisa a D (la que entra)


def test_tip_pendiente_se_ve_sobre_la_primera_cancion_de_la_cola(engine, media):
    a = add_audio(engine, media, "A", seconds=60.0)
    b = add_audio(engine, media, "B", seconds=60.0)
    tip = add_audio(engine, media, "TIP", tipo="Tips", seconds=6.0)
    engine._mode = "manual"
    engine.queue_track(a); engine.queue_track(b); _tip_manual(engine, tip, "intro_inicio")
    cola = engine.programa()["cola"]
    assert cola[0]["tip"]["modo"] == "intro_inicio" and "tip" not in cola[1]


def test_puente_que_aun_no_sonaba_no_sale_si_el_operador_salta(engine, media):
    a = add_audio(engine, media, "A", seconds=60.0, cue_mezcla=50.0)
    b = add_audio(engine, media, "B", seconds=60.0)
    tip = add_audio(engine, media, "TIP 6", tipo="Tips", seconds=6.0)
    _plan(engine, _p_audio(tip, "Tips", tip_modo="puente"), _p_audio(b))
    engine._start({"track": engine._fetch_track(a), "origen": "auto"})
    _al(engine, 20.0)
    engine.skip(); engine._drain_commands()
    assert engine._current_track["id"] == b and engine._tip is None


def test_la_musica_baja_suave_bajo_el_tip_y_sube_antes_de_que_termine(engine, media):
    """Queja del operador: bajaba mucho y subía tarde. Ahora: -7 dB y la subida empieza ~1,3 s antes del final del tip."""
    assert engine._tip_duck == 0.45 > engine._voz_duck             # más suave que bajo una voz
    tema = add_audio(engine, media, "TEMA", seconds=60.0, cue_mezcla=55.0)
    tip = add_audio(engine, media, "TIP 7", tipo="Tips", seconds=7.0)
    engine._mode = "manual"
    vols = _volumenes(engine)
    _tip_manual(engine, tip, "intro_inicio")
    engine._start({"track": engine._fetch_track(tema), "origen": "manual"})
    canal = engine._active_canal
    pleno = engine._master_volume * engine._gain_of(engine._current_track)
    assert (canal, round(pleno * 0.45, 3)) in vols and engine._tip["duck"]
    antes = len(vols)
    engine._audio.set_position_sec(CANAL_TIP, 4.0)                 # aún falta mucho: sigue abajo
    engine._tick()
    assert engine._tip["duck"] and len(vols) == antes
    engine._audio.set_position_sec(CANAL_TIP, 5.9)                 # quedan 1,1 s: empieza a subir mientras el tip termina
    engine._tick()
    assert not engine._tip["duck"] and vols[-1] == (canal, round(pleno, 3))
    assert engine._tip["estado"] == "sonando"                      # el tip aún suena


def test_en_un_puente_el_cambio_de_cancion_es_corto_para_que_se_oiga_la_siguiente(engine, media):
    """Con 4 s de crossfade la siguiente casi no se oía bajo el tip (parecía un tip suelto en los últimos segundos)."""
    a = add_audio(engine, media, "A", seconds=40.0, cue_mezcla=36.5)
    b = add_audio(engine, media, "B", seconds=40.0, cue_intro=10.0)
    tip = add_audio(engine, media, "TIP 6", tipo="Tips", seconds=6.0)
    ms = []
    real = engine._audio.slide_volume
    engine._audio.slide_volume = lambda c, v, m: (ms.append((c, round(v, 2), m)), real(c, v, m))[1]
    assert engine._crossfade_secs == 4.0
    _plan(engine, _p_audio(tip, "Tips", tip_modo="puente"), _p_audio(b))
    engine._start({"track": engine._fetch_track(a), "origen": "auto"})
    _al(engine, 33.5)                                              # arranca el tip (mezcla 36.5 − 3)
    canal_a = engine._active_canal
    _al(engine, 36.5)                                              # entra B con el tip encima
    canal_b = engine._active_canal
    assert engine._current_track["id"] == b
    entrada_b = [m for c, v, m in ms if c == canal_b][0]
    salida_a = [m for c, v, m in ms[::-1] if c == canal_a and v == 0.0][0]
    assert entrada_b <= int(engine.TIP_XFADE * 1000)               # B ya está a su nivel en la 1.ª mitad del cruce
    assert salida_a == int(engine.TIP_XFADE * 1000)                # A se va en 1,5 s, no en los 4 s normales
    assert engine._tip["estado"] == "sonando"


def test_sin_puente_el_crossfade_sigue_siendo_el_normal(engine, media):
    a = add_audio(engine, media, "A", seconds=40.0, cue_mezcla=36.5)
    b = add_audio(engine, media, "B", seconds=40.0)
    ms = []
    real = engine._audio.slide_volume
    engine._audio.slide_volume = lambda c, v, m: (ms.append((c, round(v, 2), m)), real(c, v, m))[1]
    _plan(engine, _p_audio(b))
    engine._start({"track": engine._fetch_track(a), "origen": "auto"})
    _al(engine, 36.5)
    assert any(m == 4000 for c, v, m in ms)                        # crossfade de 4 s intacto fuera de los puentes


def test_la_cancion_que_entra_arranca_a_su_volumen_sin_subir_en_4_segundos(engine, media):
    """Queja del operador: «cuando inicia una canción empieza baja y después sube»."""
    a = add_audio(engine, media, "A", seconds=40.0, cue_mezcla=36.5)
    b = add_audio(engine, media, "B", seconds=40.0)
    ms = []
    real = engine._audio.slide_volume
    engine._audio.slide_volume = lambda c, v, m: (ms.append((c, round(v, 2), m)), real(c, v, m))[1]
    _plan(engine, _p_audio(b))
    engine._start({"track": engine._fetch_track(a), "origen": "auto"})
    _al(engine, 36.5)
    canal_b = engine._active_canal
    entrada = [(v, m) for c, v, m in ms if c == canal_b][0]
    assert entrada[0] == round(engine._target_volume(engine._current_track), 2) and entrada[1] == 500


# ── Studio: lo que se ve en la lista del programa y en la tanda ─────────────

def test_el_programa_trae_genero_rotacion_y_anio_de_cada_cancion(engine, media):
    a = add_audio(engine, media, "A", seconds=60.0)
    b = add_audio(engine, media, "B", seconds=60.0, subgenero="SALSA", categoria="EXITO", fecha_lanzamiento="2019-05-03")
    c = add_audio(engine, media, "C", seconds=60.0, subgenero="POP", categoria="NUEVO")
    engine._mode = "manual"
    engine.queue_track(c); engine._drain_commands()
    _plan(engine, _p_audio(b, titulo="B"))
    engine._start({"track": engine._fetch_track(a), "origen": "manual"})
    prog = engine.programa()
    assert prog["items"][0]["genero"] == "SALSA" and prog["items"][0]["rotacion"] == "EXITO" and prog["items"][0]["anio"] == "2019"
    assert prog["cola"][0]["genero"] == "POP" and prog["cola"][0]["rotacion"] == "NUEVO" and prog["cola"][0]["anio"] == ""


def test_el_estado_muestra_las_cunas_de_la_tanda_cargada(engine, media):
    from collections import deque
    spot = add_audio(engine, media, "CUNA", tipo="Cuña", seconds=10.0)
    engine._priority_queue = deque([{"pauta_id": 1, "cliente": "FARMACIA", "audio_id": spot, "duracion": 30.4, "slot": "x"},
                                    {"pauta_id": 2, "cliente": "BANCO", "audio_id": spot, "duracion": 20, "slot": "x"}])
    st = engine.status
    assert [c["cliente"] for c in st["cunas_en_cola"]] == ["FARMACIA", "BANCO"] and st["cunas_en_cola"][0]["duracion"] == 30
    engine._priority_queue.clear()
    assert engine.status["cunas_en_cola"] == []


def test_cada_cuna_de_la_tanda_dice_su_audio_para_poder_escucharla(engine, media):
    """El Studio escucha cada cuña de la tanda con el Play de preescucha: necesita su audio_id."""
    from datetime import datetime
    from backend.audio.planner import Planner
    pid = add_pauta(engine, media, "FARMACIA")
    conn = db(engine)
    audio_id = conn.execute("SELECT audio_id FROM pautas WHERE id = ?", (pid,)).fetchone()[0]
    cunas = Planner(engine._db).lista_tanda(conn, datetime(2026, 10, 12, 10), 15, [15, 45])
    assert [c["audio_id"] for c in cunas] == [audio_id]
