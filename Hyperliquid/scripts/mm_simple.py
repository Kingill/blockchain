#!/usr/bin/env python3
"""
Simple Market Maker (Étape 1)
- Quote a single bid and ask around mid-price with a fixed spread.
- Use post-only preference (if exchange supports it).
- Minimal hold time between cancels/requotes to reduce churn.
- Blocking exchange calls are run in a threadpool via asyncio.to_thread.
- Very small, readable, easy to extend.
"""
import asyncio
import json
import logging
import signal
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List, Set
from collections import deque

import websockets
from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants
from rich.console import Console
from rich.table import Table

# ========================
# --- CONFIGURATION ---
# ========================
URI = "wss://api.hyperliquid-testnet.xyz/ws"
COIN = "BTC"

# Strategy parameters
#BASE_SIZE = 0.0003            # order size in coin
BASE_SIZE = 0.001            # order size in coin
SPREAD_PCT = 0.001            # 0.001 -> 0.1% total (each side uses +/- spread_pct)
MIN_ORDER_NOTIONAL = 5.0      # minimal notional in USDC to place an order
POST_ONLY = True              # try to post-only (exchange dependent)
MIN_HOLD_SECONDS = 3.0        # minimum seconds to keep orders before cancelling/replacing
DRY_RUN = False               # if True, do not send orders to exchange

# Market / technical
TICK_SIZE = 1.0
INTERVAL = 30                 # websocket receive timeout (s)
RECONCILE_INTERVAL = 60       # how often to reconcile position from API
HEARTBEAT_INTERVAL = 20

# Rate limiting
MAX_REQUESTS_PER_SECOND = 10
RATE_LIMIT_WINDOW = 1.0

# Misc
CONFIG_PATH = Path("config.json")
MAX_RECONNECT_ATTEMPTS = 10

# ========================
# --- LOGGING ---
# ========================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("mm_simple")
console = Console()

# ========================
# --- HELPERS & DATACLASSES
# ========================
def load_config(path=CONFIG_PATH):
    with open(path) as f:
        cfg = json.load(f)
    cfg.setdefault("network", "testnet")
    return cfg

def round_to_tick(price: float, tick_size: float) -> float:
    """Round price to nearest tick."""
    return round(price / tick_size) * tick_size

@dataclass
class Metrics:
    quotes_sent: int = 0
    cancels_sent: int = 0
    fills_received: int = 0
    errors: int = 0
    last_placed_time: float = 0.0
    reconnections: int = 0

    def log(self):
        logger.info(f"Metrics: quotes={self.quotes_sent}, cancels={self.cancels_sent}, fills={self.fills_received}, errors={self.errors}, reconnects={self.reconnections}")

# ========================
# --- RATE LIMITER ---
# ========================
class RateLimiter:
    def __init__(self, max_requests: int, window: float):
        self.max_requests = max_requests
        self.window = window
        self.requests = deque()

    async def acquire(self):
        now = time.time()
        while self.requests and self.requests[0] < now - self.window:
            self.requests.popleft()
        if len(self.requests) >= self.max_requests:
            sleep_time = self.requests[0] + self.window - now + 0.005
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)
        self.requests.append(time.time())

# ========================
# --- POSITION TRACKER ---
# ========================
class PositionTracker:
    def __init__(self, exchange: Exchange, coin: str):
        self.exchange = exchange
        self.coin = coin
        self.position = 0.0
        self._lock = asyncio.Lock()

    async def update_from_api(self):
        """Fetch position from REST (run sync call in thread)."""
        from hyperliquid.info import Info
        api_url = constants.TESTNET_API_URL if "testnet" in self.exchange.base_url else constants.MAINNET_API_URL
        info = Info(api_url, skip_ws=True)
        try:
            user_state = await asyncio.to_thread(info.user_state, self.exchange.wallet.address)
        except Exception as e:
            logger.error(f"Position fetch failed: {e}")
            return self.position

        async with self._lock:
            new_pos = 0.0
            if user_state and "assetPositions" in user_state:
                for asset in user_state["assetPositions"]:
                    pos_data = asset.get("position", {})
                    if pos_data.get("coin") == self.coin:
                        try:
                            new_pos = float(pos_data.get("szi", 0))
                        except (ValueError, TypeError):
                            new_pos = 0.0
                        break
            self.position = new_pos
            return self.position

    async def update_from_fill(self, fill: dict):
        """Update local position from a fill event."""
        if not isinstance(fill, dict):
            return
        if fill.get("coin") != self.coin:
            return
        try:
            sz = float(fill.get("sz", 0))
            side = fill.get("side")
        except (ValueError, TypeError):
            return
        async with self._lock:
            if side == "B":
                self.position += sz
            elif side == "A":
                self.position -= sz

    async def get_position(self) -> float:
        async with self._lock:
            return self.position

