"""
backend/database.py — Ónix FM Database Configuration & MariaDB Adapter
Configura la conexión usando SQLAlchemy y PyMySQL para MariaDB 12.3+.
Proporciona:
  1. Motor SQLAlchemy (`engine`), `SessionLocal`, y `Base`.
  2. Dependencia FastAPI estándar (`get_db`) que entrega conexión MariaDB compatible.
  3. Capa de compatibilidad `get_connection()` y `db_session()` que conecta a MariaDB
     traduciendo transparentemente placeholders `?` a `%s`, compatibilidad con DictRow/IndexRow
     y soporte para transacciones y excepciones duales (pymysql + sqlite3).
"""

import os
import re
import logging
import sqlite3
from contextlib import contextmanager
from typing import Generator, Any, Optional, List, Dict

import pymysql
import pymysql.cursors
import pymysql.err
from pathlib import Path
from urllib.parse import quote_plus

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base, Session

logger = logging.getLogger("onix.database")

# ---------------------------------------------------------------------------
# Excepciones duales (Heredan de pymysql.err y sqlite3 para máxima compatibilidad)
# ---------------------------------------------------------------------------
class DualError(pymysql.err.Error, sqlite3.Error):
    pass

class DualDatabaseError(pymysql.err.DatabaseError, sqlite3.DatabaseError, DualError):
    pass

class DualOperationalError(pymysql.err.OperationalError, sqlite3.OperationalError, DualDatabaseError):
    pass

class DualIntegrityError(pymysql.err.IntegrityError, sqlite3.IntegrityError, DualDatabaseError):
    pass

IntegrityError = DualIntegrityError
OperationalError = DualOperationalError
DatabaseError = DualDatabaseError
Error = DualError

# ---------------------------------------------------------------------------
# Configuración: variables de entorno o archivo `.env` de la raíz (NUNCA en el código ni en git)
# ---------------------------------------------------------------------------
def cargar_env() -> None:
    """Lee `<raíz>/.env` (CLAVE=valor por línea) sin pisar lo que ya esté en el entorno."""
    ruta = Path(__file__).resolve().parent.parent / ".env"
    if not ruta.is_file():
        return
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, valor = linea.split("=", 1)
        os.environ.setdefault(clave.strip(), valor.strip().strip('"').strip("'"))


cargar_env()

# mariadb = producción (por defecto). sqlite = SOLO las pruebas automáticas (una base temporal por prueba).
DB_BACKEND  = os.getenv("DB_BACKEND", "mariadb").lower()
DB_USER     = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_HOST     = os.getenv("DB_HOST", "localhost")
DB_PORT     = os.getenv("DB_PORT", "3306")
DB_NAME     = os.getenv("DB_NAME", "onix_db")

# URL oficial para SQLAlchemy con MariaDB / PyMySQL
DATABASE_URL = (f"mariadb+pymysql://{quote_plus(DB_USER)}:{quote_plus(DB_PASSWORD)}@{DB_HOST}:{DB_PORT}/{DB_NAME}?charset=utf8mb4")

# ---------------------------------------------------------------------------
# SQLAlchemy Setup
# ---------------------------------------------------------------------------
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,      # Valida que la conexión esté viva antes de usarla
    pool_recycle=3600,       # Recicla conexiones tras 1 hora para evitar timeouts de MariaDB
    pool_size=10,
    max_overflow=20,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# ---------------------------------------------------------------------------
# Row Adapter: Compatible tanto con dict (row['col']) como tupla (row[0])
# ---------------------------------------------------------------------------
class CompatRow(dict):
    """Permite acceso por nombre ('titulo') y por índice (0), igual que sqlite3.Row."""
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        if isinstance(key, slice):                    # fila[1:] devuelve una tupla, como sqlite3.Row
            return tuple(self.values())[key]
        return super().__getitem__(key)

    def __iter__(self):
        """Al iterar o hacer list(row), entrega los valores en orden, igual que sqlite3.Row."""
        return iter(self.values())


