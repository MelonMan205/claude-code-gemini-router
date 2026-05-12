"""
Claude Code → Gemini proxy.

Receives Anthropic Messages API requests from Claude Code and forwards them
to Google Gemini, translating both the request and the response.

Set GEMINI_API_KEY in the environment before starting.
Optionally set PROXY_API_KEY to require a bearer token from clients.

Runs on port 9090.  Set with PORT env var if needed.
"""

import json
import logging
import os
import uuid
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from converter import anthropic_to_gemini, gemini_to_anthropic, stream_gemini_to_anthropic

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("gemini-router")

GEMINI_API_KEY: str = os.environ.get("GEMINI_API_KEY", "")
PROXY_API_KEY: str = os.environ.get("PROXY_API_KEY", "")   # optional client auth
PORT: int = int(os.environ.get("PORT", "9090"))
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

# Models advertised to Claude Code via GET /v1/models
GEMINI_MODELS = [
    "gemini-2.5-pro",
    "gemini-2.5-pro-preview-05-06",
    "gemini-2.5-flash",
    "gemini-2.5-flash-preview-04-17",
    "gemini-2.0-flash",
    "gemini-2.0-flash-thinking-exp",
    "gemini-1.5-pro",
    "gemini-1.5-flash",
]

if not GEMINI_API_KEY:
    logger.warning("GEMINI_API_KEY is not set — requests to Gemini will fail.")


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http = httpx.AsyncClient(timeout=httpx.Timeout(300.0))
    logger.info("HTTP client ready. Listening on :%s", PORT)
    yield
    await app.state.http.aclose()


app = FastAPI(title="Claude Code Gemini Router", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------

def _check_proxy_key(request: Request) -> bool:
    """Return True if proxy key check passes (or is not configured)."""
    if not PROXY_API_KEY:
        return True
    key = request.headers.get("x-api-key") or ""
    if not key:
        auth = request.headers.get("authorization", "")
        key = auth.removeprefix("Bearer ").strip()
    return key == PROXY_API_KEY


# ---------------------------------------------------------------------------
# Core handler
# ---------------------------------------------------------------------------

async def _handle_messages(request: Request) -> Response:
    if not _check_proxy_key(request):
        return JSONResponse(
            status_code=401,
            content={"type": "error", "error": {"type": "authentication_error", "message": "Invalid proxy API key."}},
        )

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"type": "error", "error": {"type": "invalid_request_error", "message": "Invalid JSON body."}},
        )

    model: str = body.get("model", "gemini-2.5-pro")
    stream: bool = body.get("stream", False)
    request_id = f"msg_{uuid.uuid4().hex}"

    logger.info("→ model=%s stream=%s id=%s", model, stream, request_id)

    try:
        gemini_model, gemini_body = anthropic_to_gemini(body)
    except Exception as exc:
        logger.exception("Request conversion failed")
        return JSONResponse(
            status_code=400,
            content={"type": "error", "error": {"type": "invalid_request_error", "message": str(exc)}},
        )

    http: httpx.AsyncClient = request.app.state.http

    if stream:
        url = (
            f"{GEMINI_BASE_URL}/models/{gemini_model}:streamGenerateContent"
            f"?key={GEMINI_API_KEY}&alt=sse"
        )

        async def generate():
            try:
                async with http.stream("POST", url, json=gemini_body) as resp:
                    if resp.status_code != 200:
                        raw = await resp.aread()
                        logger.error("Gemini %s: %s", resp.status_code, raw[:300])
                        err = json.dumps({
                            "type": "error",
                            "error": {
                                "type": "api_error",
                                "message": f"Gemini returned {resp.status_code}: {raw.decode()[:200]}",
                            },
                        })
                        yield f"event: error\ndata: {err}\n\n"
                        return

                    async for event in stream_gemini_to_anthropic(resp, gemini_model, request_id):
                        yield event

            except httpx.TimeoutException:
                err = json.dumps({"type": "error", "error": {"type": "api_error", "message": "Request to Gemini timed out."}})
                yield f"event: error\ndata: {err}\n\n"
            except Exception as exc:
                logger.exception("Streaming error")
                err = json.dumps({"type": "error", "error": {"type": "api_error", "message": str(exc)}})
                yield f"event: error\ndata: {err}\n\n"

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    # Non-streaming
    url = f"{GEMINI_BASE_URL}/models/{gemini_model}:generateContent?key={GEMINI_API_KEY}"
    try:
        resp = await http.post(url, json=gemini_body)
    except httpx.TimeoutException:
        return JSONResponse(
            status_code=504,
            content={"type": "error", "error": {"type": "api_error", "message": "Request to Gemini timed out."}},
        )
    except Exception as exc:
        logger.exception("HTTP error")
        return JSONResponse(
            status_code=502,
            content={"type": "error", "error": {"type": "api_error", "message": str(exc)}},
        )

    if resp.status_code != 200:
        logger.error("Gemini %s: %s", resp.status_code, resp.text[:300])
        return JSONResponse(
            status_code=resp.status_code,
            content={
                "type": "error",
                "error": {"type": "api_error", "message": f"Gemini error: {resp.text[:500]}"},
            },
        )

    try:
        gemini_resp = resp.json()
        anthropic_resp = gemini_to_anthropic(gemini_resp, gemini_model, request_id)
        logger.info("← id=%s stop_reason=%s", request_id, anthropic_resp.get("stop_reason"))
        return JSONResponse(content=anthropic_resp)
    except Exception as exc:
        logger.exception("Response conversion failed")
        return JSONResponse(
            status_code=500,
            content={"type": "error", "error": {"type": "api_error", "message": str(exc)}},
        )


# ---------------------------------------------------------------------------
# Routes — mounted at both /v1/* and /gemini/v1/* to accommodate
# whether Cloudflare strips the /gemini prefix or passes it through.
# ---------------------------------------------------------------------------

@app.post("/v1/messages")
@app.post("/gemini/v1/messages")
async def messages(request: Request):
    return await _handle_messages(request)


@app.get("/v1/models")
@app.get("/gemini/v1/models")
async def list_models():
    data = [
        {
            "type": "model",
            "id": m,
            "display_name": m,
            "created_at": "2025-01-01T00:00:00Z",
        }
        for m in GEMINI_MODELS
    ]
    return JSONResponse(content={"data": data})


@app.get("/health")
@app.get("/gemini/health")
async def health():
    return {"status": "ok", "gemini_key_set": bool(GEMINI_API_KEY)}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, log_level="info")
