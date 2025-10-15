from fastapi import APIRouter, Depends, HTTPException, status, Query, Body
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime, timezone
import os
from contextlib import contextmanager
from zk import ZK, const  # pyzk se importa como 'zk'

router = APIRouter(tags=["zk"])

# ---- API Key simple ----
API_KEY_ENV = os.getenv("API_KEY", "").strip()
def require_api_key(x_api_key: Optional[str] = None):
    if not API_KEY_ENV:
        return
    if x_api_key != API_KEY_ENV:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")

# ---- Conexión ZK helper ----
ZK_IP = os.getenv("ZK_IP")
ZK_PORT = int(os.getenv("ZK_PORT", "4370") or 4370)
ZK_PASSWORD = int(os.getenv("ZK_PASSWORD", "0") or 0)
ZK_TIMEOUT = int(os.getenv("ZK_TIMEOUT", "5") or 5)

@contextmanager
def zk_conn():
    if not ZK_IP:
        raise HTTPException(500, "ZK_IP no configurado")
    zk = ZK(ip=ZK_IP, port=ZK_PORT, timeout=ZK_TIMEOUT, password=ZK_PASSWORD,
            force_udp=False, ommit_ping=True)
    conn = None
    try:
        conn = zk.connect()
        yield conn
    except Exception as e:
        raise HTTPException(502, f"No se pudo conectar al checador: {e}")
    finally:
        try:
            if conn:
                conn.disconnect()
        except Exception:
            pass

# ---- Modelos ----
class UserCreate(BaseModel):
    user_id: str = Field(..., description="PIN visible en el reloj")
    name: Optional[str] = None
    privilege: int = Field(0, description="0=user, 14=admin (según firmware)")
    password: Optional[str] = None
    card: Optional[str] = None

class SetTimePayload(BaseModel):
    iso_datetime: Optional[str] = None
    sync_now: bool = False

# ---- Endpoints ----
@router.get("/zk/ping")
def zk_ping(_: None = Depends(require_api_key)):
    with zk_conn() as _:
        return {"ok": True}

@router.get("/zk/info")
def zk_info(_: None = Depends(require_api_key)):
    with zk_conn() as conn:
        info = {}
        try: info["firmware"] = conn.get_firmware_version()
        except Exception as e: info["firmware"] = f"error: {e}"
        try: info["platform"] = conn.get_platform()
        except Exception as e: info["platform"] = f"error: {e}"
        try: info["serial"] = conn.get_serialnumber()
        except Exception as e: info["serial"] = f"error: {e}"
        try: info["device_name"] = conn.get_device_name() if hasattr(conn, "get_device_name") else None
        except Exception as e: info["device_name"] = f"error: {e}"
        try: info["device_time"] = str(conn.get_time())
        except Exception as e: info["device_time"] = f"error: {e}"
        try: info["users_count"] = len(conn.get_users() or [])
        except Exception as e: info["users_count"] = f"error: {e}"
        try: info["attendance_count"] = len(conn.get_attendance() or [])
        except Exception as e: info["attendance_count"] = f"error: {e}"
        return {"ok": True, "info": info}

@router.get("/zk/users")
def zk_list_users(limit: int = Query(100, ge=1, le=10000), _: None = Depends(require_api_key)):
    with zk_conn() as conn:
        users = conn.get_users() or []
        out = []
        for u in users[:limit]:
            out.append({
                "uid": getattr(u, "uid", None),
                "user_id": getattr(u, "user_id", None),
                "name": getattr(u, "name", None),
                "privilege": getattr(u, "privilege", None),
                "password": getattr(u, "password", None),
                "card": getattr(u, "card", None),
            })
        return {"ok": True, "count": len(out), "items": out}

@router.post("/zk/users")
def zk_create_or_update_user(payload: UserCreate = Body(...), _: None = Depends(require_api_key)):
    with zk_conn() as conn:
        try:
            conn.set_user(
                uid=None,
                name=payload.name or "",
                privilege=payload.privilege,
                password=payload.password or "",
                group_id="",
                user_id=payload.user_id,
                card=payload.card or "",
            )
            return {"ok": True, "user_id": payload.user_id}
        except Exception as e:
            raise HTTPException(400, f"No se pudo crear/actualizar: {e}")

@router.delete("/zk/users/{user_id}")
def zk_delete_user(user_id: str, _: None = Depends(require_api_key)):
    with zk_conn() as conn:
        try:
            users = conn.get_users() or []
            target = next((u for u in users if getattr(u, "user_id", None) == user_id), None)
            if not target:
                raise HTTPException(404, "Usuario no encontrado")
            conn.delete_user(uid=getattr(target, "uid", None))
            return {"ok": True, "deleted": user_id}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(400, f"No se pudo borrar: {e}")

@router.get("/zk/attendance")
def zk_attendance(limit: int = Query(100, ge=1, le=10000), _: None = Depends(require_api_key)):
    with zk_conn() as conn:
        att = conn.get_attendance() or []
        try:
            att = sorted(att, key=lambda a: getattr(a, "timestamp", datetime.min))
        except Exception:
            pass
        items = []
        for a in att[-limit:]:
            items.append({
                "timestamp": str(getattr(a, "timestamp", None)),
                "user_id": getattr(a, "user_id", None) or getattr(a, "uid", None),
                "status": getattr(a, "status", None),
                "punch": getattr(a, "punch", None),
                "workcode": getattr(a, "workcode", None),
            })
        return {"ok": True, "count": len(items), "items": items}

@router.post("/zk/time/sync")
def zk_time_sync(_: None = Depends(require_api_key)):
    with zk_conn() as conn:
        now = datetime.now(timezone.utc).astimezone()
        try:
            conn.set_time(now.replace(tzinfo=None))
            return {"ok": True, "set_to": str(now)}
        except Exception as e:
            raise HTTPException(400, f"No se pudo ajustar hora: {e}")

@router.put("/zk/time")
def zk_time_set(payload: SetTimePayload, _: None = Depends(require_api_key)):
    with zk_conn() as conn:
        if payload.sync_now:
            now = datetime.now()
            try:
                conn.set_time(now)
                return {"ok": True, "set_to": str(now)}
            except Exception as e:
                raise HTTPException(400, f"No se pudo ajustar hora: {e}")
        if not payload.iso_datetime:
            raise HTTPException(400, "Falta iso_datetime o sync_now=true")
        try:
            dt = datetime.fromisoformat(payload.iso_datetime.replace("Z", "+00:00"))
            conn.set_time(dt.replace(tzinfo=None))
            return {"ok": True, "set_to": str(dt)}
        except Exception as e:
            raise HTTPException(400, f"Fecha/hora inválida o error: {e}")

@router.post("/zk/reboot")
def zk_reboot(_: None = Depends(require_api_key)):
    with zk_conn() as conn:
        try:
            conn.restart()
            return {"ok": True}
        except Exception as e:
            raise HTTPException(400, f"No se pudo reiniciar: {e}")
