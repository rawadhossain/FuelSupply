from fastapi import FastAPI
from fuelsupply_shared.observability import setup_observability

app = FastAPI(title="Fuel Supply Intelligence Service")

setup_observability(app, service="intelligence")


@app.get("/health")
async def health() -> dict:
    return {"status": "healthy", "service": "intelligence"}
