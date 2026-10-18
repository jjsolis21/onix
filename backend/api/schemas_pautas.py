"""
schemas_pautas.py — Ónix FM Radio Core
=====================================================================
Modelos Pydantic para la Sección 03 — Programación de Pautas.

Guardar en: backend/api/schemas_pautas.py

Convención de nombres:
  PautaIn  → payload recibido del frontend (POST / PUT)
  PautaOut → respuesta enviada al frontend (GET / POST response)

Columnas en DB:
  id, cliente, audio_id, audio_nombre, matriz (JSON str),
  fecha_inicio, fecha_fin, notas, activo, created_at, updated_at
=====================================================================
"""

from __future__ import annotations

import json
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ─────────────────────────────────────────────────────────────────
# PAUTA — Entrada (validación y normalización del payload)
# ─────────────────────────────────────────────────────────────────

class PautaIn(BaseModel):
    """
    Payload recibido del frontend al crear (POST) o actualizar (PUT) una pauta.

    Normalización automática:
      · cliente  → .strip().upper()   (Marca Blanca: siempre en MAYÚSCULAS)
      · notas    → .strip().upper()
      · matriz   → acepta dict o string JSON; siempre se devuelve como dict
    """

    cliente:      str            = Field(..., min_length=1, max_length=200)
    audio_id:     Optional[int]  = Field(None, description="ID del audio en tabla audios")
    audio_nombre: str            = Field("", max_length=500)
    # La matriz llega como dict desde el frontend; se serializa a JSON al guardar
    matriz:       dict[str, Any] = Field(default_factory=dict)
    fecha_inicio: str            = Field("", description="YYYY-MM-DD o vacío")
    fecha_fin:    str            = Field("", description="YYYY-MM-DD o vacío")
    notas:        str            = Field("", max_length=2000)
    cue_intro:    Optional[float]= Field(None, description="Punto de inicio o intro de la pauta")
    cue_outro:    Optional[float]= Field(None, description="Punto final o outro de la pauta")
    menciones_diarias: Optional[int] = Field(None, ge=1, le=24, description="Cantidad de veces a reproducir por día")
    rubro:        str            = Field("", max_length=200)
    prioridad:    int            = Field(0, ge=-1, le=1, description="1 Alta · 0 Normal · -1 Baja")
    contrato_id:  str            = Field("", max_length=100)
    agencia:      str            = Field("", max_length=200)
    posicion:     int            = Field(0, ge=0, le=2, description="0 libre · 1 primero · 2 último en la tanda")
    hora_inicio_permitida: Optional[int] = Field(None, ge=0, le=23, description="Hora inicial permitida (Dayparting)")
    hora_fin_permitida:    Optional[int] = Field(None, ge=0, le=23, description="Hora final permitida (Dayparting)")

    # Varias versiones del spot que rotan en cada salida (incluye la principal).

    # None = no tocar las que ya tiene; [] = una sola versión (audio_id)
    versiones:    Optional[list[int]] = Field(None, max_length=20)
    seccion:      int            = Field(3, ge=0, le=99,
                                         description="Sección de la tanda (1 ID, 2 cortina de entrada, 3 comerciales…)")


    # ── Normalización de texto ────────────────────────────────────

    @field_validator("cliente", mode="before")
    @classmethod
    def cliente_upper(cls, v: str) -> str:
        """MARCA BLANCA: nombre del cliente siempre en MAYÚSCULAS."""
        return (v or "").strip().upper()

    @field_validator("notas", mode="before")
    @classmethod
    def notas_upper(cls, v: str) -> str:
        return (v or "").strip().upper()

    @field_validator("prioridad", mode="before")
    @classmethod
    def prioridad_nivel(cls, v: Any) -> int:
        """Acepta el sí/no viejo (urgente = Alta) o el nivel -1/0/1."""
        if isinstance(v, bool) or v is None:
            return 1 if v else 0
        return v

    @field_validator("rubro", "contrato_id", "agencia", mode="before")
    @classmethod
    def rubro_contrato_str_upper(cls, v: str) -> str:
        return (v or "").strip().upper()

    @field_validator("audio_nombre", mode="before")
    @classmethod
    def audio_nombre_strip(cls, v: str) -> str:
        return (v or "").strip()

    @field_validator("fecha_inicio", "fecha_fin", mode="before")
    @classmethod
    def fecha_strip(cls, v: str) -> str:
        return (v or "").strip()

    # ── Soporte de matriz como string JSON (retrocompatibilidad) ──

    @field_validator("matriz", mode="before")
    @classmethod
    def parse_matriz(cls, v: Any) -> dict:
        """
        Acepta la matriz como:
          · dict  → pasa directo (caso normal desde frontend JSON)
          · str   → parsea el string JSON (caso PUT desde algunos clientes)
        """
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
                return parsed if isinstance(parsed, dict) else {}
            except (json.JSONDecodeError, ValueError):
                return {}
        return v if isinstance(v, dict) else {}

    # ── Validación cruzada de fechas ──────────────────────────────

    @model_validator(mode="after")
    def validate_fechas(self) -> "PautaIn":
        if self.fecha_inicio and self.fecha_fin:
            if self.fecha_inicio > self.fecha_fin:
                raise ValueError(
                    "fecha_inicio no puede ser posterior a fecha_fin."
                )
        return self

    # ── Conversión al shape de INSERT/UPDATE en SQLite ────────────

    def to_db_dict(self) -> dict:
        """
        Devuelve un dict listo para pasar como parámetros a cursor.execute().
        La matriz se serializa a JSON string para almacenamiento en SQLite.
        """
        return {
            "cliente":      self.cliente,
            "audio_id":     self.audio_id,
            "audio_nombre": self.audio_nombre,
            "matriz":       json.dumps(self.matriz, ensure_ascii=False),
            "fecha_inicio": self.fecha_inicio,
            "fecha_fin":    self.fecha_fin,
            "notas":        self.notas,
            "cue_intro":    self.cue_intro,
            "cue_outro":    self.cue_outro,
            "menciones_diarias": self.menciones_diarias,
            "rubro":        self.rubro,
            "prioridad":    self.prioridad,
            "contrato_id":  self.contrato_id,
            "agencia":      self.agencia,
            "posicion":     self.posicion,
            "hora_inicio_permitida": self.hora_inicio_permitida,
            "hora_fin_permitida":    self.hora_fin_permitida,
            "seccion":      self.seccion,
        }


