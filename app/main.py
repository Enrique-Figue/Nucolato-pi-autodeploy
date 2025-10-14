from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def home():
    return {"ok": True, "msg": "Versión autodeploy OK 🚀"}

@app.get("/health")
def health():
    return {"status": "healthy"}
