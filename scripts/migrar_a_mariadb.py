"""
scripts/migrar_a_mariadb.py — Ónix FM
Script de Migración Automática de SQLite (radio_core.db) a MariaDB 12.3+

Pasos que realiza:
  1. Conecta al servidor MariaDB local y verifica si la base de datos `onix_db` existe;
     si no existe, la crea con:
     CREATE DATABASE onix_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
  2. Se conecta a la base de datos SQLite (radio_core.db) y lee todas las tablas.
  3. Convierte las definiciones DDL de SQLite al dialecto MariaDB (InnoDB, utf8mb4),
     adaptando tipos, AUTO_INCREMENT, primary keys y constraints únicos.
  4. Inserta todos los datos de SQLite en MariaDB respetando tipos, fechas y booleanos.
  5. Imprime un resumen detallado con la cantidad de registros migrados por tabla.
"""

import os
import re
import sys
import sqlite3
import pymysql
from pathlib import Path

# Configuración: variables de entorno o archivo .env (la clave NO va en el código)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.database import cargar_env  # noqa: E402
cargar_env()
DB_USER     = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_HOST     = os.getenv("DB_HOST", "localhost")
DB_PORT     = int(os.getenv("DB_PORT", "3306"))
DB_NAME     = os.getenv("DB_NAME", "onix_db")

# Localizar la base de datos SQLite
ROOT_DIR    = Path(__file__).resolve().parent.parent
SQLITE_PATH = Path(os.getenv("RADIO_DB_PATH", ROOT_DIR / "backend" / "api" / "radio_core.db"))


def clean_sql_comments(sql: str) -> str:
    """Elimina comentarios de una línea de SQLite (-- comentario)."""
    lines = []
    for line in sql.splitlines():
        line = re.sub(r'--.*$', '', line)
        if line.strip():
            lines.append(line)
    return "\n".join(lines)


def convert_sqlite_to_mariadb(table_name: str, sqlite_ddl: str) -> str:
    """
    Traduce el DDL de SQLite a sintaxis MariaDB/InnoDB.
    Maneja collation, AUTO_INCREMENT, longitud de columnas UNIQUE y tipos de fecha.
    """
    ddl = clean_sql_comments(sqlite_ddl)
    
    # Normalizar encabezado
    ddl = re.sub(r'\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)', f'CREATE TABLE IF NOT EXISTS `{table_name}`', ddl, flags=re.IGNORECASE)
    
    # Quitar COLLATE NOCASE de SQLite (MariaDB ya es insensible a mayúsculas con utf8mb4_unicode_ci)
    ddl = re.sub(r'\bCOLLATE\s+NOCASE\b', '', ddl, flags=re.IGNORECASE)

    # AUTOINCREMENT -> AUTO_INCREMENT PRIMARY KEY
    ddl = re.sub(r'\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b', 'INT AUTO_INCREMENT PRIMARY KEY', ddl, flags=re.IGNORECASE)
    
    # Defaults de fechas de SQLite
    ddl = re.sub(r"DEFAULT\s+\(datetime\([^)]*\)\)", "DEFAULT CURRENT_TIMESTAMP", ddl, flags=re.IGNORECASE)
    ddl = re.sub(r"DEFAULT\s+\(strftime\([^)]*\)\)", "DEFAULT CURRENT_TIMESTAMP", ddl, flags=re.IGNORECASE)
    ddl = re.sub(r"DEFAULT\s+current_timestamp", "DEFAULT CURRENT_TIMESTAMP", ddl, flags=re.IGNORECASE)
    
    # Extraer columnas involucradas en PRIMARY KEY o UNIQUE compuestos y simples
    composite_key_cols = set()
    single_key_cols = set()
    for m in re.finditer(r'(?:PRIMARY\s+KEY|UNIQUE)\s*\(([^)]+)\)', ddl, re.IGNORECASE):
        cols = [c.strip().strip('`"[]').split()[0].lower() for c in m.group(1).split(',')]
        if len(cols) > 1:
            for c in cols:
                composite_key_cols.add(c)
        else:
            single_key_cols.add(cols[0])

    lines = ddl.splitlines()
    new_lines = []
    for line in lines:
        col_match = re.match(r'^\s*[`"\[]?([a-zA-Z0-9_]+)[`"\]]?\s+(.*)$', line)
        if col_match:
            c_name = col_match.group(1)
            c_rest = col_match.group(2)
            c_lower = c_name.lower()
            
            is_composite = c_lower in composite_key_cols
            is_single = (c_lower in single_key_cols) or bool(re.search(r'\b(UNIQUE|PRIMARY\s+KEY)\b', c_rest, re.IGNORECASE))
            
            # En MariaDB/InnoDB columnas indexadas o UNIQUE no pueden ser TEXT sin longitud
            if re.search(r'\bTEXT\b', c_rest, re.IGNORECASE):
                if is_composite:
                    c_rest = re.sub(r'\bTEXT\b', 'VARCHAR(150) COLLATE utf8mb4_bin', c_rest, flags=re.IGNORECASE)
                    line = re.sub(r'(\b' + c_name + r'\b\s+).*$', r'\1' + c_rest, line, flags=re.IGNORECASE)
                elif is_single:
                    c_rest = re.sub(r'\bTEXT\b', 'VARCHAR(500) COLLATE utf8mb4_bin', c_rest, flags=re.IGNORECASE)
                    line = re.sub(r'(\b' + c_name + r'\b\s+).*$', r'\1' + c_rest, line, flags=re.IGNORECASE)

        line = re.sub(r'\bINTEGER\b', 'INT', line, flags=re.IGNORECASE)
        new_lines.append(line)
        
    ddl = "\n".join(new_lines)
    
    # Casos especiales de tablas
    if table_name == 'tanda_secciones':
        # La tabla tanda_secciones usa el número 0 como sección válida (Promociones).
        # En MariaDB AUTO_INCREMENT convertiría 0 en 1 a menos que sea INT PRIMARY KEY sin auto_increment.
        ddl = re.sub(r'\bnumero\s+INT\s+AUTO_INCREMENT\s+PRIMARY\s+KEY\b', 'numero INT PRIMARY KEY', ddl, flags=re.IGNORECASE)
        ddl = re.sub(r'\bnumero\s+INTEGER\s+PRIMARY\s+KEY\b', 'numero INT PRIMARY KEY', ddl, flags=re.IGNORECASE)
        ddl = re.sub(r'\bnumero\s+INT\s+PRIMARY\s+KEY\s+AUTO_INCREMENT\b', 'numero INT PRIMARY KEY', ddl, flags=re.IGNORECASE)

    # Configuración de motor InnoDB y codificación utf8mb4
    if not ddl.strip().endswith(';'):
        ddl = ddl.rstrip() + ' ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;'
    else:
        ddl = ddl.rstrip()[:-1] + ' ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;'
        
    return ddl