# ---------------------------------------------------------------------------
# Cursor y Connection Wrappers para MariaDB
# ---------------------------------------------------------------------------
class MariaDBCursor:
    def __init__(self, raw_cursor: pymysql.cursors.DictCursor):
        self._cur = raw_cursor

    @property
    def lastrowid(self):
        return self._cur.lastrowid

    @property
    def rowcount(self):
        return self._cur.rowcount

    @property
    def description(self):
        return self._cur.description

    def _adapt_sql(self, sql: str, con_args: bool = False) -> str:
        s = sql.strip()
        # Manejar PRAGMAs comunes de SQLite
        if re.match(r"^PRAGMA\s+foreign_keys", s, re.IGNORECASE):
            return "SET foreign_key_checks = 1"
        if re.match(r"^PRAGMA\s+journal_mode", s, re.IGNORECASE):
            return "SELECT 1"
        if re.match(r"^PRAGMA\s+table_info\((.*)\)", s, re.IGNORECASE):
            m = re.match(r"^PRAGMA\s+table_info\((.*)\)", s, re.IGNORECASE)
            tbl = m.group(1).strip("`'\" ")
            return f"DESCRIBE `{tbl}`"

        # Adaptaciones de sintaxis SQLite -> MariaDB
        s = re.sub(r"\bINSERT\s+OR\s+IGNORE\b", "INSERT IGNORE", s, flags=re.IGNORECASE)
        s = re.sub(r"\bINSERT\s+OR\s+REPLACE\s+INTO\b", "REPLACE INTO", s, flags=re.IGNORECASE)
        # SQLite ON CONFLICT(col) DO UPDATE SET col2 = excluded.col2 -> ON DUPLICATE KEY UPDATE col2 = VALUES(col2)
        s = re.sub(r"\bON\s+CONFLICT\s*\([^)]*\)\s*DO\s+UPDATE\s+SET\b", "ON DUPLICATE KEY UPDATE", s, flags=re.IGNORECASE)
        s = re.sub(r"\bexcluded\.(\w+)", r"VALUES(\1)", s, flags=re.IGNORECASE)

        # Reemplazar funciones SQLite comunes: datetime('now', ...) -> MariaDB
        s = re.sub(r"datetime\s*\(\s*['\"]now['\"]\s*(?:,\s*['\"][^'\"]+['\"])?\s*,\s*['\"]-(\d+)\s+days?['\"]\s*\)", r"DATE_SUB(NOW(), INTERVAL \1 DAY)", s, flags=re.IGNORECASE)
        s = re.sub(r"date\s*\(\s*['\"]now['\"]\s*(?:,\s*['\"][^'\"]+['\"])?\s*,\s*['\"]-(\d+)\s+days?['\"]\s*\)", r"DATE_SUB(CURDATE(), INTERVAL \1 DAY)", s, flags=re.IGNORECASE)
        s = re.sub(r"datetime\s*\(\s*['\"]now['\"]\s*(?:,\s*['\"][^'\"]+['\"])?\s*,\s*['\"]-(\d+)\s+hours?['\"]\s*\)", r"DATE_SUB(NOW(), INTERVAL \1 HOUR)", s, flags=re.IGNORECASE)
        s = re.sub(r"datetime\s*\(\s*['\"]now['\"]\s*(?:,\s*['\"][^'\"]+['\"])?\s*,\s*['\"]\+(\d+)\s+days?['\"]\s*\)", r"DATE_ADD(NOW(), INTERVAL \1 DAY)", s, flags=re.IGNORECASE)
        s = re.sub(r"date\s*\(\s*['\"]now['\"]\s*(?:,\s*['\"][^'\"]+['\"])?\s*,\s*['\"]\+(\d+)\s+days?['\"]\s*\)", r"DATE_ADD(CURDATE(), INTERVAL \1 DAY)", s, flags=re.IGNORECASE)
        s = re.sub(r"datetime\s*\(\s*['\"]now['\"][^)]*\)", "NOW()", s, flags=re.IGNORECASE)
        s = re.sub(r"date\s*\(\s*['\"]now['\"][^)]*\)", "CURDATE()", s, flags=re.IGNORECASE)
        s = re.sub(r"CAST\s*\(\s*strftime\s*\(\s*['\"]%H['\"]\s*,\s*([^)]+)\s*\)\s*AS\s+INTEGER\s*\)", r"HOUR(\1)", s, flags=re.IGNORECASE)
        s = re.sub(r"strftime\s*\(\s*['\"]%H['\"]\s*,\s*([^)]+)\s*\)", r"LPAD(HOUR(\1), 2, '0')", s, flags=re.IGNORECASE)
        s = re.sub(r"strftime\s*\(\s*['\"]%Y-%m-%d['\"]\s*,\s*([^)]+)\s*\)", r"DATE(\1)", s, flags=re.IGNORECASE)

        # SQLite «COLLATE NOCASE»: en MariaDB la colación utf8mb4_unicode_ci ya compara sin distinguir mayúsculas
        s = re.sub(r"\s+COLLATE\s+NOCASE\b", "", s, flags=re.IGNORECASE)

        # SQLite sqlite_master -> information_schema.tables
        if "sqlite_master" in s.lower():
            s = re.sub(r"\bsqlite_master\b", "information_schema.tables", s, flags=re.IGNORECASE)
            s = re.sub(r"\bname\b", "table_name", s, flags=re.IGNORECASE)

        # LIKE ... ESCAPE '\' (SQLite): en MariaDB la barra invertida escapa la comilla, hay que duplicarla
        s = re.sub(r"ESCAPE\s+'\\'", "ESCAPE '\\\\\\\\'", s, flags=re.IGNORECASE)

        # Con parámetros, PyMySQL formatea el SQL con «%»: los % literales de las cadenas ('%texto%') se duplican
        if con_args:
            s = re.sub(r"'(?:[^']|'')*'", lambda m: m.group(0).replace("%", "%%"), s)

        # Reemplazar placeholders con nombre estilo SQLite :param por %(param)s si la consulta los usa
        # (solo FUERA de las cadenas entre comillas: un texto como '10:ab' o '¿qué?' no es un parámetro)
        def _fuera(texto: str, fn) -> str:
            partes = re.split(r"('(?:[^']|'')*')", texto)
            return "".join(p if i % 2 else fn(p) for i, p in enumerate(partes))

        s = _fuera(s, lambda t: re.sub(r"(?<!:):([a-zA-Z_][a-zA-Z0-9_]*)", r"%(\1)s", t))

        # Reemplazar placeholders ? por %s si la consulta no usa %(nombre)s ni %s
        if "?" in s and "%s" not in s and "%(" not in s:
            s = _fuera(s, lambda t: t.replace("?", "%s"))
        return s


    def _map_exception(self, exc: Exception):
        if isinstance(exc, pymysql.err.IntegrityError):
            return DualIntegrityError(*exc.args)
        if isinstance(exc, pymysql.err.OperationalError):
            return DualOperationalError(*exc.args)
        if isinstance(exc, pymysql.err.DatabaseError):
            return DualDatabaseError(*exc.args)
        if isinstance(exc, pymysql.err.Error):
            return DualError(*exc.args)
        return exc

    def execute(self, query: str, args: Any = None):
        sql = self._adapt_sql(query, con_args=args is not None)
        try:
            if args is None:
                return self._cur.execute(sql)
            if isinstance(args, (list, tuple)):
                clean_args = tuple(
                    int(x) if isinstance(x, bool) else x
                    for x in args
                )
                return self._cur.execute(sql, clean_args)
            elif isinstance(args, dict):
                clean_dict = {
                    k: (int(v) if isinstance(v, bool) else v)
                    for k, v in args.items()
                }
                return self._cur.execute(sql, clean_dict)
            else:
                return self._cur.execute(sql, (args,))
        except Exception as e:
            raise self._map_exception(e) from e


    def executemany(self, query: str, args: Any):
        sql = self._adapt_sql(query, con_args=True)
        try:
            return self._cur.executemany(sql, args)
        except Exception as e:
            raise self._map_exception(e) from e

    def fetchone(self) -> Optional[CompatRow]:
        row = self._cur.fetchone()
        return CompatRow(row) if row is not None else None

    def fetchall(self) -> List[CompatRow]:
        rows = self._cur.fetchall()
        return [CompatRow(r) for r in rows] if rows else []

    def fetchmany(self, size: int = 1) -> List[CompatRow]:
        rows = self._cur.fetchmany(size)
        return [CompatRow(r) for r in rows] if rows else []

    def close(self):
        self._cur.close()

    def __iter__(self):
        for r in self._cur:
            yield CompatRow(r)


