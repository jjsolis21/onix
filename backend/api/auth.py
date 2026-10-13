"""
auth.py — Ónix FM
Usuarios, contraseñas y roles para entrar desde otras PCs de la red.

QUIÉN NECESITA CONTRASEÑA
    · La PC del estudio (127.0.0.1) NO: si ONIX se reinicia solo de
      madrugada, el Studio tiene que seguir funcionando sin que nadie escriba
      nada. Quien está en esa PC ya controla el aire. Cuenta como "admin".
    · Cualquier otra PC de la red SÍ: usuario y contraseña (/login).

ROLES
    admin         todo (usuarios, motor, salidas, respaldos, el aire)
    programacion  Admin: biblioteca, relojes, eventos, pautas, cartuchera,
                  estadísticas. NO opera el aire ni toca motor/salidas/respaldos.
    operador      el Studio (el aire) y consultar.

    Regla de las APIs (ver permiso()):
      · operar el aire (player, programa del Studio) → admin, operador
      · configuración del sistema → solo admin
      · resto de cambios (POST/PUT/DELETE) → admin, programacion
      · consultas (GET) → cualquier usuario con sesión

CONTRASEÑAS: PBKDF2-SHA256 con sal (200 000 vueltas). Las sesiones guardan
solo el hash del token. 5 intentos fallidos desde una IP = 1 minuto de espera.

ENDPOINTS
    POST /api/v1/auth/login      {"usuario", "clave"} → cookie de sesión
    POST /api/v1/auth/logout
    GET  /api/v1/auth/yo         → {"usuario", "nombre", "rol", "local"}
    GET/POST/PUT/DELETE /api/v1/usuarios  (solo admin)
"""

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator

from backend.paths import DB_PATH

router = APIRouter(tags=["Usuarios"])

ROLES = ("admin", "programacion", "operador")
COOKIE = "onix_sesion"
DURACION_SESION = timedelta(days=14)
ITERACIONES = 200_000

# Equipos que cuentan como "la PC del estudio" (sin contraseña). Las pruebas
# añaden "testclient" con ONIX_HOSTS_LOCALES.
HOSTS_LOCALES = {"127.0.0.1", "::1", "localhost"} | {
    h.strip() for h in os.getenv("ONIX_HOSTS_LOCALES", "").split(",") if h.strip()}

# Rutas que no piden sesión
LIBRES = ("/login", "/api/v1/auth/login", "/api/v1/health", "/static/login/", "/favicon.ico")


def _conn():
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def hash_clave(clave: str, sal: Optional[bytes] = None) -> str:
    sal = sal or secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", clave.encode("utf-8"), sal, ITERACIONES)
    return f"pbkdf2${ITERACIONES}${sal.hex()}${h.hex()}"


def verificar_clave(clave: str, guardado: str) -> bool:
    try:
        _, vueltas, sal, h = guardado.split("$")
        calc = hashlib.pbkdf2_hmac("sha256", clave.encode("utf-8"), bytes.fromhex(sal), int(vueltas))
        return hmac.compare_digest(calc.hex(), h)
    except (ValueError, TypeError):
        return False


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def es_local(request) -> bool:
    return (request.client.host if request.client else "") in HOSTS_LOCALES


def usuario_de(request) -> Optional[dict]:
    """Usuario de la petición (o None). La PC del estudio es admin sin sesión."""
    if es_local(request):
        return {"id": None, "usuario": "estudio", "nombre": "PC del estudio", "rol": "admin", "local": True}
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    conn = _conn()
    try:
        r = conn.execute(
            """SELECT u.id, u.usuario, u.nombre, u.rol, s.expira FROM sesiones s
               JOIN usuarios u ON u.id = s.usuario_id
               WHERE s.token_hash = ? AND u.activo = 1""", (_hash_token(token),)).fetchone()
        if not r or r["expira"] < datetime.now().isoformat():
            return None
        return {"id": r["id"], "usuario": r["usuario"], "nombre": r["nombre"], "rol": r["rol"], "local": False}
    finally:
        conn.close()


