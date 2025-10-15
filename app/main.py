from fastapi import FastAPI
from app.routers import adms as adms_router
from app.routers import zk

app = FastAPI()

@app.get("/")
def home():
    return {"ok": True, "msg": "Hot reload funcionando 🔥"}

@app.get("/health")
def health():
    return {"status": "healthy"}

app.include_router(adms_router.router) 
app.include_router(zk.router)

