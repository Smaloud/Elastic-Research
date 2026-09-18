from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.config import get_settings
from app.database import SessionLocal, initialize_database
from app.services.extraction import adopt_existing_extractions


ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "static"


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
    yield


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
