# main.py — DocIntel FastAPI entry point
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from routes.documents import router as documents_router
from routes.chat import router as chat_router
from services.vectordb import init_firestore


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    try:
        init_firestore()
        print("✓ Firestore connected")
    except Exception as e:
        print(f"✗ Firestore init failed: {e}")
        print("  Check FIREBASE_SERVICE_ACCOUNT_PATH / FIREBASE_SERVICE_ACCOUNT_JSON in .env")
        raise
    yield
    # teardown (if needed)


app = FastAPI(title="DocIntel API", version="1.0.0", lifespan=lifespan)

# ── CORS — allow React dev server (port 5173) and production origin ───────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── API routers ───────────────────────────────────────────────────────────────
app.include_router(documents_router, prefix="/api/documents", tags=["documents"])
app.include_router(chat_router,      prefix="/api/chat",      tags=["chat"])


@app.get("/api/health")
async def health():
    return {"status": "ok"}


# ── In production: serve the built React app from ../frontend/dist ────────────
frontend_dist = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
if os.path.isdir(frontend_dist):
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="spa")
