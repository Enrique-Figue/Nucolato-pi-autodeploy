"""Rutas FastAPI para operar relojes ZKTeco mediante la librería pyzk.

Este módulo encapsula la lógica de autenticación por API key, la apertura
segura de sesiones con el reloj y expone endpoints sencillos que Odoo u
otros integradores pueden consumir para consultar información, administrar
usuarios y sincronizar marcajes o la hora del dispositivo.
"""

from datetime import datetime, timezone
from contextlib import contextmanager
from typing import Any, Iterator, Optional
import os

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from zk import ZK  # pyzk instala el paquete 'pyzk' pero se importa como 'zk'

router = APIRouter(tags=["zk"])

# ---- Autenticación ---------------------------------------------------------
API_KEY_ENV = (os.getenv("API_KEY") or "").strip()


def require_api_key(
    x_api_key: Optional[str] = Header(default=None, alias="X-API-KEY"),
    api_key_qs: Optional[str] = Query(default=None, alias="api_key"),
) -> None:
    """Valida la API key recibida ya sea por header o query-string."""
    if not API_KEY_ENV:
        return  # Si no se define API_KEY en entorno, no se exige autenticación.
    provided = x_api_key or api_key_qs
    if provided != API_KEY_ENV:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )


# ---- Configuración de conexión --------------------------------------------
ZK_IP = os.getenv("ZK_IP")
ZK_PORT = int(os.getenv("ZK_PORT", "4370") or 4370)
ZK_PASSWORD = int(os.getenv("ZK_PASSWORD", "0") or 0)
ZK_TIMEOUT = int(os.getenv("ZK_TIMEOUT", "5") or 5)


@contextmanager
def zk_conn() -> Iterator[Any]:
    """Abre una conexión con el reloj ZKTeco garantizando su cierre."""
    if not ZK_IP:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="ZK_IP no configurado",
        )
    zk = ZK(
        ip=ZK_IP,
        port=ZK_PORT,
        timeout=ZK_TIMEOUT,
        password=ZK_PASSWORD,
        force_udp=False,
        ommit_ping=True,
    )
    conn = None
    try:
        conn = zk.connect()
        yield conn
    except Exception as exc:  # pyzk propaga distintos tipos de errores
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"No se pudo conectar al checador: {exc}",
        ) from exc
    finally:
        if conn:
            try:
                conn.disconnect()
            except Exception:
                # ya no hacemos nada: estamos en cleanup
                pass


# ---- Modelos ---------------------------------------------------------------
class UserCreate(BaseModel):
    """Payload para crear o actualizar usuarios en el reloj."""

    user_id: str = Field(..., description="PIN visible en el reloj")
    name: Optional[str] = Field(None, description="Nombre mostrado en el equipo")
    privilege: int = Field(
        0,
        description="Nivel de privilegio (0=usuario regular, 14=administrador)",
    )
    password: Optional[str] = Field(None, description="Contraseña numérica (si aplica)")
    card: Optional[int] = Field(
        None,
        description="Número de tarjeta/badge. Usa 0 o None si no aplica.",
    )

    @field_validator("user_id")
    @classmethod
    def validate_user_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("user_id no puede estar vacío")
        return value

    @field_validator("privilege")
    @classmethod
    def validate_privilege(cls, value: int) -> int:
        if value < 0:
            raise ValueError("privilege debe ser un entero positivo")
        return value

    @field_validator("card", mode="before")
    @classmethod
    def normalize_card(cls, value: Optional[Any]) -> Optional[int]:
        if value in (None, "", 0, "0"):
            return None
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("card debe ser numérico") from exc


class SetTimePayload(BaseModel):
    """Solicita sincronizar o fijar manualmente la hora del reloj."""

    iso_datetime: Optional[str] = Field(
        None,
        description="Fecha/hora en formato ISO (ej. 2024-01-31T08:00:00-06:00)",
    )
    sync_now: bool = Field(
        False,
        description="Si es true, ignora iso_datetime y sincroniza con la hora local",
    )


# ---- Funciones auxiliares --------------------------------------------------
def _serialize_user(user: Any) -> dict[str, Any]:
    """Convierte la instancia de usuario pyzk a un diccionario JSON."""
    return {
        "uid": getattr(user, "uid", None),
        "user_id": getattr(user, "user_id", None),
        "name": getattr(user, "name", None),
        "privilege": getattr(user, "privilege", None),
        "password": getattr(user, "password", None),
        "card": getattr(user, "card", None),
    }


def _serialize_attendance(record: Any) -> dict[str, Any]:
    """Convierte un registro de asistencia pyzk a un diccionario JSON."""
    return {
        "timestamp": str(getattr(record, "timestamp", None)),
        "user_id": getattr(record, "user_id", None) or getattr(record, "uid", None),
        "status": getattr(record, "status", None),
        "punch": getattr(record, "punch", None),
        "workcode": getattr(record, "workcode", None),
    }