class MariaDBConnection:
    def __init__(self, raw_conn: pymysql.Connection):
        self._conn = raw_conn

    def cursor(self) -> MariaDBCursor:
        return MariaDBCursor(self._conn.cursor(pymysql.cursors.DictCursor))

    def execute(self, sql: str, args: Any = None) -> MariaDBCursor:
        cur = self.cursor()
        cur.execute(sql, args)
        return cur

    def executemany(self, sql: str, seq) -> MariaDBCursor:
        cur = self.cursor()
        cur.executemany(sql, seq)
        return cur

    def executescript(self, script: str) -> None:
        """Permite ejecutar múltiples sentencias separadas por punto y coma."""
        statements = [stmt.strip() for stmt in script.split(";") if stmt.strip()]
        cur = self.cursor()
        for stmt in statements:
            try:
                cur.execute(stmt)
            except Exception:
                pass  # Tolerar CREATE TABLE IF NOT EXISTS u otras sentencias de setup

    def commit(self):

        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self.rollback()
        else:
            self.commit()
        self.close()


def get_raw_mariadb_connection() -> MariaDBConnection:
    """Abre una conexión directa a MariaDB vía PyMySQL."""
    if not DB_PASSWORD:
        raise RuntimeError("Falta la contraseña de MariaDB: pon DB_PASSWORD en el archivo .env (ver .env.example)")
    raw = pymysql.connect(
        host=DB_HOST,
        port=int(DB_PORT),
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        charset="utf8mb4",
        autocommit=False,
        # READ COMMITTED: cada consulta ve lo ya confirmado por las demás PCs (con REPEATABLE READ una conexión abierta
        # veía una «foto» vieja y fallaba con «Record has changed since last read»)
        init_command="SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED",
    )
    return MariaDBConnection(raw)


