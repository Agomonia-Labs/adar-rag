# main.py
import os
from contextlib import asynccontextmanager
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from database.connection import init_pool
from database.models     import create_tables
from auth.router         import router as auth_router
from routes.documents    import router as docs_router
from routes.chat         import router as chat_router
from routes.admin        import router as admin_router
from routes.summarize    import router as summarize_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_pool()
    await create_tables()
    print(f"✓ DocIntel ready  |  LLM: {os.getenv('LLM_PROVIDER','openai')}  |  VectorDB: pgvector")
    yield


app = FastAPI(title="DocIntel API", version="4.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:3000",
        "https://adar.agomoniai.com",
        "https://www.adar.agomoniai.com",
        "https://docintel.adar.agomoniai.com",
        "https://www.docintel.adar.agomoniai.com",
        # Firebase default URLs
        "https://docintel-adar.web.app",
        "https://docintel-backend-tzwvc47f5q-uc.a.run.app",
        "*"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router,      prefix="/api/auth",      tags=["auth"])
app.include_router(docs_router,      prefix="/api/documents",  tags=["documents"])
app.include_router(chat_router,      prefix="/api/chat",       tags=["chat"])
app.include_router(admin_router,     prefix="/api/admin",      tags=["admin"])
app.include_router(summarize_router, prefix="/api/summarize",  tags=["summarize"])


@app.get("/api/health")
async def health():
    return {"status": "ok", "llm": os.getenv("LLM_PROVIDER", "openai")}


_static = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_static):
    app.mount("/", StaticFiles(directory=_static, html=True), name="spa")