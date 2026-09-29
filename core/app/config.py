import os

SIMULATOR_BASE_URL = os.environ.get("SIMULATOR_BASE_URL", "http://localhost:8000")
POLL_INTERVAL_SECONDS = float(os.environ.get("POLL_INTERVAL_SECONDS", "5"))
SSE_GAP_THRESHOLD_TICKS = int(os.environ.get("SSE_GAP_THRESHOLD_TICKS", "2"))
SSE_GAP_CHECK_INTERVAL_SECONDS = float(os.environ.get("SSE_GAP_CHECK_INTERVAL_SECONDS", "5"))
DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+psycopg://fuelsupply:fuelsupply@localhost:5432/fuelsupply"
)
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
