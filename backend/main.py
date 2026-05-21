# main.py
import os, sys, logging, asyncio
from contextlib import asynccontextmanager
from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s — %(message)s",
    stream=sys.stdout,
    force=True,
)
log = logging.getLogger("docintel")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("═══════════════════════════════════")
    log.info("DocIntel starting up")
    log.info(f"  PORT         = {os.getenv('PORT', '8080')}")
    log.info(f"  LLM_PROVIDER = {os.getenv('LLM_PROVIDER', 'NOT SET')}")
    log.info(f"  EMBEDDING_DIM= {os.getenv('EMBEDDING_DIM', 'NOT SET')}")
    log.info(f"  GCS_BUCKET   = {os.getenv('GCS_BUCKET_NAME', 'NOT SET')}")
    db_url = os.getenv("DATABASE_URL", "")
    log.info(f"  DATABASE_URL = {'SET (length=' + str(len(db_url)) + ')' if db_url else 'NOT SET ← PROBLEM'}")
    if db_url:
        safe = db_url.split("@")[0].split(":")[0] + ":***@" + db_url.split("@")[-1] if "@" in db_url else db_url
        log.info(f"  DB (masked)  = {safe}")
    log.info("═══════════════════════════════════")

    try:
        from database.connection import init_pool
        from database.models     import create_tables
        log.info("▶ Connecting to database...")
        await init_pool()
        log.info("✓ Database pool ready")
        await create_tables()
        from database.models import create_additional_tables
        await create_additional_tables()
        log.info("✓ Schema ready")
    except Exception as e:
        log.exception(f"✗ Database startup failed: {e}")

    log.info("✓ Server ready — listening on port " + os.getenv("PORT", "8080"))
    yield
    log.info("Shutting down DocIntel")


app = FastAPI(title="DocIntel API", version="4.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://docintel.adar.agomoniai.com",
        "https://docintel-adar.web.app",
        "http://localhost:5173",
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health():
    from database.connection import _pool
    return {
        "status":       "ok",
        "llm":          os.getenv("LLM_PROVIDER", "unknown"),
        "db_connected": _pool is not None,
    }


@app.get("/api/health/email")
async def health_email():
    """Test Gmail SMTP connection — no email sent, no auth required."""
    from services.email import test_smtp_connection
    return await test_smtp_connection()


# Register each router individually — a failure in one doesn't block others
_ROUTERS = [
    ("auth.router",           "auth_router",           "/api/auth",          ["auth"]),
    ("routes.documents",      "docs_router",           "/api/documents",     ["documents"]),
    ("routes.chat",           "chat_router",           "/api/chat",          ["chat"]),
    ("routes.admin",          "admin_router",          "/api/admin",         ["admin"]),
    ("routes.summarize",      "summarize_router",      "/api/summarize",     ["summarize"]),
    ("routes.password_reset", "password_reset_router", "/api/auth",          ["auth"]),
    ("routes.chat_sessions",  "sessions_router",       "/api/chat/sessions", ["chat-sessions"]),
    ("routes.compare",        "compare_router",        "/api/compare",       ["compare"]),
    ("routes.feedback",       "feedback_router",       "/api/feedback",      ["feedback"]),
    ("routes.usage",          "usage_router",          "/api/usage",         ["usage"]),
]

for _module, _attr, _prefix, _tags in _ROUTERS:
    try:
        import importlib
        _mod = importlib.import_module(_module)
        _router = getattr(_mod, "router")
        app.include_router(_router, prefix=_prefix, tags=_tags)
        log.info(f"  ✓ {_prefix}")
    except Exception as _e:
        log.error(f"  ✗ Failed to register {_module}: {_e}")


# Serve built React app in production
_static = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_static):
    app.mount("/", StaticFiles(directory=_static, html=True), name="spa")