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
