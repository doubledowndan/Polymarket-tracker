from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import sqlite3
import httpx
import asyncio
import json
from datetime import datetime, timezone
from typing import Optional
import os

app = FastAPI(title=“Polymarket Winning Wallet Tracker”)

app.add_middleware(
CORSMiddleware,
allow_origins=[”*”],
allow_methods=[”*”],
allow_headers=[”*”],
)

DB_PATH = “tracker.db”
POLYMARKET_API = “https://clob.polymarket.com”
GAMMA_API = “https://gamma-api.polymarket.com”

# ─── Database Setup ───────────────────────────────────────────────────────────

def get_db():
conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
return conn

def init_db():
conn = get_db()
conn.executescript(”””
CREATE TABLE IF NOT EXISTS wallets (
address TEXT PRIMARY KEY,
label TEXT,
added_at TEXT DEFAULT (datetime(‘now’)),
last_updated TEXT,
total_profit REAL DEFAULT 0,
win_rate REAL DEFAULT 0,
total_trades INTEGER DEFAULT 0,
wins INTEGER DEFAULT 0,
losses INTEGER DEFAULT 0,
active_positions INTEGER DEFAULT 0,
volume_traded REAL DEFAULT 0,
is_tracking INTEGER DEFAULT 1
);

```
    CREATE TABLE IF NOT EXISTS positions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        wallet_address TEXT,
        market_id TEXT,
        market_question TEXT,
        outcome TEXT,
        size REAL,
        avg_price REAL,
        current_price REAL,
        pnl REAL,
        status TEXT,
        created_at TEXT,
        resolved_at TEXT,
        FOREIGN KEY (wallet_address) REFERENCES wallets(address)
    );

    CREATE TABLE IF NOT EXISTS refresh_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        wallet_address TEXT,
        refreshed_at TEXT DEFAULT (datetime('now')),
        status TEXT
    );
""")
conn.commit()
conn.close()
```

init_db()

# ─── Polymarket API Helpers ────────────────────────────────────────────────────

async def fetch_wallet_trades(address: str) -> dict:
“”“Fetch trade history from Polymarket CLOB API”””
async with httpx.AsyncClient(timeout=30) as client:
try:
# Fetch trades from CLOB
resp = await client.get(
f”{POLYMARKET_API}/trades”,
params={“maker_address”: address, “limit”: 500}
)
trades_data = resp.json() if resp.status_code == 200 else {“data”: []}

```
        # Also fetch as taker
        resp2 = await client.get(
            f"{POLYMARKET_API}/trades",
            params={"taker_address": address, "limit": 500}
        )
        trades_data2 = resp2.json() if resp2.status_code == 200 else {"data": []}

        all_trades = (trades_data.get("data") or []) + (trades_data2.get("data") or [])

        # Fetch positions from Gamma API
        positions_resp = await client.get(
            f"{GAMMA_API}/positions",
            params={"user": address, "sizeThreshold": "0.01"}
        )
        positions_data = positions_resp.json() if positions_resp.status_code == 200 else []

        return {
            "trades": all_trades,
            "positions": positions_data if isinstance(positions_data, list) else []
        }
    except Exception as e:
        print(f"Error fetching data for {address}: {e}")
        return {"trades": [], "positions": []}
```

def compute_stats(trades: list, positions: list) -> dict:
“”“Compute PnL and win rate from raw trade data”””
total_profit = 0.0
wins = 0
losses = 0
volume = 0.0
active = 0
position_records = []

```
# Process positions from Gamma API
for pos in positions:
    try:
        size = float(pos.get("size", 0) or 0)
        avg_price = float(pos.get("avgPrice", 0) or 0)
        cur_price = float(pos.get("curPrice", pos.get("currentPrice", avg_price)) or avg_price)
        realized_pnl = float(pos.get("realizedPnl", 0) or 0)
        unrealized_pnl = (cur_price - avg_price) * size
        total_pnl = realized_pnl + unrealized_pnl
        status = pos.get("outcome", "ACTIVE")
        redeemable = pos.get("redeemable", False)

        if redeemable or status in ("YES", "NO"):
            if total_pnl > 0:
                wins += 1
            elif total_pnl < 0:
                losses += 1
        else:
            active += 1

        total_profit += total_pnl
        volume += size * avg_price

        position_records.append({
            "market_id": pos.get("conditionId", pos.get("marketId", "")),
            "market_question": pos.get("title", pos.get("question", "Unknown Market")),
            "outcome": pos.get("outcomeIndex", ""),
            "size": size,
            "avg_price": avg_price,
            "current_price": cur_price,
            "pnl": total_pnl,
            "status": "RESOLVED" if redeemable or status in ("YES", "NO") else "ACTIVE",
            "created_at": pos.get("startDate", datetime.now(timezone.utc).isoformat()),
            "resolved_at": pos.get("endDate", ""),
        })
    except Exception:
        continue

total_trades = wins + losses
win_rate = (wins / total_trades * 100) if total_trades > 0 else 0.0

return {
    "total_profit": round(total_profit, 4),
    "wins": wins,
    "losses": losses,
    "total_trades": total_trades,
    "win_rate": round(win_rate, 2),
    "active_positions": active,
    "volume_traded": round(volume, 4),
    "positions": position_records,
}
```

# ─── Routes ───────────────────────────────────────────────────────────────────

@app.get(”/”)
async def serve_frontend():
return FileResponse(“index.html”)

@app.get(”/api/wallets”)
async def list_wallets():
conn = get_db()
rows = conn.execute(“SELECT * FROM wallets ORDER BY total_profit DESC”).fetchall()
conn.close()
return [dict(r) for r in rows]

@app.post(”/api/wallets”)
async def add_wallet(payload: dict, background_tasks: BackgroundTasks):
address = payload.get(“address”, “”).strip().lower()
label = payload.get(“label”, “”).strip() or f”Wallet {address[:6]}…{address[-4:]}”

```
if not address or len(address) != 42 or not address.startswith("0x"):
    raise HTTPException(status_code=400, detail="Invalid Ethereum address")

conn = get_db()
existing = conn.execute("SELECT address FROM wallets WHERE address = ?", (address,)).fetchone()
if existing:
    conn.close()
    raise HTTPException(status_code=409, detail="Wallet already tracked")

conn.execute(
    "INSERT INTO wallets (address, label, last_updated) VALUES (?, ?, ?)",
    (address, label, datetime.now(timezone.utc).isoformat())
)
conn.commit()
conn.close()

background_tasks.add_task(refresh_wallet_data, address)
return {"status": "added", "address": address, "label": label}
```

@app.delete(”/api/wallets/{address}”)
async def remove_wallet(address: str):
conn = get_db()
conn.execute(“DELETE FROM wallets WHERE address = ?”, (address.lower(),))
conn.execute(“DELETE FROM positions WHERE wallet_address = ?”, (address.lower(),))
conn.commit()
conn.close()
return {“status”: “removed”}

@app.post(”/api/wallets/{address}/refresh”)
async def refresh_wallet(address: str, background_tasks: BackgroundTasks):
address = address.lower()
conn = get_db()
existing = conn.execute(“SELECT address FROM wallets WHERE address = ?”, (address,)).fetchone()
conn.close()
if not existing:
raise HTTPException(status_code=404, detail=“Wallet not found”)
background_tasks.add_task(refresh_wallet_data, address)
return {“status”: “refresh_queued”}

@app.get(”/api/wallets/{address}/positions”)
async def get_positions(address: str):
conn = get_db()
rows = conn.execute(
“SELECT * FROM positions WHERE wallet_address = ? ORDER BY pnl DESC”,
(address.lower(),)
).fetchall()
conn.close()
return [dict(r) for r in rows]

@app.get(”/api/leaderboard”)
async def get_leaderboard():
conn = get_db()
rows = conn.execute(”””
SELECT address, label, total_profit, win_rate, total_trades, wins, losses,
active_positions, volume_traded, last_updated
FROM wallets
WHERE is_tracking = 1
ORDER BY total_profit DESC
LIMIT 50
“””).fetchall()
conn.close()
return [dict(r) for r in rows]

@app.get(”/api/stats/summary”)
async def summary_stats():
conn = get_db()
row = conn.execute(”””
SELECT
COUNT(*) as total_wallets,
SUM(total_profit) as combined_profit,
AVG(win_rate) as avg_win_rate,
SUM(volume_traded) as total_volume,
MAX(total_profit) as top_profit
FROM wallets WHERE is_tracking = 1
“””).fetchone()
conn.close()
return dict(row) if row else {}

# ─── Background Refresh ───────────────────────────────────────────────────────

async def refresh_wallet_data(address: str):
try:
data = await fetch_wallet_trades(address)
stats = compute_stats(data[“trades”], data[“positions”])

```
    conn = get_db()
    conn.execute("""
        UPDATE wallets SET
            total_profit = ?,
            win_rate = ?,
            total_trades = ?,
            wins = ?,
            losses = ?,
            active_positions = ?,
            volume_traded = ?,
            last_updated = ?
        WHERE address = ?
    """, (
        stats["total_profit"],
        stats["win_rate"],
        stats["total_trades"],
        stats["wins"],
        stats["losses"],
        stats["active_positions"],
        stats["volume_traded"],
        datetime.now(timezone.utc).isoformat(),
        address
    ))

    # Clear old positions and insert new
    conn.execute("DELETE FROM positions WHERE wallet_address = ?", (address,))
    for pos in stats["positions"]:
        conn.execute("""
            INSERT INTO positions 
            (wallet_address, market_id, market_question, outcome, size, avg_price, current_price, pnl, status, created_at, resolved_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            address, pos["market_id"], pos["market_question"], str(pos["outcome"]),
            pos["size"], pos["avg_price"], pos["current_price"], pos["pnl"],
            pos["status"], pos["created_at"], pos.get("resolved_at", "")
        ))

    conn.execute(
        "INSERT INTO refresh_log (wallet_address, status) VALUES (?, 'success')", (address,)
    )
    conn.commit()
    conn.close()
except Exception as e:
    print(f"Refresh failed for {address}: {e}")
    try:
        conn = get_db()
        conn.execute(
            "INSERT INTO refresh_log (wallet_address, status) VALUES (?, 'error')", (address,)
        )
        conn.commit()
        conn.close()
    except:
        pass
```

if **name** == “**main**”:
import uvicorn
uvicorn.run(app, host=“0.0.0.0”, port=8000, reload=True)