# LAN Chat — Authenticated WebSocket Chat
## Secure local-area-network messaging backed by an Excel user database

---

## 📂 File Layout

```
lan-chat/
├── server.py          ← Python WebSocket server (authentication + broadcast)
├── index.html         ← Frontend (login screen + chat UI)
├── users.xlsx         ← User database (username | password | role)
├── requirements.txt   ← Python dependencies
└── README.md
```

---

## 🚀 Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Edit users.xlsx (optional)

Open `users.xlsx` and add/remove rows. Columns required:

| username | password  | role      |
|----------|-----------|-----------|
| alice    | pass123   | admin     |
| bob      | hello456  | user      |
| charlie  | secret789 | user      |

> `role` is optional. If absent, defaults to `user`.  
> `admin` role gets a **↻ Reload Users** button in the UI.

### 3. Find your LAN IP

```bash
# Linux / Mac
ip addr show   # or: hostname -I
ifconfig

# Windows
ipconfig
```

Example: `10.0.25.115`

### 4. Edit index.html

Change **one line** at the top of the `<script>` block:

```js
const SERVER_URL = "ws://YOUR_LAN_IP:8765";
```

### 5. Start the server

```bash
python server.py
```

You'll see:
```
[INFO] Loaded 4 user(s) from users.xlsx
[INFO] Auto-reload enabled every 30s
[INFO] 🚀 Server listening on ws://0.0.0.0:8765
```

### 6. Open the frontend

On any device on the same LAN, open `index.html` in a browser.  
(No web server needed — plain `file://` works, or serve with `python -m http.server 8080`.)

---

## 🔐 Authentication Flow

```
Browser                          Server
   │                                │
   │── WebSocket connect ──────────>│
   │                                │
   │── { type:"login",              │
   │    username:"alice",           │
   │    password:"pass123" } ──────>│
   │                                │  Validates against users.xlsx
   │<── { type:"auth_ok",           │
   │      username:"alice",         │
   │      role:"admin" } ───────────│
   │                                │
   │  ← Chat messages allowed →     │
```

If login **fails**:
```
   │<── { type:"auth_fail",
   │      reason:"Invalid username or password" }
   │
   │  ← Connection closed. UI shows error.
```

If a client tries to send a message **without logging in**:
```
   │<── { type:"error", text:"Unauthorized – please log in first" }
```

---

## ⚙️ Configuration (top of server.py)

| Variable          | Default      | Description                            |
|-------------------|--------------|----------------------------------------|
| `HOST`            | `"0.0.0.0"`  | Listen on all network interfaces       |
| `PORT`            | `8765`       | WebSocket port                         |
| `EXCEL_FILE`      | `"users.xlsx"` | Path to the user database            |
| `RELOAD_INTERVAL` | `30`         | Seconds between auto-reloads (0 = off) |

---

## 💬 Message Protocol

All messages over WebSocket are **JSON**.

### Client → Server

| type      | fields                       | notes                        |
|-----------|------------------------------|------------------------------|
| `login`   | `username`, `password`       | Must be first message        |
| `message` | `text`                       | Requires prior auth          |
| `reload`  | *(none)*                     | Admin only — reloads Excel   |

### Server → Client

| type        | fields                         | notes                    |
|-------------|--------------------------------|--------------------------|
| `auth_ok`   | `username`, `role`             | Login success            |
| `auth_fail` | `reason`                       | Login failure            |
| `chat`      | `from`, `text`                 | Chat message broadcast   |
| `system`    | `text`                         | Join/leave/system events |
| `user_list` | `users` (array of usernames)   | Online users             |
| `error`     | `text`                         | Server-side error        |

---

## 🔄 Excel Sync Behaviour

- **On startup**: `users.xlsx` is loaded immediately.
- **Auto-reload**: Every `RELOAD_INTERVAL` seconds (background thread), the file is re-read. Changes take effect on the next login attempt.
- **Manual reload**: Any `admin` user can click **↻ Reload Users** in the sidebar to force an immediate reload server-side.

---

## 🏗 Architecture

```
┌─────────────────────────┐        ┌──────────────────────────────────┐
│   Browser (index.html)  │        │   server.py (Python asyncio)     │
│                         │        │                                  │
│  ┌─────────────────┐    │  JSON  │  ┌────────────────────────────┐  │
│  │  Login Screen   │◄──────────►│  │  handle_client() coroutine  │  │
│  └────────┬────────┘    │  over  │  └───────────┬────────────────┘  │
│           │ auth_ok      │  WS    │              │                   │
│  ┌────────▼────────┐    │        │  ┌───────────▼────────────────┐  │
│  │  Chat UI        │    │        │  │  authenticated_clients {}   │  │
│  │  - Message log  │    │        │  │  broadcast()               │  │
│  │  - User list    │    │        │  └───────────┬────────────────┘  │
│  │  - Input bar    │    │        │              │                   │
│  └─────────────────┘    │        │  ┌───────────▼────────────────┐  │
│                         │        │  │  user_store {}              │  │
│  sessionStorage:        │        │  │  (loaded from users.xlsx)  │  │
│   - lan_chat_user       │        │  │  background reload thread  │  │
│   - lan_chat_role       │        │  └────────────────────────────┘  │
└─────────────────────────┘        └──────────────────────────────────┘
```

**Key design choices:**

- **No HTTP** — authentication happens over the same WebSocket connection as chat. The first message *must* be a `login` frame.
- **No JWT** — session is tracked server-side in a dict (`authenticated_clients`). Simple and appropriate for a LAN app.
- **No database** — Excel is the single source of truth, loaded into an in-memory dict for O(1) lookups.
- **Async broadcast** — `asyncio.gather()` sends to all connected authenticated clients concurrently, so a slow client doesn't block others.

---

## 🔒 Security Notes

- Passwords are **plain text** — suitable for LAN-only use. For internet-facing deployment, hash passwords with `bcrypt` and use `wss://` (TLS).
- The server trusts only authenticated sockets for chat; unauthenticated sockets receive an error and cannot read messages.
- `sessionStorage` is used client-side (not `localStorage`) so sessions clear when the browser tab is closed.
