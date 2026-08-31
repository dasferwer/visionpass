import logging
import time
from uuid import uuid4

from fastapi import FastAPI, Request, Response

from visionpass.api import router
from visionpass.config import get_settings

settings = get_settings()
logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logging.getLogger("pika").setLevel(logging.WARNING)
logger = logging.getLogger("visionpass.http")

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    summary="Privacy-aware access control with ready-made CV integration",
)
app.include_router(router)


@app.middleware("http")
async def request_context(request: Request, call_next) -> Response:  # type: ignore[no-untyped-def]
    request_id = request.headers.get("X-Request-ID", str(uuid4()))
    started = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    logger.info(
        "request_id=%s method=%s path=%s status=%s duration_ms=%s",
        request_id,
        request.method,
        request.url.path,
        response.status_code,
        round((time.perf_counter() - started) * 1000, 2),
    )
    return response


@app.get("/", include_in_schema=False)
def root() -> dict[str, str]:
    return {"service": settings.app_name, "docs": "/docs", "health": "/health"}
