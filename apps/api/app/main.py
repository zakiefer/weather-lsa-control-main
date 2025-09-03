from fastapi import FastAPI, Depends, HTTPException, Request, APIRouter
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import os
import json
import logging
import app.tiles as tiles_router
import app.overlays as overlays_router
import app.pages as pages_router

app = FastAPI(title="Weather LSA Control API", version="0.1.0")

origins = os.getenv("WEB_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- JSON logging ---
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:  # type: ignore[override]
        payload = {
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload)


handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
logging.getLogger().handlers = [handler]
logging.getLogger().setLevel(logging.INFO)

# --- Auth stub (JWT) ---
class User(BaseModel):
    sub: str
    role: str


def get_current_user() -> User:
    # TODO: validate JWT from Authorization header using Clerk/Auth0 JWKS
    # For now, allow a stub user via env
    role = os.getenv("DEV_ROLE", "admin")
    return User(sub="dev", role=role)


@app.get("/healthz")
def healthz():
    return {"ok": True}


# Example v1 route namespace
v1 = APIRouter(prefix="/v1")


@v1.get("/map/state")
def map_state(request: Request, user: User = Depends(get_current_user)):
    # Minimal MapState compatible shape for initial wiring
    # Compute absolute API base for tile URLs
    host = request.headers.get("host") or request.url.netloc
    scheme = request.url.scheme
    api_base = f"{scheme}://{host}"
    is_fixture = bool(int(os.getenv("E2E_FIXTURE", "0")))
    fx_q = "?fixture=1" if is_fixture else ""
    return {
        "center": [37.8, -96.9],
        "zoom": 4,
        "opacities": {},
        "tiles": {
            "radar": f"{api_base}/v1/overlays/radar/{{z}}/{{x}}/{{y}}.png{fx_q}",
            "sat_true": f"{api_base}/v1/overlays/sat/true/{{z}}/{{x}}/{{y}}.jpg{fx_q}",
            "sat_ir": f"{api_base}/v1/overlays/sat/ir/{{z}}/{{x}}/{{y}}.jpg{fx_q}",
            "osm": f"{api_base}/v1/tiles/osm/{{z}}/{{x}}/{{y}}.png",
            "fixture": f"{api_base}/v1/tiles/fixture/{{z}}/{{x}}/{{y}}.png",
        },
        "fixture": is_fixture,
    }


@v1.get("/alerts/live")
def alerts_live(states: Optional[str] = None, user: User = Depends(get_current_user)):
    # TODO: Call existing src.weather_monitor and return features
    _states = (states or "IN,IL,KY").split(",")
    return {"states": _states, "features": []}


app.include_router(v1)
app.include_router(tiles_router.router)
app.include_router(overlays_router.router)
app.include_router(pages_router.router)


# Error handlers
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail, "status": exc.status_code},
    )

# Optional versioned health for consistency
@v1.get("/health")
def v1_health():
    return {"ok": True}

