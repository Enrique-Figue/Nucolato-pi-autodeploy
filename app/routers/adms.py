from fastapi import APIRouter, Request, Query
from fastapi.responses import PlainTextResponse, JSONResponse
from datetime import datetime
import os, json, csv
from urllib.parse import parse_qs
from io import StringIO

router = APIRouter(tags=["adms"])

# --- Directorios base ---
BASE_DIR = "/app/data/adms"
RAW_DIR  = os.path.join(BASE_DIR, "raw")
PARSED_NDJSON = os.path.join(BASE_DIR, "attlog.ndjson")
PARSED_CSV    = os.path.join(BASE_DIR, "attlog.csv")
os.makedirs(BASE_DIR, exist_ok=True)
os.makedirs(RAW_DIR, exist_ok=True)

# --- Utilidades básicas ---
def _ts():
    return datetime.utcnow().strftime("%Y%m%dT%H%M%S%fZ")

def _append_ndjson(obj: dict):
    with open(PARSED_NDJSON, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")

def _append_csv_header_if_needed():
    if not os.path.exists(PARSED_CSV):
        with open(PARSED_CSV, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow([
                "ts_ingest","sn",
                "user_id","timestamp",
                "status","punch","workcode",
                "ext_verified","ext_status","ext_punch","ext_workcode",
                "ext_c7","ext_c8","ext_c9",
                "raw_source"
            ])

def _append_csv_row(ev: dict):
    _append_csv_header_if_needed()
    with open(PARSED_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            ev.get("ts_ingest"),
            ev.get("sn"),
            ev.get("user_id"),
            ev.get("timestamp"),
            ev.get("status"),
            ev.get("punch"),
            ev.get("workcode"),
            ev.get("ext", {}).get("verified"),
            ev.get("ext", {}).get("status"),
            ev.get("ext", {}).get("punch"),
            ev.get("ext", {}).get("workcode"),
            ev.get("ext", {}).get("c7"),
            ev.get("ext", {}).get("c8"),
            ev.get("ext", {}).get("c9"),
            ev.get("raw_source"),
        ])

# --- Parser ATTLOG ---
def _parse_attlog_line(line: str):
    raw = (line or "").rstrip().rstrip(",")
    if not raw:
        return None

    # TSV (ZKTeco extendido)
    if "\t" in raw and "," not in raw:
        parts = raw.split("\t")
        while parts and parts[-1] == "":
            parts.pop()
        if len(parts) >= 2:
            user_id = parts[0].strip()
            dt_str  = parts[1].strip()
            ext = {
                "verified": parts[2].strip() if len(parts) > 2 else None,
                "status":   parts[3].strip() if len(parts) > 3 else None,
                "punch":    parts[4].strip() if len(parts) > 4 else None,
                "workcode": parts[5].strip() if len(parts) > 5 else None,
                "c7":       parts[6].strip() if len(parts) > 6 else None,
                "c8":       parts[7].strip() if len(parts) > 7 else None,
                "c9":       parts[8].strip() if len(parts) > 8 else None,
            }
            ts_fmt = dt_str
            try:
                ts_fmt = datetime.fromisoformat(" ".join(dt_str.split()).replace(" ", "T")).isoformat(sep=" ")
            except Exception:
                pass
            return {
                "user_id": user_id,
                "timestamp": ts_fmt,
                "status": ext["status"],
                "punch":  ext["punch"],
                "workcode": ext["workcode"],
                "ext": ext,
            }
        return None

    # CSV corto
    parts = [p.strip() for p in raw.split(",")]
    while len(parts) < 5:
        parts.append(None)
    user_id, dt_str, status, punch, workcode = parts[:5]
    ts_fmt = dt_str
    try:
        ts_fmt = datetime.fromisoformat(" ".join(dt_str.split()).replace(" ", "T")).isoformat(sep=" ")
    except Exception:
        pass
    return {
        "user_id": user_id,
        "timestamp": ts_fmt,
        "status": status,
        "punch": punch,
        "workcode": workcode,
        "ext": {},
    }

def _maybe_parse_attlog(query: dict, body_text: str):
    events = []
    if str(query.get("table", "")).upper() == "ATTLOG":
        raw = query.get("ATTLOG")
        raw_lines = []
        if isinstance(raw, list): raw_lines.extend(raw)
        elif raw: raw_lines.append(raw)
        for raw_line in raw_lines:
            for line in str(raw_line).splitlines():
                ev = _parse_attlog_line(line)
                if ev: events.append(ev)

    bt = body_text or ""
    if "ATTLOG=" in bt:
        for line in bt.splitlines():
            if "ATTLOG=" in line:
                raw = line.split("ATTLOG=", 1)[1]
                ev = _parse_attlog_line(raw)
                if ev: events.append(ev)
    else:
        if str(query.get("table", "")).upper() == "ATTLOG" and ("\t" in bt or "\n" in bt):
            for line in bt.splitlines():
                ev = _parse_attlog_line(line)
                if ev: events.append(ev)
    return events

# --- Ingestor general ---
def _ingest(request_path: str, query: dict, body: str):
    ts = _ts()
    raw_payload = {"ts": ts, "path": request_path, "query": query, "body": body}
    with open(os.path.join(RAW_DIR, f"{ts}.json"), "w", encoding="utf-8") as f:
        json.dump(raw_payload, f, ensure_ascii=False, indent=2)
    sn = query.get("SN") or query.get("sn") or query.get("SerialNumber")
    events = _maybe_parse_attlog(query, body)
    for ev in events:
        ev_norm = {"ts_ingest": ts, "sn": sn, **ev, "raw_source": request_path}
        _append_ndjson(ev_norm)
        _append_csv_row(ev_norm)

# --- Rutas principales ---
@router.api_route("/iclock/cdata", methods=["GET", "POST"])
@router.api_route("/iclock/cdata/", methods=["GET", "POST"])
async def iclock_cdata(request: Request):
    q = dict(request.query_params)
    body = (await request.body()).decode(errors="ignore")
    if request.headers.get("content-type","").startswith("application/x-www-form-urlencoded") and body:
        try:
            form = parse_qs(body, keep_blank_values=True)
            for k, v in form.items():
                if isinstance(v, list) and len(v) == 1:
                    form[k] = v[0]
            for k, v in form.items():
                q.setdefault(k, v)
        except Exception:
            pass
    _ingest("/iclock/cdata", q, body)
    return PlainTextResponse("OK", status_code=200)

@router.api_route("/iclock/getrequest", methods=["GET", "POST"])
@router.api_route("/iclock/getrequest/", methods=["GET", "POST"])
async def iclock_getrequest(request: Request):
    q = dict(request.query_params)
    body = (await request.body()).decode(errors="ignore")
    _ingest("/iclock/getrequest", q, body)
    return PlainTextResponse("OK", status_code=200)

# --- Consultas de estado ---
@router.get("/iclock/last")
@router.get("/adms/last")
def last():
    files = [f for f in os.listdir(RAW_DIR) if f.endswith(".json")]
    if not files:
        return {"ok": True, "msg": "sin datos aún"}
    path = os.path.join(RAW_DIR, sorted(files)[-1])
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

@router.get("/adms/health")
def health():
    raw_count = len([f for f in os.listdir(RAW_DIR) if f.endswith(".json")]) if os.path.exists(RAW_DIR) else 0
    parsed_count = 0
    if os.path.exists(PARSED_NDJSON):
        with open(PARSED_NDJSON, "r", encoding="utf-8") as f:
            parsed_count = sum(1 for _ in f)
    return {"ok": True, "raw": raw_count, "parsed": parsed_count}

# --- Export avanzado (JSON + CSV con filtros) ---
def _csv_header():
    return [
        "ts_ingest","sn","user_id","timestamp",
        "status","punch","workcode",
        "ext_verified","ext_status","ext_punch","ext_workcode",
        "ext_c7","ext_c8","ext_c9",
        "raw_source"
    ]

def _row_matches(ev, sn: str|None, user_id: str|None, since: str|None, until: str|None):
    if sn and str(ev.get("sn")) != str(sn):
        return False
    if user_id and str(ev.get("user_id")) != str(user_id):
        return False
    ts = str(ev.get("timestamp",""))
    if since and ts < since:
        return False
    if until and ts > until:
        return False
    return True

@router.get("/adms/export.json")
def export_json(
    sn: str | None = None,
    user_id: str | None = None,
    since: str | None = Query(None, description="YYYY-MM-DD HH:MM:SS"),
    until: str | None = Query(None, description="YYYY-MM-DD HH:MM:SS"),
    limit: int = 1000
):
    items = []
    if os.path.exists(PARSED_NDJSON):
        with open(PARSED_NDJSON, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                ext = ev.get("ext") or {}
                ev_flat = {
                    "ts_ingest": ev.get("ts_ingest"),
                    "sn": ev.get("sn"),
                    "user_id": ev.get("user_id"),
                    "timestamp": ev.get("timestamp"),
                    "status": ev.get("status"),
                    "punch": ev.get("punch"),
                    "workcode": ev.get("workcode"),
                    "ext_verified": ext.get("verified"),
                    "ext_status": ext.get("status"),
                    "ext_punch": ext.get("punch"),
                    "ext_workcode": ext.get("workcode"),
                    "ext_c7": ext.get("c7"),
                    "ext_c8": ext.get("c8"),
                    "ext_c9": ext.get("c9"),
                    "raw_source": ev.get("raw_source"),
                }
                if _row_matches(ev_flat, sn, user_id, since, until):
                    items.append(ev_flat)
    items = items[-limit:]
    return JSONResponse({"ok": True, "count": len(items), "items": items})

@router.get("/adms/export.csv")
def export_csv(
    sn: str | None = None,
    user_id: str | None = None,
    since: str | None = Query(None, description="YYYY-MM-DD HH:MM:SS"),
    until: str | None = Query(None, description="YYYY-MM-DD HH:MM:SS"),
    limit: int = 50000
):
    header = _csv_header()
    rows = []
    if os.path.exists(PARSED_NDJSON):
        with open(PARSED_NDJSON, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                ext = ev.get("ext") or {}
                ev_flat = {
                    "ts_ingest": ev.get("ts_ingest"),
                    "sn": ev.get("sn"),
                    "user_id": ev.get("user_id"),
                    "timestamp": ev.get("timestamp"),
                    "status": ev.get("status"),
                    "punch": ev.get("punch"),
                    "workcode": ev.get("workcode"),
                    "ext_verified": ext.get("verified"),
                    "ext_status": ext.get("status"),
                    "ext_punch": ext.get("punch"),
                    "ext_workcode": ext.get("workcode"),
                    "ext_c7": ext.get("c7"),
                    "ext_c8": ext.get("c8"),
                    "ext_c9": ext.get("c9"),
                    "raw_source": ev.get("raw_source"),
                }
                if _row_matches(ev_flat, sn, user_id, since, until):
                    rows.append([ev_flat.get(k) for k in header])
    rows = rows[-limit:]
    buff = StringIO()
    w = csv.writer(buff)
    w.writerow(header)
    w.writerows(rows)
    return PlainTextResponse(buff.getvalue(), media_type="text/csv")

# --- NUEVO: parseo rtlog --------------------------------------------
from urllib.parse import parse_qs

def _parse_rtlog(payload: dict) -> dict | None:
    """
    rtlog típico: PIN, Time, Status[, Workcode]
    Puede venir por query o form-urlencoded (POST), a veces múltiples líneas.
    """
    # Normaliza a str simples
    norm = {}
    for k, v in payload.items():
        if isinstance(v, list) and len(v) == 1:
            norm[k] = v[0]
        else:
            norm[k] = v

    pin  = norm.get("PIN") or norm.get("pin")
    tstr = norm.get("Time") or norm.get("time")
    st   = norm.get("Status") or norm.get("status")
    wc   = norm.get("Workcode") or norm.get("workcode")

    if not pin or not tstr:
        return None

    # Normaliza fecha
    ts_fmt = tstr
    try:
        ts_fmt = datetime.fromisoformat(" ".join(tstr.split()).replace(" ", "T")).isoformat(sep=" ")
    except Exception:
        pass

    return {
        "user_id": str(pin),
        "timestamp": ts_fmt,
        "status": str(st) if st is not None else None,
        "punch": None,          # rtlog no siempre lo manda
        "workcode": str(wc) if wc is not None else None,
        "ext": {}
    }

@router.api_route("/iclock/rtlog", methods=["GET","POST"])
@router.api_route("/iclock/rtlog/", methods=["GET","POST"])
async def iclock_rtlog(request: Request):
    # 1) recolecta query
    q = dict(request.query_params)

    # 2) recolecta form (si aplica) y mézclalo en q
    body = (await request.body()).decode(errors="ignore")
    if request.headers.get("content-type","").startswith("application/x-www-form-urlencoded") and body:
        try:
            form = parse_qs(body, keep_blank_values=True)
            # aplana valores
            for k, v in form.items():
                if isinstance(v, list) and len(v) == 1:
                    form[k] = v[0]
            # mezcla en q (sin pisar explícitamente query existentes)
            for k, v in form.items():
                q.setdefault(k, v)
        except Exception:
            pass

    # Guardar crudo
    _ingest("/iclock/rtlog", q, body)

    # Parsear un solo evento (rtlog suele ser 1 punch)
    ev = _parse_rtlog(q)
    if not ev and body:
        # algunos equipos mandan "PIN=...&Time=...&..." en el body pero no en query
        try:
            body_form = parse_qs(body, keep_blank_values=True)
            flat = {k:(v[0] if isinstance(v,list) and len(v)==1 else v) for k,v in body_form.items()}
            ev = _parse_rtlog(flat)
        except Exception:
            ev = None

    if ev:
        ts = _ts()
        sn = q.get("SN") or q.get("sn") or q.get("SerialNumber")
        ev_norm = {"ts_ingest": ts, "sn": sn, **ev, "raw_source": "/iclock/rtlog"}
        _append_ndjson(ev_norm)
        _append_csv_row(ev_norm)

    # Respuesta típica esperada por el firmware
    return PlainTextResponse("OK", status_code=200)
# --- FIN NUEVO -------------------------------------------------------