"""
publicar.py — Ónix FM
«Publicar metadata» (como Soho): qué texto se muestra al aire (RDS, streaming, pantallas) mientras suena un audio.

    meta_modo   ''              según el tipo: la música y los programas actualizan; cuñas, jingles y voces no
                'actualizar'    publica este audio
                'no_actualizar' se queda lo que ya se mostraba (no cambia el texto)
    meta_titulo / meta_artista  vacío o «AUTO» = los del propio audio
    meta_extra1 / meta_extra2   textos libres (p. ej. el eslogan o la web)

Esto solo CALCULA el texto. Enviarlo al RDS o al encoder queda para cuando se conecte el equipo.
"""

ACTUALIZAN_POR_DEFECTO = ("Musica", "Programa")


def _limpio(v) -> str:
    return " ".join(str(v or "").split())


def modo_efectivo(tipo_audio, meta_modo) -> str:
    modo = _limpio(meta_modo).lower()
    if modo in ("actualizar", "no_actualizar"):
        return modo
    return "actualizar" if (tipo_audio or "Musica") in ACTUALIZAN_POR_DEFECTO else "no_actualizar"


def texto_publicado(audio) -> dict:
    """`audio`: fila o dict de la tabla audios. Devuelve lo que se publicaría si este audio suena."""
    g = (lambda k: audio[k] if k in audio.keys() else None) if hasattr(audio, "keys") else audio.get
    titulo = _limpio(g("meta_titulo"))
    artista = _limpio(g("meta_artista"))
    if not titulo or titulo.upper() == "AUTO":
        titulo = _limpio(g("titulo"))
    if not artista or artista.upper() == "AUTO":
        artista = _limpio(g("artista"))
    e1, e2 = _limpio(g("meta_extra1")), _limpio(g("meta_extra2"))
    return {
        "actualizar": modo_efectivo(g("tipo_audio"), g("meta_modo")) == "actualizar",
        "titulo": titulo, "artista": artista, "extra1": e1, "extra2": e2,
        "texto": " - ".join(x for x in (artista, titulo) if x),
    }
