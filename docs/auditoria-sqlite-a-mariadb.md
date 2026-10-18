# Auditoría: rastros de SQLite en Ónix FM (2026-10-18)

Objetivo: que el sistema hable MariaDB de forma directa, sin depender de que la capa de
compatibilidad (`backend/database.py`) traduzca SQL de SQLite al vuelo.

## Inventario (conteo de coincidencias)

| Zona | Coincidencias | Qué hay |
|---|---|---|
| `backend/` | ~357 | SQL de SQLite, tipos `sqlite3.Row/Connection`, excepciones `sqlite3.Error` |
| `scripts/` | ~76 | `init_db.py` (SQLite completo), migradores, diagnósticos |
| `tests/` | varios | `sqlite3.connect` directo (ya migrados los principales) |

## Clasificación

### A. Se traduce en `database.py` pero debe escribirse en MariaDB (la mayoría)
- `datetime('now', ...)`, `date('now', ...)`, `strftime(...)`: reemplazar por `NOW()`, `DATE_SUB`, `DATE_FORMAT`.
- `INSERT OR IGNORE` / `INSERT OR REPLACE`: `INSERT IGNORE` / `REPLACE INTO` (o `ON DUPLICATE KEY`).
- `ON CONFLICT(...) DO UPDATE SET x = excluded.x`: `ON DUPLICATE KEY UPDATE x = VALUES(x)`.
- `COLLATE NOCASE`: ya no hace falta (la colación de las tablas es `utf8mb4_unicode_ci`).
- `json_set`/`json_extract` (sin mayúsculas): `JSON_SET`/`JSON_UNQUOTE(JSON_EXTRACT(...))`.
- Parámetros `?` mezclados con texto literal: convertir a placeholders `%s` directos.
- `sqlite_master`, `PRAGMA table_info`: `information_schema`.

Riesgo: cada una de estas traducciones es una expresión regular. Una mal escrita da un
error solo en un caso concreto. Por eso se reemplazan en el código y se eliminan de la capa.

### B. SQLite que sí se mantiene (legítimo)
- Importar la configuración del Locutor **anterior** (AzuraCast), que era un archivo SQLite
  externo: `backend/locutor/almacen.py` (lectura en modo solo lectura).
- Se conserva esa lectura, aislada y explícita.

### C. SQLite que se elimina
- `backend/database.py`: `_adapt_sql` (toda la traducción), `_conexion_sqlite`, `executescript`
  que traga errores, y el modo `DB_BACKEND=sqlite`.
- `scripts/init_db.py`: solo sirve para el archivo SQLite histórico. Las migraciones nuevas van
  ya en `backend/migraciones_maria.py`. Se conserva como referencia, marcado como histórico.
- `scripts/migrar_epocas.py`, `scripts/diag_categorias.py`: solo SQLite, obsoletos.
- `backend/api/migrate.py`: abre `DB_PATH` con `sqlite3`; revisar si aún se usa.
- `ONIX_TEST_DB=sqlite` en `tests/conftest.py`: ya no es necesario.

### D. Revisar uno por uno (riesgo alto)
- `backend/api/api_server.py` (77 coincidencias): concentra la mayoría. Incluye valores que se
  pasan como *texto* `"datetime('now')"` (líneas ~1454–1461) que luego se interpolan. Hay que
  reescribirlos como expresiones SQL reales.
- `backend/api/reportes.py` (31) y `backend/api/relojes.py` (25): fechas y agregados por hora.
- `backend/audio/audio_engine.py` (14): `datetime('now','localtime')` en el registro de emisiones
  (zona horaria local: MariaDB debe usar la misma).

## Plan propuesto (en fases, cada una con pruebas)

1. **Fase 1 — Inventario por archivo y lista de SQL a reescribir** (este documento).
2. **Fase 2 — Reescritura en MariaDB nativo**, archivo por archivo, empezando por los que tienen
   más coincidencias. Cada archivo: cambiar SQL, correr sus pruebas, commit.
3. **Fase 3 — Quitar la capa de traducción**: `_adapt_sql` y el modo SQLite de `database.py`.
   Si algo se rompe, aparece en la prueba, no en producción.
4. **Fase 4 — Limpiar scripts y pruebas obsoletos** (migradores SQLite, `ONIX_TEST_DB=sqlite`).
5. **Fase 5 — Verificación**: suite completa en paralelo varias veces, y una prueba de
   concurrencia real con dos conexiones escribiendo la misma fila.

## Lo que NO se hace
- No se cambia el esquema de producción en esta auditoría.
- No se borra ningún dato.

## Estado al 2026-10-18 (fases 2 y 3 hechas)

- **Backend:** sin SQL de SQLite. `sqlite3` solo queda donde se lee el archivo del Locutor anterior (AzuraCast):
  `backend/locutor/almacen.py` y `backend/api/locutor.py` (comentado en cada import).
- **`backend/database.py`:** la capa de traducción se redujo a lo que necesita el controlador: marcadores `?` y `:nombre`,
  y el escapado de `%` dentro de textos cuando hay parámetros. Un `datetime('now')`, `INSERT OR IGNORE` o `PRAGMA` ahora da
  error de sintaxis de MariaDB en la prueba, en vez de traducirse en silencio. Se quitó el modo SQLite (`DB_BACKEND`,
  `ONIX_TEST_DB`), `executescript` y la herencia de excepciones de `sqlite3`.
- **Bugs reales hallados por el camino:** `||` como concatenación (restricción por día de las canciones y separador de sección),
  `datetime('now','-N minutes')` que perdía el desplazamiento (noticias del Locutor), colaciones que distinguían mayúsculas,
  ids sin AUTO_INCREMENT, triggers de búsqueda ausentes, `duracion` entera, respaldo que copiaba el SQLite viejo.
- **Pruebas:** 349 pasan, 3 corridas seguidas idénticas (~95 s con 4 workers).

## Pendiente (fase 4)
- `scripts/init_db.py` sigue siendo SQLite (histórico) y se ejecuta al arrancar sobre el archivo viejo; una instalación nueva no
  tiene todavía un creador de esquema para MariaDB (hoy depende de `scripts/migrar_a_mariadb.py`).
- `scripts/migrar_epocas.py` y `scripts/diag_categorias.py`: solo SQLite, obsoletos.
- `Planner(DB_PATH)` / `AudioEngine(db_path)`: pasan una ruta que MariaDB ignora; quitar el parámetro.
- `backend/esquema_extra.py`: DDL de SQLite que solo usa `init_db.py`; MariaDB lo recibe por `migraciones_maria.py`.
