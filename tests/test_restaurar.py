"""Restaurar la base de datos desde un respaldo (scripts/restaurar_respaldo.py, backend/respaldo_maria.py)."""

import gzip

import pytest

from backend.database import get_connection
from backend.respaldo_maria import restaurar, verificar, volcar


def _poner(clave, valor):
    c = get_connection()
    c.execute("DELETE FROM config WHERE clave = ?", (clave,))
    c.execute("INSERT INTO config (clave, valor) VALUES (?, ?)", (clave, valor))
    c.commit()
    c.close()


def _leer(clave):
    c = get_connection()
    r = c.execute("SELECT valor FROM config WHERE clave = ?", (clave,)).fetchone()
    c.close()
    return r[0] if r else None


def test_restaura_y_guarda_la_base_anterior(tmp_db, tmp_path):
    _poner("marca", "AYER")
    copia = tmp_path / "onix-2026-10-12_0300.sql.gz"
    volcar(copia)
    _poner("marca", "HOY")                                   # cambia después de la copia
    anterior = restaurar(copia, tmp_path / "seguridad")
    assert _leer("marca") == "AYER"                          # volvió a lo de la copia
    assert anterior.is_file() and anterior.name.startswith("antes-de-restaurar-")
    restaurar(anterior, tmp_path / "seguridad2")             # y lo que había antes se puede recuperar
    assert _leer("marca") == "HOY"


def test_los_datos_raros_vuelven_identicos(tmp_db, tmp_path):
    raro = "O'Brien \"x\"\nlínea 2 \\ ñandú 100% ; DROP TABLE audios; --"
    c = get_connection()
    c.execute("INSERT INTO audios (titulo, artista, duracion, archivo_path, tipo_audio, subgenero)"
              " VALUES (?, ?, 61.5, 'library/raro.mp3', 'Musica', NULL)", (raro, "Café ♪"))
    c.commit()
    c.close()
    copia = tmp_path / "onix-2026-10-12_0300.sql.gz"
    volcar(copia)
    c = get_connection()
    c.execute("DELETE FROM audios")
    c.commit()
    c.close()
    restaurar(copia, tmp_path)
    c = get_connection()
    f = c.execute("SELECT titulo, artista, duracion, subgenero, busq FROM audios WHERE archivo_path = 'library/raro.mp3'").fetchone()
    c.close()
    assert f["titulo"] == raro and f["artista"] == "Café ♪" and float(f["duracion"]) == 61.5
    assert f["subgenero"] is None
    assert f["busq"].startswith("o'brien")                   # el trigger de búsqueda quedó restaurado y funcionando


def test_no_restaura_una_copia_cortada_ni_ajena(tmp_db, tmp_path):
    _poner("marca", "HOY")
    buena = tmp_path / "onix-2026-10-12_0300.sql.gz"
    volcar(buena)
    # cortada a la mitad (disco lleno / apagón)
    datos = buena.read_bytes()
    cortada = tmp_path / "onix-2026-10-12_0400.sql.gz"
    cortada.write_bytes(datos[: len(datos) // 2])
    # gzip válido pero sin el cierre
    sin_cierre = tmp_path / "onix-2026-10-12_0500.sql.gz"
    sin_cierre.write_bytes(gzip.compress(gzip.decompress(datos).rsplit(b"-- ONIX-FIN", 1)[0]))
    # gzip válido pero de otra cosa
    ajena = tmp_path / "onix-2026-10-12_0600.sql.gz"
    ajena.write_bytes(gzip.compress(b"SELECT 1;"))
    basura = tmp_path / "onix-2026-10-12_0700.sql.gz"
    basura.write_bytes(b"no soy gzip" * 100)
    vieja = tmp_path / "onix-2026-10-12_0800.db"
    vieja.write_bytes(b"SQLite format 3")
    for mala in (cortada, sin_cierre, ajena, basura, vieja, tmp_path / "no-existe.sql.gz"):
        with pytest.raises(ValueError):
            restaurar(mala, tmp_path / "seg")
    assert _leer("marca") == "HOY"                           # la base real no se tocó
    assert not (tmp_path / "seg").exists()                   # ni siquiera se generó respaldo de seguridad


def test_verificar_acepta_un_respaldo_real(tmp_db, tmp_path):
    copia = tmp_path / "onix-2026-10-12_0300.sql.gz"
    info = volcar(copia)
    assert verificar(copia) == info and info["tablas"] > 10
