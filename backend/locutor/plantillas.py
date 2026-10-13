"""Guiones de emergencia sin LLM.

Si el modelo de lenguaje falla (cuota agotada, red caída), el locutor no se queda mudo en lo básico: estas
plantillas solo combinan datos que ya están en el contexto, así que no pueden inventar nada.
Las piezas que requieren redacción (noticias, publicidad, saludos) no tienen plantilla: esas se omiten.
"""
_UNIDADES = ("cero uno dos tres cuatro cinco seis siete ocho nueve diez once doce trece catorce quince dieciséis "
             "diecisiete dieciocho diecinueve veinte veintiuno veintidós veintitrés veinticuatro veinticinco veintiséis "
             "veintisiete veintiocho veintinueve").split()
_DECENAS = {30: "treinta", 40: "cuarenta", 50: "cincuenta"}


def numero(n: int) -> str:
    """0-59 en palabras."""
    if n < 30:
        return _UNIDADES[n]
    decena, unidad = n - n % 10, n % 10
    return _DECENAS[decena] + (f" y {_UNIDADES[unidad]}" if unidad else "")


def hora_hablada(hora: int, minuto: int, momento: str) -> str:
    h = hora % 12 or 12
    base = ("Es la una" if h == 1 else f"Son las {numero(h)}")
    if minuto:
        base += f" y {numero(minuto)}"
    return f"{base} de la {momento}"


_CORTA_PUENTE = ("Escuchas a {a_artista} con {a_titulo}. Sigue {s_artista} con {s_titulo}.",
                 "Suena {a_titulo}, de {a_artista}. Y ahora, {s_artista} con {s_titulo}.",
                 "{a_artista} en {emisora}. A continuación, {s_titulo}, de {s_artista}.")
_CORTA = ("Sonó {a_artista} con {a_titulo}. Ahora, {s_artista} con {s_titulo}.",
          "Fue {a_titulo}, de {a_artista}. Seguimos con {s_artista} y {s_titulo}.",
          "En {emisora} escuchaste a {a_artista}. Viene {s_titulo}, de {s_artista}.")
_SOLO_SIGUIENTE = ("En {emisora}, ahora suena {s_artista} con {s_titulo}.",
                   "Seguimos en {emisora} con {s_titulo}, de {s_artista}.")
_IDENTIFICACION = ("Estás en {emisora}.", "Esto es {emisora}. Seguimos con más música.",
                   "Gracias por sintonizar {emisora}.")
_SALUDO = {"mañana": "Buenos días", "tarde": "Buenas tardes", "noche": "Buenas noches", "madrugada": "Buenas noches"}


def guion_de_emergencia(tipo: str, persona: dict, ctx, variante: int = 0) -> str | None:
    """Texto listo para la voz, o None si ese tipo no se puede decir sin redactar."""
    datos = {"emisora": ctx.emisora, "dj": persona["nombre"]}
    if ctx.anterior:
        datos.update(a_artista=ctx.anterior["artista"], a_titulo=ctx.anterior["titulo"])
    if ctx.siguiente:
        datos.update(s_artista=ctx.siguiente["artista"], s_titulo=ctx.siguiente["titulo"])

    def elegir(opciones):
        return opciones[variante % len(opciones)].format(**datos)

    if tipo in ("corta", "contenido") and ctx.siguiente:
        if ctx.anterior:
            return elegir(_CORTA_PUENTE if ctx.sobre_musica else _CORTA)
        return elegir(_SOLO_SIGUIENTE)
    if tipo == "identificacion":
        return elegir(_IDENTIFICACION)
    if tipo == "hora_clima" and ctx.hora_hablada:
        return f"{ctx.hora_hablada} en {ctx.emisora}." + (f" Tenemos {ctx.clima}." if ctx.clima else "")
    if tipo in ("apertura", "saludo") and ctx.momento:
        lugar = f", {ctx.ciudad}" if ctx.ciudad else ""
        return f"{_SALUDO[ctx.momento]}{lugar}. Te acompaña {persona['nombre']} en {ctx.emisora}. Seguimos con más música."
    return None