# ========================
# --- ORDER MANAGER ---
# ========================
class OrderManager:
    def __init__(self, exchange: Exchange, coin: str, rate_limiter: RateLimiter):
        self.exchange = exchange
        self.coin = coin
        self.rate_limiter = rate_limiter
        self.open_oids: Set[int] = set()
        self._lock = asyncio.Lock()

    async def cancel_all(self) -> int:
        """Cancel all known open orders (runs blocking call in thread)."""
        async with self._lock:
            oids = list(self.open_oids)
        if not oids:
            return 0

        cancelled = 0
        failed = []
        for oid in oids:
            try:
                await self.rate_limiter.acquire()
                resp = await asyncio.to_thread(self.exchange.cancel, self.coin, oid)
                if resp and resp.get("status") == "ok":
                    async with self._lock:
                        self.open_oids.discard(oid)
                    cancelled += 1
                else:
                    failed.append(oid)
            except Exception as e:
                logger.error(f"Cancel error oid={oid}: {e}")
                failed.append(oid)
        if failed:
            logger.warning(f"Failed cancels: {failed}")
        return cancelled

    async def place_orders(self, orders: List[dict]) -> List[int]:
        """Place orders via bulk_orders (blocking -> thread). Returns placed OIDs."""
        if not orders:
            return []
        try:
            await self.rate_limiter.acquire()
            resp = await asyncio.to_thread(self.exchange.bulk_orders, orders)
            if not resp or resp.get("status") != "ok":
                logger.error(f"bulk_orders failed: {resp}")
                return []
            oids = []
            async with self._lock:
                for st in resp.get("response", {}).get("data", {}).get("statuses", []):
                    if "resting" in st:
                        oid = st["resting"]["oid"]
                        oids.append(oid)
                        self.open_oids.add(oid)
                    elif "error" in st:
                        logger.warning(f"order error: {st['error']}")
            return oids
        except Exception as e:
            logger.error(f"place_orders exception: {e}")
            return []

    async def get_open_count(self) -> int:
        async with self._lock:
            return len(self.open_oids)

