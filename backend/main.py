from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def inicio():
    return {
        "aplicacion": "El Pergamino Perdido",
        "estado": "Funcionando"
    } 