def _conexion_sqlite(db_path: Any = None):
    """Modo SOLO PARA PRUEBAS (DB_BACKEND=sqlite): una base SQLite temporal, como antes de MariaDB."""
    if DB_BACKEND != "sqlite":
        raise RuntimeError("El modo SQLite solo existe para las pruebas (DB_BACKEND=sqlite)")
    ruta = str(db_path or os.environ.get("RADIO_DB_PATH") or "")
    if not ruta:
        raise RuntimeError("DB_BACKEND=sqlite necesita RADIO_DB_PATH")
    c = sqlite3.connect(ruta, check_same_thread=False, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    c.execute("PRAGMA journal_mode = WAL")
    return c


def get_connection(db_path: Any = None):
    """
    Conexión a la base de ONIX: MariaDB (producción). `db_path` solo se usa en el modo de pruebas (SQLite).
    """
    if DB_BACKEND == "sqlite":
        return _conexion_sqlite(db_path)
    return get_raw_mariadb_connection()


@contextmanager
def db_session() -> Generator[MariaDBConnection, None, None]:
    """Context manager para operaciones transaccionales con auto-commit y rollback."""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_db():
    """
    Dependencia estándar para FastAPI.
    Proporciona la conexión MariaDB compatible a los endpoints.
    """
    with db_session() as conn:
        yield conn


def get_sqlalchemy_session() -> Generator[Session, None, None]:
    """Dependencia para endpoints que requieran sesión nativa de SQLAlchemy."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