# ─────────────────────────────────────────────────────────────────
# PAUTA — Salida (respuesta al cliente con matriz ya como dict)
# ─────────────────────────────────────────────────────────────────

class PautaOut(BaseModel):
    """
    Respuesta enviada al cliente.
    El campo `matriz` se devuelve como dict (no como string JSON),
    listo para consumir directamente en pautas-controller.js.
    """

    id:           int
    cliente:      str
    audio_id:     Optional[int]
    audio_nombre: str
    matriz:       dict[str, Any]
    fecha_inicio: str
    fecha_fin:    str
    notas:        str
    cue_intro:    Optional[float]
    cue_outro:    Optional[float]
    menciones_diarias: Optional[int]
    rubro:        str
    prioridad:    int = 0
    contrato_id:  str
    agencia:      str = ""
    posicion:     int = 0
    hora_inicio_permitida: Optional[int] = None
    hora_fin_permitida:    Optional[int] = None
    orden:        Optional[int] = None
    versiones:    list[dict] = []
    seccion:      int = 3
    activo:       int
    created_at:   str
    updated_at:   str


    model_config = {"from_attributes": True}

    @classmethod
    def from_row(cls, row: Any) -> "PautaOut":
        """
        Construye un PautaOut desde una fila de la base (CompatRow).

        Parsea automáticamente el campo `matriz` de string JSON → dict
        y aplica valores por defecto para campos que puedan ser NULL
        en registros anteriores a esta migración.
        """
        data = dict(row)

        # Parsear matriz (siempre guardado como string en SQLite)
        raw_matriz = data.get("matriz", "{}")
        try:
            data["matriz"] = (
                json.loads(raw_matriz)
                if isinstance(raw_matriz, str)
                else (raw_matriz or {})
            )
        except (json.JSONDecodeError, TypeError):
            data["matriz"] = {}

        # Defaults defensivos para campos opcionales
        data.setdefault("audio_nombre", "")
        data.setdefault("notas",        "")
        data.setdefault("fecha_inicio", "")
        data.setdefault("fecha_fin",    "")
        data.setdefault("rubro",        "")
        data.setdefault("contrato_id",  "")
        if data.get("seccion") is None:
            data["seccion"] = 3
        data.setdefault("updated_at",   data.get("created_at", ""))
        
        # Parse boolean properties
        prioridad_val = data.get("prioridad")
        data["prioridad"] = int(prioridad_val or 0)
        data["agencia"] = data.get("agencia") or ""
        data["posicion"] = int(data.get("posicion") or 0)

        return cls(**data)
