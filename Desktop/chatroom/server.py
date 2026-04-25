"""
LAN Chat Server with Excel-based Authentication
================================================
Requires: pip install websockets pandas openpyxl watchdog
Run:      python server.py
"""

import asyncio
import json
import logging
import os
import threading
import time

import pandas as pd
import websockets

# ── Configuration ──────────────────────────────────────────────
HOST = "0.0.0.0"       # Listen on all interfaces
PORT = 8765
EXCEL_FILE = "users.xlsx"
RELOAD_INTERVAL = 30   # Auto-reload Excel every N seconds (0 = disable)
# ───────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Shared state ───────────────────────────────────────────────
user_store: dict[str, dict] = {}   # { username: {password, role} }
authenticated_clients: dict = {}   # { websocket: {username, role} }
# ───────────────────────────────────────────────────────────────


def load_users(filepath: str) -> dict:
    """Load users from Excel into a dict keyed by lowercase username."""
    if not os.path.exists(filepath):
        log.error(f"Excel file not found: {filepath}")
        return {}
    try:
        df = pd.read_excel(filepath, dtype=str).fillna("")
        # Normalise column names to lowercase / strip whitespace
        df.columns = [c.strip().lower() for c in df.columns]
        required = {"username", "password"}
        if not required.issubset(set(df.columns)):
            log.error(f"Excel must contain columns: {required}. Found: {list(df.columns)}")
            return {}
        store = {}
        for _, row in df.iterrows():
            username = row["username"].strip().lower()
            if username:
                store[username] = {
                    "password": row["password"].strip(),
                    "role": row.get("role", "user").strip() or "user",
                }
        log.info(f"Loaded {len(store)} user(s) from {filepath}")
        return store
    except Exception as exc:
        log.error(f"Failed to read Excel: {exc}")
        return {}


def background_reload(interval: int):
    """Periodically reload the Excel file."""
    global user_store
    while True:
        time.sleep(interval)
        fresh = load_users(EXCEL_FILE)
        if fresh:
            user_store = fresh
            log.info("User store refreshed from Excel")


async def broadcast(message: str, sender_ws=None):
    """Send a message to every authenticated client except the sender."""
    targets = [ws for ws in authenticated_clients if ws is not sender_ws]
    if targets:
        await asyncio.gather(*[ws.send(message) for ws in targets], return_exceptions=True)


async def handle_client(websocket):
    """
    Message protocol (JSON):
      Client → Server:
        { "type": "login",   "username": "...", "password": "..." }
        { "type": "message", "text": "..." }
        { "type": "reload" }          ← admin-only: reload Excel
      Server → Client:
        { "type": "auth_ok",    "username": "...", "role": "..." }
        { "type": "auth_fail",  "reason": "..." }
        { "type": "chat",       "from": "...", "text": "..." }
        { "type": "system",     "text": "..." }
        { "type": "user_list",  "users": [...] }
        { "type": "error",      "text": "..." }
    """
    addr = websocket.remote_address
    log.info(f"Connection from {addr}")

    global user_store

    async def send(data: dict):
        try:
            await websocket.send(json.dumps(data))
        except Exception:
            pass

    async def broadcast_user_list():
        users = [info["username"] for info in authenticated_clients.values()]
        msg = json.dumps({"type": "user_list", "users": users})
        await asyncio.gather(*[ws.send(msg) for ws in authenticated_clients], return_exceptions=True)

    try:
        async for raw in websocket:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await send({"type": "error", "text": "Invalid JSON"})
                continue

            msg_type = data.get("type", "")

            # ── LOGIN ────────────────────────────────────────────────
            if msg_type == "login":
                if websocket in authenticated_clients:
                    await send({"type": "error", "text": "Already authenticated"})
                    continue

                username = data.get("username", "").strip().lower()
                password = data.get("password", "").strip()

                entry = user_store.get(username)
                if entry and entry["password"] == password:
                    role = entry["role"]
                    authenticated_clients[websocket] = {"username": username, "role": role}
                    log.info(f"  ✅ Auth OK: {username} ({role}) from {addr}")
                    await send({"type": "auth_ok", "username": username, "role": role})

                    # Notify everyone
                    join_msg = json.dumps({"type": "system", "text": f"👋 {username} joined the chat"})
                    await asyncio.gather(*[ws.send(join_msg) for ws in authenticated_clients], return_exceptions=True)
                    await broadcast_user_list()
                else:
                    log.warning(f"  ❌ Auth FAIL: '{username}' from {addr}")
                    await send({"type": "auth_fail", "reason": "Invalid username or password"})

            # ── CHAT MESSAGE ─────────────────────────────────────────
            elif msg_type == "message":
                if websocket not in authenticated_clients:
                    await send({"type": "error", "text": "Unauthorized – please log in first"})
                    continue

                text = data.get("text", "").strip()
                if not text:
                    continue

                info = authenticated_clients[websocket]
                username = info["username"]
                log.info(f"  💬 {username}: {text[:60]}")

                chat_msg = json.dumps({"type": "chat", "from": username, "text": text})
                # Echo back to sender + broadcast to others
                await asyncio.gather(
                    websocket.send(chat_msg),
                    broadcast(chat_msg, sender_ws=websocket),
                    return_exceptions=True,
                )

            # ── ADMIN: RELOAD EXCEL ──────────────────────────────────
            elif msg_type == "reload":
                if websocket not in authenticated_clients:
                    await send({"type": "error", "text": "Unauthorized"})
                    continue
                info = authenticated_clients[websocket]
                if info["role"] != "admin":
                    await send({"type": "error", "text": "Only admins can reload the user list"})
                    continue
                fresh = load_users(EXCEL_FILE)
                if fresh:
                    user_store = fresh
                    await send({"type": "system", "text": f"✅ Reloaded {len(user_store)} users from Excel"})
                else:
                    await send({"type": "error", "text": "Reload failed – check server logs"})

            else:
                await send({"type": "error", "text": f"Unknown message type: {msg_type}"})

    except websockets.exceptions.ConnectionClosedOK:
        pass
    except websockets.exceptions.ConnectionClosedError as e:
        log.debug(f"Connection error from {addr}: {e}")
    finally:
        if websocket in authenticated_clients:
            username = authenticated_clients[websocket]["username"]
            del authenticated_clients[websocket]
            log.info(f"Disconnected: {username} ({addr})")
            leave_msg = json.dumps({"type": "system", "text": f"🚪 {username} left the chat"})
            await asyncio.gather(*[ws.send(leave_msg) for ws in authenticated_clients], return_exceptions=True)
            await broadcast_user_list()
        else:
            log.info(f"Unauthenticated disconnect: {addr}")


async def main():
    global user_store
    user_store = load_users(EXCEL_FILE)
    if not user_store:
        log.warning("Starting with empty user store – no one can log in!")

    # Start background reload thread (optional)
    if RELOAD_INTERVAL > 0:
        t = threading.Thread(target=background_reload, args=(RELOAD_INTERVAL,), daemon=True)
        t.start()
        log.info(f"Auto-reload enabled every {RELOAD_INTERVAL}s")

    log.info(f"🚀 Server listening on ws://{HOST}:{PORT}")
    async with websockets.serve(handle_client, HOST, PORT):
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    asyncio.run(main())
