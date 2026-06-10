import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .db import SessionLocal
from .notifications import build_adapter_registry
from .notifications.scheduler import ReminderScheduler
from .routers import account as account_router
from .routers import auth as auth_router
from .routers import notes as notes_router
from .routers import notifications as notifications_router
from .routers import tags as tags_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    task: asyncio.Task | None = None
    # Registry is needed by the notifications endpoints even when the
    # scheduler loop is off (tests), so it always lives on app.state.
    registry = build_adapter_registry(settings)
    app.state.adapter_registry = registry
    app.state.scheduler = None
    if settings.scheduler_enabled:
        scheduler = ReminderScheduler(SessionLocal, registry)
        app.state.scheduler = scheduler
        task = asyncio.create_task(scheduler.run())
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


app = FastAPI(title="Notes API", version="0.1.0", lifespan=lifespan)

_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


app.include_router(auth_router.router, prefix="/api")
app.include_router(account_router.router, prefix="/api")
app.include_router(notes_router.router, prefix="/api")
app.include_router(notifications_router.router, prefix="/api")
app.include_router(tags_router.router, prefix="/api")
