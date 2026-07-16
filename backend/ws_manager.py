"""
ws_manager.py
Tracks connected dashboard WebSocket clients and broadcasts events to them.

The tricky part: MQTT messages arrive on a background thread (paho-mqtt's
own network loop thread), but WebSocket sends must happen on the asyncio
event loop that FastAPI/uvicorn is running. `asyncio.run_coroutine_threadsafe`
is the standard bridge for "call this coroutine from a different thread."
"""

import asyncio
import json
from typing import Optional

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []
        self.loop: Optional[asyncio.AbstractEventLoop] = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop):
        """Called once at FastAPI startup so background threads can safely
        schedule broadcasts onto the correct event loop."""
        self.loop = loop

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active:
            self.active.remove(websocket)

    async def broadcast(self, event: dict):
        payload = json.dumps(event)
        dead = []
        for ws in self.active:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    def broadcast_from_thread(self, event: dict):
        """
        Thread-safe entry point for non-async code (the MQTT callback
        thread) to push an event out to every connected dashboard.
        No-ops quietly if the event loop isn't bound yet or there are no
        clients connected - this is expected during startup/idle periods.
        """
        if self.loop is None:
            return
        asyncio.run_coroutine_threadsafe(self.broadcast(event), self.loop)


manager = ConnectionManager()