# ========================
# --- MARKET MAKER BOT ---
# ========================
class SimpleMarketMaker:
    def __init__(self):
        self.stop_event = asyncio.Event()
        self.position_tracker: Optional[PositionTracker] = None
        self.order_manager: Optional[OrderManager] = None
        self.metrics = Metrics()
        self.rate_limiter = RateLimiter(MAX_REQUESTS_PER_SECOND, RATE_LIMIT_WINDOW)
        self.last_mid = None
        self.last_quote_time = 0.0

    def setup_signal_handlers(self):
        signal.signal(signal.SIGINT, lambda s, f: self.stop_event.set())
        signal.signal(signal.SIGTERM, lambda s, f: self.stop_event.set())

    def render_table(self, mid, pos, bids_on=True, asks_on=True):
        table = Table(title="Simple MM - BTC")
        table.add_column("Mid")
        table.add_column("Pos")
        table.add_column("Bids")
        table.add_column("Asks")
        table.add_row(f"{mid:.2f}", f"{pos:.6f}", "[green]ON[/green]" if bids_on else "[red]OFF[/red]", "[green]ON[/green]" if asks_on else "[red]OFF[/red]")
        return table

    async def handle_l2book(self, data: dict):
        """Process L2 snapshot/update - compute mid and maybe quote."""
        levels = data.get("levels", [[], []])
        if len(levels) < 2:
            return
        bids, asks = levels[0], levels[1]
        if not bids or not asks:
            return
        try:
            best_bid = float(bids[0]['px'])
            best_ask = float(asks[0]['px'])
        except Exception:
            return
        mid = (best_bid + best_ask) / 2.0

        # decide whether to (re)quote
        now = time.time()
        if self.last_mid is not None:
            rel_move = abs(mid - self.last_mid) / max(self.last_mid, 1.0)
            # require some move OR enough time since last quote
            if rel_move < 0.0002 and (now - self.last_quote_time) < MIN_HOLD_SECONDS:
                # too small change and we are still in hold period
                return

        # Build simple bid/ask orders
        buy_px = round_to_tick(mid * (1.0 - SPREAD_PCT), TICK_SIZE)
        sell_px = round_to_tick(mid * (1.0 + SPREAD_PCT), TICK_SIZE)

        # check notional
        notional = BASE_SIZE * mid
        if notional < MIN_ORDER_NOTIONAL:
            logger.debug(f"Notional {notional:.2f} < min {MIN_ORDER_NOTIONAL:.2f}, skipping")
            return

        orders = []
        buy_order = {
            "coin": COIN,
            "is_buy": True,
            "limit_px": buy_px,
            "sz": BASE_SIZE,
            "reduce_only": False,
            "order_type": {"limit": {"tif": "Gtc"}}
        }
        sell_order = {
            "coin": COIN,
            "is_buy": False,
            "limit_px": sell_px,
            "sz": BASE_SIZE,
            "reduce_only": False,
            "order_type": {"limit": {"tif": "Gtc"}}
        }
        if POST_ONLY:
            buy_order["post_only"] = True
            sell_order["post_only"] = True

        orders.append(buy_order)
        orders.append(sell_order)

        # Place: cancel old orders only if MIN_HOLD_SECONDS elapsed since last quote
        if (time.time() - self.last_quote_time) < MIN_HOLD_SECONDS and self.last_quote_time > 0:
            logger.debug("Holding existing orders (min hold not elapsed)")
            return

        if DRY_RUN:
            logger.info(f"[DRY RUN] Would cancel & place orders: buy {buy_px} sell {sell_px} size {BASE_SIZE}")
            self.last_quote_time = time.time()
            self.last_mid = mid
            return

        # Cancel all and place new
        cancelled_count = await self.order_manager.cancel_all()
        self.metrics.cancels_sent += cancelled_count

        placed_oids = await self.order_manager.place_orders(orders)
        if placed_oids:
            self.metrics.quotes_sent += len(placed_oids)
            self.last_quote_time = time.time()
            self.last_mid = mid

        # UI
        pos = await self.position_tracker.get_position()
        console.clear()
        console.print(self.render_table(mid, pos))

    async def handle_user_fills(self, fill: dict):
        """Update local pos on fills."""
        if not isinstance(fill, dict):
            return
        # basic validation
        if 'side' not in fill or 'sz' not in fill:
            return
        await self.position_tracker.update_from_fill(fill)
        self.metrics.fills_received += 1
        logger.info(f"Fill: side={fill.get('side')} sz={fill.get('sz')} px={fill.get('px', 'N/A')}")

    async def reconcile_loop(self):
        while not self.stop_event.is_set():
            try:
                await asyncio.sleep(RECONCILE_INTERVAL)
                await self.position_tracker.update_from_api()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Recon error: {e}")

    async def heartbeat_loop(self, ws):
        while not self.stop_event.is_set():
            try:
                await asyncio.sleep(HEARTBEAT_INTERVAL)
                await ws.ping()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Heartbeat error: {e}")
                break

    async def run(self):
        self.setup_signal_handlers()
        cfg = load_config()
        wallet = Account.from_key(cfg["secret_key"])
        API_URL = constants.TESTNET_API_URL if cfg["network"].lower() == "testnet" else constants.MAINNET_API_URL
        # exchange creation may be fast; if it's blocking, consider asyncio.to_thread
        exchange = Exchange(wallet, base_url=API_URL)

        self.position_tracker = PositionTracker(exchange, COIN)
        self.order_manager = OrderManager(exchange, COIN, self.rate_limiter)

        await self.position_tracker.update_from_api()
        logger.info(f"Initial position: {await self.position_tracker.get_position():.6f}")

        reconcile_task = asyncio.create_task(self.reconcile_loop())

        retry_count = 0
        reconnect_delay = 1

        while not self.stop_event.is_set() and retry_count < MAX_RECONNECT_ATTEMPTS:
            try:
                async with websockets.connect(URI, ping_interval=20, ping_timeout=60, close_timeout=10) as ws:
                    logger.info("WS connected")
                    retry_count = 0
                    reconnect_delay = 1
                    self.metrics.reconnections += 1

                    # Subscribe
                    await ws.send(json.dumps({
                        "method": "subscribe",
                        "subscription": {"type": "l2Book", "coin": COIN}
                    }))
                    await ws.send(json.dumps({
                        "method": "subscribe",
                        "subscription": {"type": "userFills", "user": wallet.address}
                    }))

                    heartbeat_task = asyncio.create_task(self.heartbeat_loop(ws))

                    while not self.stop_event.is_set():
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=INTERVAL)
                            try:
                                data = json.loads(msg)
                            except json.JSONDecodeError:
                                continue
                            if not isinstance(data, dict):
                                continue
                            channel = data.get("channel")
                            if channel == "l2Book" and data.get("data"):
                                await self.handle_l2book(data["data"])
                            elif channel == "userFills" and data.get("data"):
                                fills = data["data"]
                                if isinstance(fills, dict):
                                    fills = fills.get("fills", [])
                                if isinstance(fills, list):
                                    for f in fills:
                                        await self.handle_user_fills(f)
                        except asyncio.TimeoutError:
                            continue
                        except websockets.exceptions.ConnectionClosedError:
                            logger.warning("WS closed during recv")
                            break

                    heartbeat_task.cancel()
                    await asyncio.gather(heartbeat_task, return_exceptions=True)

            except Exception as e:
                logger.error(f"WS error: {e}")
                # try to cancel open orders to be safe
                try:
                    await self.order_manager.cancel_all()
                except Exception as ee:
                    logger.error(f"cancel_all failed after ws error: {ee}")
                retry_count += 1
                self.metrics.reconnections += 1
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 30)

        reconcile_task.cancel()
        await asyncio.gather(reconcile_task, return_exceptions=True)

        # shutdown cleanup
        try:
            cancelled = await self.order_manager.cancel_all()
            self.metrics.cancels_sent += cancelled
        except Exception as e:
            logger.error(f"final cancel failed: {e}")

        self.metrics.log()
        logger.info("Bot stopped cleanly.")

# ========================
# --- ENTRYPOINT ---
# ========================
if __name__ == "__main__":
    bot = SimpleMarketMaker()
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        logger.info("Interrupted by user.")