# ── Permisos ────────────────────────────────────────────────────────────────

# El operador carga sus paletas arrastrando desde la biblioteca; programación también
_BOTONES = re.compile(r"^/api/v1/cartuchera/")
_AIRE = re.compile(r"^/api/v1/(player/|programa/|playlists/\d+/lanzar$)")
_SISTEMA = re.compile(r"^/api/v1/(usuarios|respaldos|audio/|config/motor|ia/|tandas/secciones|normalizacion)")


def permiso(rol: str, metodo: str, ruta: str) -> bool:
    if rol == "admin":
        return True
    if ruta == "/" or ruta.startswith("/studio"):
        return rol == "operador"                 # el Studio opera el aire
    if ruta.startswith("/admin"):
        return rol == "programacion"
    if not ruta.startswith("/api/"):
        return True                              # /static, /stream…
    if metodo in ("GET", "HEAD", "OPTIONS"):
        return not ruta.startswith(("/api/v1/usuarios", "/api/v1/ia/"))   # claves de IA: solo admin
    if ruta.startswith("/api/v1/problemas"):
        return True                              # marcar avisos como vistos
    if _BOTONES.match(ruta):
        return rol in ("operador", "programacion")
    if _AIRE.match(ruta):
        return rol == "operador"
    if _SISTEMA.match(ruta):
        return False
    return rol == "programacion"


def puede_operar_aire(rol: str) -> bool:
    return rol in ("admin", "operador")


# ── Intentos fallidos ───────────────────────────────────────────────────────

_fallos: dict[str, list[float]] = {}


def _bloqueado(ip: str) -> bool:
    ahora = time.monotonic()
    _fallos[ip] = [t for t in _fallos.get(ip, []) if ahora - t < 60]
    return len(_fallos[ip]) >= 5


# ── Endpoints de sesión ─────────────────────────────────────────────────────

class LoginIn(BaseModel):
    usuario: str = Field(..., min_length=1, max_length=60)
    clave:   str = Field(..., min_length=1, max_length=200)


@router.post("/api/v1/auth/login", summary="Iniciar sesión")
def login(body: LoginIn, request: Request, response: Response):
    ip = request.client.host if request.client else "?"
    if _bloqueado(ip):
        raise HTTPException(status_code=429, detail="Demasiados intentos. Espera un minuto.")
    conn = _conn()
    try:
        u = conn.execute("SELECT * FROM usuarios WHERE usuario = ? AND activo = 1",
                         (body.usuario.strip().lower(),)).fetchone()
        if not u or not verificar_clave(body.clave, u["clave_hash"]):
            _fallos.setdefault(ip, []).append(time.monotonic())
            raise HTTPException(status_code=401, detail="Usuario o contraseña incorrectos")
        token = secrets.token_urlsafe(32)
        expira = (datetime.now() + DURACION_SESION).isoformat(timespec="seconds")
        conn.execute("DELETE FROM sesiones WHERE expira < ?", (datetime.now().isoformat(),))
        conn.execute("INSERT INTO sesiones (token_hash, usuario_id, expira, ip) VALUES (?, ?, ?, ?)",
                     (_hash_token(token), u["id"], expira, ip))
        conn.execute("UPDATE usuarios SET ultimo_acceso = datetime('now', 'localtime') WHERE id = ?", (u["id"],))
        conn.commit()
    finally:
        conn.close()
    _fallos.pop(ip, None)
    response.set_cookie(COOKIE, token, max_age=int(DURACION_SESION.total_seconds()),
                        httponly=True, samesite="lax")
    return {"usuario": u["usuario"], "nombre": u["nombre"], "rol": u["rol"],
            "inicio": "/" if u["rol"] == "operador" else "/admin/"}