def migrar():
    print("=================================================================")
    print("   MIGRACIÓN DE BASE DE DATOS: SQLite -> MariaDB 12.3 (ONIX FM)")
    print("=================================================================")
    
    if not SQLITE_PATH.exists():
        print(f"ERROR: No se encontró la base de datos SQLite en: {SQLITE_PATH}")
        sys.exit(1)
        
    # SEGURO: este script hace DROP TABLE de cada tabla. Contra la base de producción solo corre con confirmación explícita.
    if DB_NAME == "onix_db" and os.getenv("ONIX_CONFIRMAR_PRODUCCION") != "SI":
        print("ERROR: el destino es `onix_db` (PRODUCCION) y este script BORRA y recrea sus tablas.")
        print("       Para una base de pruebas usa DB_NAME=onix_test.")
        print("       Solo si de verdad quieres rehacer la base real: ONIX_CONFIRMAR_PRODUCCION=SI")
        sys.exit(2)

    print(f"[*] Origen SQLite: {SQLITE_PATH}")
    print(f"[*] Destino MariaDB: {DB_HOST}:{DB_PORT} / {DB_NAME} (Usuario: {DB_USER})")

    # 1. Conexión al servidor MariaDB y creación de la BD si no existe
    print("\n[1/5] Verificando servidor MariaDB y base de datos...")
    try:
        mconn = pymysql.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            charset='utf8mb4'
        )
        mcur = mconn.cursor()
        mcur.execute(f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;")
        mcur.execute(f"USE `{DB_NAME}`;")
        mcur.execute("SET FOREIGN_KEY_CHECKS = 0;")
        mcur.execute("SET sql_mode = CONCAT_WS(',', @@sql_mode, 'NO_AUTO_VALUE_ON_ZERO');")
        print(f"      Base de datos `{DB_NAME}` lista y configurada.")
    except Exception as exc:
        print(f"ERROR al conectar con MariaDB: {exc}")
        sys.exit(1)

    # 2. Conexión a SQLite y lectura de tablas
    print("\n[2/5] Leyendo tablas desde SQLite...")
    sconn = sqlite3.connect(str(SQLITE_PATH))
    scur = sconn.cursor()
    scur.execute("SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name;")
    tables = scur.fetchall()
    print(f"      Se encontraron {len(tables)} tablas para migrar.")

    # 3. Limpiar y recrear tablas en MariaDB
    print("\n[3/5] Creando esquema de tablas en MariaDB...")
    for t_name, _ in tables:
        mcur.execute(f"DROP TABLE IF EXISTS `{t_name}`;")

    for t_name, sql in tables:
        mariadb_ddl = convert_sqlite_to_mariadb(t_name, sql)
        try:
            mcur.execute(mariadb_ddl)
        except Exception as exc:
            print(f"ERROR creando tabla `{t_name}`: {exc}")
            print(f"DDL con fallo:\n{mariadb_ddl}")
            sconn.close()
            mconn.close()
            sys.exit(1)
    print("      Todas las tablas fueron creadas con éxito.")

    # 4. Inserción de datos
    print("\n[4/5] Migrando registros y adaptando tipos...")
    migrados = {}
    for t_name, _ in tables:
        scur.execute(f"PRAGMA table_info(`{t_name}`);")
        cols = [c[1] for c in scur.fetchall()]
        
        scur.execute(f"SELECT * FROM `{t_name}`;")
        rows = scur.fetchall()
        
        if rows:
            placeholders = ", ".join(["%s"] * len(cols))
            cols_str = ", ".join([f"`{c}`" for c in cols])
            insert_sql = f"INSERT INTO `{t_name}` ({cols_str}) VALUES ({placeholders})"
            mcur.executemany(insert_sql, rows)
            
        migrados[t_name] = len(rows)

    mconn.commit()
    mcur.execute("SET FOREIGN_KEY_CHECKS = 1;")

    # 5. Resumen final
    print("\n[5/5] Resumen de migración:")
    print("=" * 60)
    print(f"{'Tabla':38} | {'Registros':15}")
    print("=" * 60)
    total_filas = 0
    for t_name, count in migrados.items():
        total_filas += count
        print(f"{t_name:38} | {count:15,d}")
    print("=" * 60)
    print(f"TOTAL: {len(migrados)} tablas migradas | {total_filas:,d} registros transferidos con éxito.")
    print("=============================================================\n")

    sconn.close()
    mconn.close()


if __name__ == "__main__":
    migrar()
