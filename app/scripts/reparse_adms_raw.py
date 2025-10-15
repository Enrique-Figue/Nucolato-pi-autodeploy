#!/usr/bin/env python3
import os, json
from app.routers import adms as adms

def main():
    raw_dir = os.path.join(adms.BASE_DIR, "raw")
    files = sorted(f for f in os.listdir(raw_dir) if f.endswith(".json"))
    count = 0
    for f in files:
        path = os.path.join(raw_dir, f)
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        q = data.get("query", {})
        body = data.get("body", "") or ""
        # re-ingesta solo ATTLOG
        if str(q.get("table", "")).upper() == "ATTLOG" or ("ATTLOG=" in body) or ("\t" in body):
            adms._ingest(data.get("path", "/reparse"), q, body)
            count += 1
    print("Reprocesados:", count)

if __name__ == "__main__":
    main()
