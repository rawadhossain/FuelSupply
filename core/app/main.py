from fastapi import FastAPI

app = FastAPI(title="Fuel Supply Core Service")


@app.get("/health")
async def health() -> dict:
    return {"status": "healthy", "service": "core"}
