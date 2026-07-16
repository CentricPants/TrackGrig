"""
main.py
FastAPI backend for PhantomTrack.

REST (all require a JWT except /auth/token and /health):
  GET  /devices              -> all known devices + last position
  GET  /devices/{id}/history -> location history for one device
  GET  /alerts                -> recent anomaly alerts
  POST /auth/token            -> issue a JWT (demo user: admin / admin123)
  GET  /health                 -> liveness check, no auth

WebSocket (requires a JWT passed as a query param):
  GET  /ws?token=<jwt>        -> live push of location + alert events

Run with:
  uvicorn main:app --reload --port 8000
(Mosquitto must already be running with TLS+auth configured - see
mosquitto/README section in the top-level README.)
"""

import asyncio
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, status, WebSocket, WebSocketDisconnect
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.middleware.cors import CORSMiddleware
from jose import jwt, JWTError

import models
from mqtt_client import start_mqtt_client_background
from ws_manager import manager

# --- config -----------------------------------------------------------
# SECRET_KEY MUST be overridden via environment variable outside of local
# dev - the fallback here is intentionally obvious so nobody mistakes it
# for something safe to ship.
SECRET_KEY = os.environ.get("PHANTOMTRACK_SECRET", "dev-secret-change-me")
ALGORITHM = "HS256"
TOKEN_EXPIRE_MINUTES = 60

# Demo dashboard credentials (replace with a real user store in production)
DASHBOARD_USER = {"username": "admin", "password": "admin123"}

# --- app setup ----------------------------------------------------------
app = FastAPI(title="PhantomTrack Fleet Tracking API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten this to your actual dashboard origin in production
    allow_methods=["*"],
    allow_headers=["*"],
)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/token")


@app.on_event("startup")
async def on_startup():
    # Bind the running asyncio loop so the MQTT thread can safely schedule
    # WebSocket broadcasts onto it later (see ws_manager.py).
    manager.bind_loop(asyncio.get_running_loop())
    models.init_db()
    start_mqtt_client_background()


# --- auth ---------------------------------------------------------------
def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def _decode_token(token: str) -> Optional[str]:
    """Returns the username ('sub' claim) if the token is valid, else None."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None


def get_current_user(token: str = Depends(oauth2_scheme)) -> str:
    username = _decode_token(token)
    if username is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return username


@app.post("/auth/token")
def login(form_data: OAuth2PasswordRequestForm = Depends()):
    if form_data.username != DASHBOARD_USER["username"] or form_data.password != DASHBOARD_USER["password"]:
        raise HTTPException(status_code=400, detail="Incorrect username or password")
    token = create_access_token({"sub": form_data.username})
    return {"access_token": token, "token_type": "bearer"}


# --- protected read endpoints ---------------------------------------------
# Every one of these now requires a valid JWT. A request with no token, an
# expired token, or a token signed with the wrong secret gets a 401.

@app.get("/devices")
def get_devices(user: str = Depends(get_current_user)):
    return models.list_devices()


@app.get("/devices/{device_id}/history")
def get_device_history(device_id: str, limit: int = 200, user: str = Depends(get_current_user)):
    return models.device_history(device_id, limit)


@app.get("/alerts")
def get_alerts(limit: int = 100, user: str = Depends(get_current_user)):
    return models.list_alerts(limit)


@app.get("/health")
def health():
    return {"status": "ok"}


# --- WebSocket: live push of location + alert events -----------------------
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: Optional[str] = None):
    # Browsers can't set custom headers on the WebSocket handshake, so the
    # JWT travels as a query param instead: /ws?token=<jwt>. Validate it
    # BEFORE accepting the connection - an unauthenticated socket should
    # never be added to the broadcast list.
    username = _decode_token(token) if token else None
    if username is None:
        await websocket.close(code=1008)  # 1008 = policy violation
        return

    await manager.connect(websocket)
    try:
        while True:
            # We don't need the client to send anything; this just keeps
            # the coroutine alive to detect disconnects.
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
