import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.config import get_settings
from app.database import SessionLocal, initialize_database
from app.services.extraction import adopt_existing_extractions
from app.services.zotero_config import load_zotero_config
from app.services.zotero_sync import ZoteroUnavailable, sync_library as sync_zotero_library


ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "static"
logger = logging.getLogger(__name__)


def _run_zotero_sync() -> None:
    with SessionLocal() as session:
        try:
            sync_zotero_library(session)
        except ZoteroUnavailable as exc:
            logger.warning("Automatic Zotero sync skipped: %s", exc)
        except Exception:
            logger.exception("Automatic Zotero sync failed")


async def _periodic_zotero_sync() -> None:
    await asyncio.sleep(60)
    while True:
        config = load_zotero_config()
        if config.enabled and config.api_key:
            await asyncio.to_thread(_run_zotero_sync)
        await asyncio.sleep(15 * 60)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    settings.storage_path.mkdir(parents=True, exist_ok=True)
    settings.figure_path.mkdir(parents=True, exist_ok=True)
    settings.model_cache_path.mkdir(parents=True, exist_ok=True)
    settings.llm_config_path.parent.mkdir(parents=True, exist_ok=True)
    initialize_database()
    with SessionLocal() as session:
        adopt_existing_extractions(session)
    zotero_task = asyncio.create_task(_periodic_zotero_sync())
    try:
        yield
    finally:
        zotero_task.cancel()
        with suppress(asyncio.CancelledError):
            await zotero_task


app = FastAPI(
    title="Paperlib",
    version="0.1.0",
    description="个人单机、证据优先的论文与书籍知识库",
    lifespan=lifespan,
)
app.include_router(router)
app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")