@router.post("/api/v1/auth/logout", summary="Cerrar sesión")
def logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE)
    if token:
        conn = _conn()
        try:
            conn.execute("DELETE FROM sesiones WHERE token_hash = ?", (_hash_token(token),))
            conn.commit()
        finally:
            conn.close()
    response.delete_cookie(COOKIE)
    return {"ok": True}


@router.get("/api/v1/auth/yo", summary="Quién soy")
def yo(request: Request):
    u = usuario_de(request)
    if not u:
        raise HTTPException(status_code=401, detail="Sin sesión")
    return {k: u[k] for k in ("usuario", "nombre", "rol", "local")}


# ── Gestión de usuarios (solo admin; lo controla el middleware) ─────────────

class UsuarioIn(BaseModel):
    usuario: str = Field(..., min_length=3, max_length=40, pattern=r"^[A-Za-z0-9._-]+$")
    nombre:  str = Field("", max_length=80)
    rol:     str
    clave:   Optional[str] = Field(None, min_length=8, max_length=200)
    activo:  bool = True

    @field_validator("rol")
    @classmethod
    def _rol(cls, v):
        if v not in ROLES:
            raise ValueError(f"rol debe ser: {', '.join(ROLES)}")
        return v


def _out(r) -> dict:
    return {"id": r["id"], "usuario": r["usuario"], "nombre": r["nombre"], "rol": r["rol"],
            "activo": bool(r["activo"]), "ultimo_acceso": r["ultimo_acceso"]}


@router.get("/api/v1/usuarios", summary="Usuarios")
def listar():
    conn = _conn()
    try:
        return [_out(r) for r in conn.execute("SELECT * FROM usuarios ORDER BY usuario")]
    finally:
        conn.close()


@router.post("/api/v1/usuarios", status_code=201, summary="Crear usuario")
def crear(body: UsuarioIn):
    if not body.clave:
        raise HTTPException(status_code=422, detail="La contraseña es obligatoria (mínimo 8 caracteres)")
    conn = _conn()
    try:
        try:
            uid = conn.execute(
                "INSERT INTO usuarios (usuario, nombre, rol, clave_hash, activo) VALUES (?, ?, ?, ?, ?)",
                (body.usuario.lower(), body.nombre.strip(), body.rol, hash_clave(body.clave),
                 int(body.activo))).lastrowid
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail=f"Ya existe el usuario '{body.usuario}'")
        conn.commit()
        return _out(conn.execute("SELECT * FROM usuarios WHERE id = ?", (uid,)).fetchone())
    finally:
        conn.close()


@router.put("/api/v1/usuarios/{uid}", summary="Modificar usuario (clave opcional)")
def modificar(uid: int, body: UsuarioIn):
    conn = _conn()
    try:
        if not conn.execute("SELECT 1 FROM usuarios WHERE id = ?", (uid,)).fetchone():
            raise HTTPException(status_code=404, detail="Usuario no encontrado")
        try:
            conn.execute("UPDATE usuarios SET usuario = ?, nombre = ?, rol = ?, activo = ? WHERE id = ?",
                         (body.usuario.lower(), body.nombre.strip(), body.rol, int(body.activo), uid))
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail=f"Ya existe el usuario '{body.usuario}'")
        if body.clave:
            conn.execute("UPDATE usuarios SET clave_hash = ? WHERE id = ?", (hash_clave(body.clave), uid))
        if body.clave or not body.activo:
            conn.execute("DELETE FROM sesiones WHERE usuario_id = ?", (uid,))   # cerrar sus sesiones
        conn.commit()
        return _out(conn.execute("SELECT * FROM usuarios WHERE id = ?", (uid,)).fetchone())
    finally:
        conn.close()


@router.delete("/api/v1/usuarios/{uid}", status_code=204, summary="Borrar usuario")
def borrar(uid: int):
    conn = _conn()
    try:
        if not conn.execute("DELETE FROM usuarios WHERE id = ?", (uid,)).rowcount:
            raise HTTPException(status_code=404, detail="Usuario no encontrado")
        conn.commit()
    finally:
        conn.close()
