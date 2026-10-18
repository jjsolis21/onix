"""
utils_musica.py — Utilidades de lógica musical para Ónix FM
"""
import re
from typing import Optional, Union


def calcular_epoca(anio: Union[int, str, None]) -> Optional[str]:
    """
    Calcula la época / década musical basándose en el año.

    Lógica:
      - Si el año es >= 2020: Retorna "Actual"
      - Si el año está entre 2010 y 2019: Retorna "2010s"
      - Si el año está entre 2000 y 2009: Retorna "2000s"
      - Si el año está entre 1990 y 1999: Retorna "90s"
      - Si el año está entre 1980 y 1989: Retorna "80s"
      - Si el año está entre 1970 y 1979: Retorna "70s"
      - Si el año es < 1970 (y > 0): Retorna "Oldies"
      - Si el año viene vacío o inválido: Retorna None
    """
    if anio is None:
        return None

    val_str = str(anio).strip()
    if not val_str:
        return None

    # Extraer los 4 dígitos del año (soporta "1994", "1994-05-12", "c. 1985")
    m = re.search(r'\b(19\d{2}|20\d{2})\b', val_str)
    if m:
        num = int(m.group(1))
    else:
        # Fallback para años menores a 1900 o formatos directos de 4 dígitos
        digits = re.sub(r'\D', '', val_str)[:4]
        if not digits:
            return None
        try:
            num = int(digits)
        except (ValueError, TypeError):
            return None

    if num <= 0:
        return None
    elif num >= 2020:
        return "Actual"
    elif 2010 <= num <= 2019:
        return "2010s"
    elif 2000 <= num <= 2009:
        return "2000s"
    elif 1990 <= num <= 1999:
        return "90s"
    elif 1980 <= num <= 1989:
        return "80s"
    elif 1970 <= num <= 1979:
        return "70s"
    else:  # num < 1970
        return "Oldies"
