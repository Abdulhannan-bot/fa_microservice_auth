from fastapi import FastAPI

from core.config import get_settings
from routers.auth import router as auth_router

settings = get_settings()

app = FastAPI(title=settings.APP_NAME)
app.include_router(auth_router)


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}

