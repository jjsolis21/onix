"""Reparto de pautas en tandas (funciones puras, sin motor ni BD)."""

from backend.audio.audio_engine import ordenar_tanda, parse_minutos, planificar_tandas


def P(pid, rubro=""):
    return {"pauta_id": pid, "rubro": rubro}


def test_parse_minutos():
    assert parse_minutos("15,45") == [15, 45]
    assert parse_minutos(" 45; 15 ,15 ") == [15, 45]
    assert parse_minutos("0 30") == [0, 30]
    # Inválidos se ignoran; sin ninguno válido queda una sola tanda en :00
    assert parse_minutos("75,abc") == [0]
    assert parse_minutos("") == [0]


def test_mismo_rubro_va_a_tandas_distintas():
    plan = planificar_tandas(
        [P(1, "BANCO"), P(2, "BANCO"), P(3, "FARMACIA"), P(4, "FARMACIA")], [15, 45]
    )
    for minuto in (15, 45):
        rubros = [p["rubro"] for p in plan[minuto]]
        assert sorted(rubros) == ["BANCO", "FARMACIA"]


def test_reparto_equilibrado_y_prioridad_en_la_primera_tanda():
    plan = planificar_tandas([P(i) for i in range(1, 6)], [15, 45])
    assert [p["pauta_id"] for p in plan[15]] == [1, 3, 5]
    assert [p["pauta_id"] for p in plan[45]] == [2, 4]


def test_orden_dentro_de_la_tanda_evita_rubros_seguidos():
    orden = ordenar_tanda([P(1, "BANCO"), P(2, "BANCO"), P(3, "AUTOS")])
    assert [p["pauta_id"] for p in orden] == [1, 3, 2]


def test_rubro_unico_no_se_puede_separar_pero_no_se_pierde():
    plan = planificar_tandas([P(1, "BANCO"), P(2, "BANCO"), P(3, "BANCO")], [0])
    assert [p["pauta_id"] for p in plan[0]] == [1, 2, 3]


def test_fijadas_abren_y_cierran_la_tanda():
    orden = ordenar_tanda([
        {**P(1, "BANCO"), "posicion": 2}, P(2, "AUTOS"),
        {**P(3, "BANCO"), "posicion": 1}, P(4, "BANCO"),
    ])
    ids = [p["pauta_id"] for p in orden]
    assert ids[0] == 3 and ids[-1] == 1
    # tras la fijada BANCO no viene otro BANCO
    assert ids == [3, 2, 4, 1]


def test_orden_manual_va_despues_de_las_fijadas_primero():
    from backend.audio.audio_engine import ordenar_tanda
    orden = ordenar_tanda([P(1), {**P(2), "orden": 1}, {**P(3), "orden": 0},
                           {**P(4), "posicion": 1}, {**P(5), "posicion": 2}])
    assert [p["pauta_id"] for p in orden] == [4, 3, 2, 1, 5]


def test_bloque_al_cierre_termina_en_punto():
    from backend.audio.audio_engine import inicio_tanda
    assert inicio_tanda(0, 600, True) == 50 * 60      # 10 min de cuñas → :50
    assert inicio_tanda(30, 120, True) == 28 * 60     # termina a la media
    assert inicio_tanda(15, 600, False) == 15 * 60    # normal: arranca en su minuto
    assert inicio_tanda(5, 900, True) == 0            # nunca antes del inicio de la hora


def test_versiones_rotan():
    from backend.audio.audio_engine import version_rotada
    assert [version_rotada([7, 8, 9], n) for n in range(5)] == [7, 8, 9, 7, 8]
    assert version_rotada([], 3) is None
