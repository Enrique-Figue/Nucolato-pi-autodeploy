#!/usr/bin/env python3
import argparse
import os
import sys
from datetime import datetime

# Import robusto: pyzk se instala como paquete "pyzk" pero se importa como "zk"
try:
    from zk import ZK, const
except Exception as e:
    print("[ERROR] No se pudo importar 'zk' (paquete 'pyzk'). "
          "Instala dentro del contenedor: pip install -U pyzk", file=sys.stderr)
    raise

def connect_zk(ip, port=4370, password=0, timeout=5, force_udp=False, ommit_ping=False):
    zk = ZK(
        ip=ip,
        port=port,
        timeout=timeout,
        password=password,
        force_udp=force_udp,
        ommit_ping=ommit_ping
    )
    try:
        conn = zk.connect()
        return conn
    except Exception as e:
        print(f"[ERROR] No se pudo conectar a {ip}:{port} (udp={force_udp}). Motivo: {e}", file=sys.stderr)
        return None

def main():
    parser = argparse.ArgumentParser(description="Prueba de conexión y lectura de checador ZKTeco")
    parser.add_argument("--ip", default=os.getenv("ZK_IP"), help="IP del checador (ej. 192.168.1.163)")
    parser.add_argument("--port", type=int, default=int(os.getenv("ZK_PORT", "4370")), help="Puerto (default 4370)")
    parser.add_argument("--password", type=int, default=int(os.getenv("ZK_PASSWORD", "0")), help="Password del dispositivo (si aplica)")
    parser.add_argument("--timeout", type=int, default=int(os.getenv("ZK_TIMEOUT", "5")), help="Timeout de conexión en segundos")
    parser.add_argument("--no-ping", action="store_true", help="Omitir ping ICMP (útil en Docker)")
    args = parser.parse_args()

    if not args.ip:
        print("[ERROR] Falta IP. Usa --ip 192.168.x.x o variable de entorno ZK_IP", file=sys.stderr)
        sys.exit(1)

    print(f"[INFO] Probando conexión TCP a {args.ip}:{args.port} ...")
    conn = connect_zk(args.ip, args.port, args.password, args.timeout, force_udp=False, ommit_ping=args.no_ping)
    if not conn:
        print(f"[WARN] Reintentando por UDP a {args.ip}:{args.port} ...")
        conn = connect_zk(args.ip, args.port, args.password, args.timeout, force_udp=True, ommit_ping=args.no_ping)

    if not conn:
        sys.exit(2)

    try:
        print("[OK] Conectado. Obteniendo información del dispositivo...")
        info_items = []
        try:
            info_items.append(("firmware", conn.get_firmware_version()))
        except Exception as e:
            info_items.append(("firmware", f"error: {e}"))
        try:
            info_items.append(("platform", conn.get_platform()))
        except Exception as e:
            info_items.append(("platform", f"error: {e}"))
        try:
            info_items.append(("serial", conn.get_serialnumber()))
        except Exception as e:
            info_items.append(("serial", f"error: {e}"))
        try:
            # algunos firmwares no exponen esto
            name = conn.get_device_name() if hasattr(conn, "get_device_name") else None
            info_items.append(("device_name", name))
        except Exception as e:
            info_items.append(("device_name", f"error: {e}"))

        for k, v in info_items:
            print(f"  - {k}: {v}")

        # Usuarios (muestra los primeros 5)
        try:
            users = conn.get_users()
            print(f"[OK] Usuarios encontrados: {len(users)}")
            for u in users[:5]:
                print(f"  - UID:{getattr(u,'uid',None)}  USER_ID:{getattr(u,'user_id',None)}  NAME:{getattr(u,'name',None)}")
        except Exception as e:
            print(f"[WARN] No se pudieron leer usuarios: {e}")

        # Asistencias (últimas 10)
        try:
            print("[INFO] Leyendo asistencias (últimas 10 si hay muchas)...")
            attendance = conn.get_attendance()
            if not attendance:
                print("  (sin eventos)")
            else:
                try:
                    attendance = sorted(attendance, key=lambda a: getattr(a, 'timestamp', datetime.min))
                except Exception:
                    pass
                for a in attendance[-10:]:
                    ts = getattr(a, "timestamp", None)
                    uid = getattr(a, "user_id", None) or getattr(a, "uid", None)
                    status = getattr(a, "status", None)
                    punch = getattr(a, "punch", None)
                    print(f"  - {ts}  UID:{uid}  status:{status}  punch:{punch}")
        except Exception as e:
            print(f"[WARN] No se pudieron leer asistencias: {e}")

        # Hora del dispositivo
        try:
            dev_time = conn.get_time()
            print(f"[OK] Hora del dispositivo: {dev_time}")
        except Exception as e:
            print(f"[WARN] No se pudo leer la hora: {e}")

    finally:
        try:
            conn.disconnect()
            print("[OK] Desconectado.")
        except Exception:
            pass

if __name__ == "__main__":
    main()
