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

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from services.tracing import current_trace_id, new_trace_id


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
        from database.models import create_additional_tables, create_eval_tables
        await create_additional_tables()
        await create_eval_tables()
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


@app.middleware("http")
async def trace_id_middleware(request: Request, call_next):
    trace_id = request.headers.get("X-Trace-Id") or new_trace_id()
    request.state.trace_id = trace_id
    token = current_trace_id.set(trace_id)
    try:
        response = await call_next(request)
        response.headers["X-Trace-Id"] = trace_id
        return response
    except Exception as exc:
        log.exception(
            "Unhandled request error trace=%s method=%s path=%s error=%s",
            trace_id,
            request.method,
            request.url.path,
            exc,
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error", "trace_id": trace_id},
            headers={"X-Trace-Id": trace_id},
        )
    finally:
        current_trace_id.reset(token)


@app.get("/api/health")
async def health():
    from database.connection import _pool
    trace_tables = []
    if _pool is not None:
        try:
            async with _pool.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT table_name FROM information_schema.tables
                       WHERE table_schema='public'
                         AND table_name = ANY($1::text[])
                       ORDER BY table_name""",
                    ["trace_flows", "trace_spans", "trace_llm_events"],
                )
                trace_tables = [r["table_name"] for r in rows]
        except Exception:
            trace_tables = []
    return {
        "status":       "ok",
        "llm":          os.getenv("LLM_PROVIDER", "unknown"),
        "db_connected": _pool is not None,
        "trace_tables": trace_tables,
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
    ("routes.workspaces",     "workspaces_router",     "/api/workspaces",    ["workspaces"]),
    ("routes.tags",           "tags_router",           "/api/tags",          ["tags"]),
    ("routes.billing",        "billing_router",        "/api/billing",       ["billing"]),
    ("routes.evals",          "evals_router",          "/api/evals",         ["evals"]),
    ("routes.voice",          "voice_router",          "/api/voice",         ["voice"]),
    ("routes.traces",         "traces_router",         "/api/traces",        ["traces"]),
    ("routes.lease",          "lease_router",          "/api/lease",         ["lease"]),
    ("routes.healthcare",     "healthcare_router",     "/api/healthcare",    ["healthcare"]),
    ("routes.finance_tax",    "finance_tax_router",    "/api/finance-tax",   ["finance-tax"]),
    ("routes.video",          "video_router",          "/api/video",         ["video"]),
    ("routes.restaurant",     "restaurant_router",     "/api/restaurant",    ["restaurant"]),
    ("routes.agent_evals",    "agent_evals_router",    "/api/agent-evals",   ["agent-evals"]),
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