def _fetch_device_info(conn: Any) -> dict[str, Any]:
    """Obtiene información básica del dispositivo manejando errores individuales."""
    info: dict[str, Any] = {}
    getters = [
        ("firmware", getattr(conn, "get_firmware_version", None)),
        ("platform", getattr(conn, "get_platform", None)),
        ("serial", getattr(conn, "get_serialnumber", None)),
        ("device_name", getattr(conn, "get_device_name", None)),
        ("device_time", getattr(conn, "get_time", None)),
    ]
    for label, getter in getters:
        if callable(getter):
            try:
                info[label] = getter()
            except Exception as exc:
                info[label] = f"error: {exc}"
        else:
            info[label] = None

    try:
        users = conn.get_users() or []
        info["users_count"] = len(users)
    except Exception as exc:
        info["users_count"] = f"error: {exc}"

    try:
        attendance = conn.get_attendance() or []
        info["attendance_count"] = len(attendance)
    except Exception as exc:
        info["attendance_count"] = f"error: {exc}"

    if info.get("device_time") is not None:
        info["device_time"] = str(info["device_time"])
    return info


def _parse_iso_datetime(value: str) -> datetime:
    """Convierte una cadena ISO 8601 en datetime manejando sufijos 'Z'."""
    normalized = value.strip().replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


# ---- Endpoints -------------------------------------------------------------
@router.get("/zk/ping")
def zk_ping(_: None = Depends(require_api_key)) -> dict[str, bool]:
    """Comprueba que la API puede autenticar y abrir sesión con el reloj."""
    with zk_conn():
        return {"ok": True}


@router.get("/zk/info")
def zk_info(_: None = Depends(require_api_key)) -> dict[str, Any]:
    """Devuelve datos generales del dispositivo y conteo de usuarios/asistencias."""
    with zk_conn() as conn:
        info = _fetch_device_info(conn)
        return {"ok": True, "info": info}


@router.get("/zk/users")
def zk_list_users(
    limit: int = Query(100, ge=1, le=10000),
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    """Lista usuarios registrados en el dispositivo."""
    with zk_conn() as conn:
        users = conn.get_users() or []
        items = [_serialize_user(u) for u in users[:limit]]
        return {"ok": True, "count": len(items), "items": items}


@router.post("/zk/users")
def zk_create_or_update_user(
    payload: UserCreate = Body(...),
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    """Crea o actualiza un usuario en el reloj."""
    with zk_conn() as conn:
        try:
            conn.set_user(
                uid=None,
                name=payload.name or "",
                privilege=payload.privilege,
                password=payload.password or "",
                group_id="",
                user_id=payload.user_id,
                card=payload.card or 0,
            )
            return {"ok": True, "user_id": payload.user_id}
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"No se pudo crear/actualizar: {exc}",
            ) from exc


@router.delete("/zk/users/{user_id}")
def zk_delete_user(
    user_id: str,
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    """Elimina un usuario del reloj usando su PIN (user_id)."""
    with zk_conn() as conn:
        try:
            users = conn.get_users() or []
            target = next((u for u in users if getattr(u, "user_id", None) == user_id), None)
            if not target:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Usuario no encontrado",
                )
            conn.delete_user(uid=getattr(target, "uid", None))
            return {"ok": True, "deleted": user_id}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"No se pudo borrar: {exc}",
            ) from exc


@router.get("/zk/attendance")
def zk_attendance(
    limit: int = Query(100, ge=1, le=10000),
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    """Recupera los últimos registros de asistencia (ordenados por timestamp)."""
    with zk_conn() as conn:
        attendance = conn.get_attendance() or []
        try:
            attendance = sorted(attendance, key=lambda a: getattr(a, "timestamp", datetime.min))
        except Exception:
            # Si el firmware devuelve timestamps no comparables, mantenemos el orden original.
            pass
        items = [_serialize_attendance(a) for a in attendance[-limit:]]
        return {"ok": True, "count": len(items), "items": items}


@router.post("/zk/time/sync")
def zk_time_sync(_: None = Depends(require_api_key)) -> dict[str, Any]:
    """Sincroniza la hora del reloj con el sistema donde corre la API."""
    with zk_conn() as conn:
        now = datetime.now(timezone.utc).astimezone()
        try:
            conn.set_time(now.replace(tzinfo=None))
            return {"ok": True, "set_to": str(now)}
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"No se pudo ajustar hora: {exc}",
            ) from exc


@router.put("/zk/time")
def zk_time_set(
    payload: SetTimePayload,
    _: None = Depends(require_api_key),
) -> dict[str, Any]:
    """Permite fijar una hora específica o sincronizar inmediatamente el reloj."""
    with zk_conn() as conn:
        if payload.sync_now:
            now = datetime.now()
            try:
                conn.set_time(now)
                return {"ok": True, "set_to": str(now)}
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"No se pudo ajustar hora: {exc}",
                ) from exc

        if not payload.iso_datetime:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Falta iso_datetime o sync_now=true",
            )

        try:
            dt = _parse_iso_datetime(payload.iso_datetime)
            conn.set_time(dt.replace(tzinfo=None))
            return {"ok": True, "set_to": str(dt)}
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Fecha/hora inválida o error: {exc}",
            ) from exc


@router.post("/zk/reboot")
def zk_reboot(_: None = Depends(require_api_key)) -> dict[str, Any]:
    """Solicita al reloj un reinicio remoto."""
    with zk_conn() as conn:
        try:
            conn.restart()
            return {"ok": True}
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"No se pudo reiniciar: {exc}",
            ) from exc
