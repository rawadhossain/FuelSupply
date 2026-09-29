from fastapi import FastAPI

app = FastAPI(title="Fuel Supply Intelligence Service")


@app.get("/health")
async def health() -> dict:
    return {"status": "healthy", "service": "intelligence"}
