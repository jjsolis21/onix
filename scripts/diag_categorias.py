"""
Script de diagnóstico para filtros de categorías.
Corre FUERA de pytest (usa la DB real).
Invocar con: python scripts/diag_categorias.py
"""
import os
import sys
import json
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DB_PATH = ROOT / "backend" / "api" / "radio_core.db"
os.environ.setdefault("RADIO_DB_PATH", str(DB_PATH))
os.environ.setdefault("RADIO_MEDIA_ROOT", str(ROOT / "backend" / "media"))
os.environ.setdefault("ONIX_AUDIO_BACKEND", "mock")
os.environ.setdefault("ONIX_HOSTS_LOCALES", "testclient")

from fastapi.testclient import TestClient
from backend.api.api_server import app  # noqa: E402

client = TestClient(app, client=("127.0.0.1", 50000))

conn = sqlite3.connect(str(DB_PATH))
conn.row_factory = sqlite3.Row
c = conn.cursor()

print("=" * 60)
print("1. AUDIOS POR TIPO")
print("=" * 60)
for r in c.execute("SELECT tipo_audio, COUNT(*) n FROM audios GROUP BY tipo_audio").fetchall():
    print(f"   {r['tipo_audio']}: {r['n']}")

print("\n2. CATEGORÍAS DE MÚSICA (a.categoria)")
for r in c.execute("SELECT COALESCE(categoria,'<NULL>') cat, COUNT(*) n FROM audios WHERE tipo_audio='Musica' GROUP BY categoria ORDER BY n DESC").fetchall():
    print(f"   {r['cat']}: {r['n']}")

print("\n3. SUBGÉNERO DE MÚSICA (a.subgenero)")
for r in c.execute("SELECT COALESCE(subgenero,'<NULL>') sub, COUNT(*) n FROM audios WHERE tipo_audio='Musica' GROUP BY subgenero ORDER BY n DESC").fetchall():
    print(f"   {r['sub']}: {r['n']}")

print("\n4. RANGO HISTORIAL")
r = c.execute("SELECT MIN(iniciado_en) desde, MAX(iniciado_en) hasta, COUNT(*) n FROM historial_emision").fetchone()
print(f"   Desde: {r['desde']}  Hasta: {r['hasta']}  Registros: {r['n']}")

print("\n5. HISTORIAL ÚLTIMAS 24H por subgenero/categoria")
desde_24h = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
for r in c.execute("""
    SELECT COALESCE(a.subgenero,'<NULL>') sub, COALESCE(a.categoria,'<NULL>') cat, COUNT(*) n
    FROM historial_emision h
    JOIN audios a ON a.id = h.audio_id
    WHERE h.iniciado_en >= ? AND a.tipo_audio = 'Musica'
    GROUP BY a.subgenero, a.categoria ORDER BY n DESC
""", (desde_24h,)).fetchall():
    print(f"   sub={r['sub']}  cat={r['cat']}: {r['n']}")

print("\n" + "=" * 60)
print("6. ENDPOINT filtros-categorias")
print("=" * 60)
resp = client.get("/api/v1/stats/filtros-categorias")
print(f"   STATUS: {resp.status_code}")
cats = resp.json()
print(json.dumps(cats, indent=2, ensure_ascii=False))

print("\n" + "=" * 60)
print("7. TOP40 SIN FILTRO")
print("=" * 60)
for period in ("day", "week"):
    r = client.get(f"/api/v1/stats/top40?period={period}&tipo=musica")
    data = r.json()
    print(f"   period={period}: {len(data)} items  status={r.status_code}")

print("\n" + "=" * 60)
print("8. TOP40 CON FILTROS DE GÉNERO (period=day)")
print("=" * 60)
generos = cats["musica"]["generos"]
for g in generos:
    url = f"/api/v1/stats/top40?period=day&tipo=musica&categoria=genero:{g}"
    r = client.get(url)
    data = r.json()
    print(f"   genero:{g!r} => {len(data)} items | status={r.status_code}")
    if data:
        print(f"     Ejemplo: {data[0]['titulo']} | cat={data[0]['categoria']}")

print("\n9. TOP40 CON FILTROS DE GÉNERO (period=week)")
for g in generos:
    url = f"/api/v1/stats/top40?period=week&tipo=musica&categoria=genero:{g}"
    r = client.get(url)
    data = r.json()
    print(f"   genero:{g!r} => {len(data)} items")

print("\n10. TOP40 CON FILTROS DE ROTACIÓN (period=week)")
rotaciones = cats["musica"]["rotaciones"]
for rot in rotaciones:
    url = f"/api/v1/stats/top40?period=week&tipo=musica&categoria=rotacion:{rot}"
    r = client.get(url)
    data = r.json()
    print(f"   rotacion:{rot!r} => {len(data)} items")

print("\n" + "=" * 60)
print("11. DIAGNÓSTICO DIRECTO EN DB PARA SALSA (week)")
print("=" * 60)
desde_week = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
hasta_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
rows = c.execute("""
    SELECT a.titulo, a.categoria, a.subgenero, COUNT(*) n
    FROM historial_emision h
    JOIN audios a ON a.id = h.audio_id
    WHERE h.iniciado_en >= ? AND h.iniciado_en <= ?
      AND (a.tipo_audio = 'Musica' OR h.tipo = 'musica')
      AND (LOWER(COALESCE(a.subgenero,'')) = LOWER('SALSA')
           OR LOWER(COALESCE(a.categoria,'')) = LOWER('SALSA'))
    GROUP BY a.id ORDER BY n DESC LIMIT 5
""", (desde_week, hasta_now)).fetchall()
print(f"   Filas directas SALSA (week): {len(rows)}")
for r in rows:
    print(f"     {r['titulo']} | cat={r['categoria']} | sub={r['subgenero']} | n={r['n']}")

print("\n" + "=" * 60)
print("12. IDENTIFICADORES (period=week)")
print("=" * 60)
tipos = cats["ident"]["tipos"]
for t in tipos:
    url = f"/api/v1/stats/top40?period=week&tipo=ident&categoria=tipo:{t['id']}"
    r = client.get(url)
    data = r.json()
    print(f"   tipo:{t['id']!r} => {len(data)} items")

ident_cats = cats["ident"]["categorias"]
for ic in ident_cats:
    url = f"/api/v1/stats/top40?period=week&tipo=ident&categoria={ic}"
    r = client.get(url)
    data = r.json()
    print(f"   cat:{ic!r} => {len(data)} items")

print("\n¡Diagnóstico completo!")
