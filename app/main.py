from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def home():
    return {"ok": True, "msg": "Hola desde la Raspberry Pi"}

@app.get("/health")
def health():
    return {"status": "healthy"}
