#!/usr/bin/env bash
set -e
# servidor principal (API) en 8000
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload &
# receptor ADMS en 8081 (misma app; reutiliza /adms/)
uvicorn app.main:app --host 0.0.0.0 --port 8081 --reload
