#!/usr/bin/env python3
"""
Enhanced Market Maker with:
- Dynamic spread based on volatility
- Position-based skew (asymmetric bid/ask)
- Position limits (MAX_POSITION)
- PnL tracking: save fills with execution price vs mid
- Healthcheck: alert if fills too rare
- CSV export for post-trade analysis (real-time append)
- Configurable TICK_SIZE per coin
- Skip snapshot fills (ignore historical data)
"""
import asyncio
import csv
import json
import logging
import signal
import time
from collections import deque
from dataclasses import dataclass
from math import sqrt
from pathlib import Path
from typing import Deque, List, Optional, Set, Tuple

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

# Coin-specific settings
COIN_CONFIG = {
    "BTC": {"tick_size": 1.0, "decimals": 4},
    "ETH": {"tick_size": 0.1, "decimals": 5},
}

# Strategy parameters
BASE_SIZE = 0.0001
BASE_SPREAD_PCT = 0.001
MIN_SPREAD_PCT = 0.0005
MAX_SPREAD_PCT = 0.02
VOL_WINDOW_SECONDS = 30
VOL_SCALE = 2.0
MIN_ORDER_NOTIONAL = 5.0
POST_ONLY = True
MIN_HOLD_SECONDS = 3.0
DRY_RUN = False

# Position / risk
MAX_POSITION = 0.01
POSITION_SKEW_FACTOR = 1.5
STOP_TRADING_THRESHOLD = 0.008

# Health / monitoring
FILL_ALERT_THRESHOLD = 120
METRICS_LOG_INTERVAL = 300

# Market / misc
INTERVAL = 30
RECONCILE_INTERVAL = 60
HEARTBEAT_INTERVAL = 20
MAX_REQUESTS_PER_SECOND = 10
RATE_LIMIT_WINDOW = 1.0
CONFIG_PATH = Path("config.json")
MAX_RECONNECT_ATTEMPTS = 10

# Output files
FILLS_CSV_PATH = Path("fills.csv")
METRICS_JSON_PATH = Path("metrics.json")

# ========================
# --- LOGGING ---
# ========================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("mm_enhanced")
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
    return round(price / tick_size) * tick_size

@dataclass
class FillRecord:
    """Track individual fills for PnL analysis."""
    timestamp: float
    side: str
    size: float
    execution_price: float
    mid_price: float
    position_after: float
    pnl_incremental: float

    def to_dict(self):
        return {
            "timestamp": self.timestamp,
            "datetime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.timestamp)),
            "side": self.side,
            "size": f"{self.size:.6f}",
            "exec_price": f"{self.execution_price:.2f}",
            "mid_price": f"{self.mid_price:.2f}",
            "position_after": f"{self.position_after:.6f}",
            "pnl_incremental": f"{self.pnl_incremental:.4f}",
        }

@dataclass
class Metrics:
    quotes_sent: int = 0
    cancels_sent: int = 0
    fills_received: int = 0
    errors: int = 0
    reconnections: int = 0
    last_fill_time: float = 0.0
    total_pnl: float = 0.0

    def log(self):
        logger.info(
            f"Metrics: quotes={self.quotes_sent}, cancels={self.cancels_sent}, "
            f"fills={self.fills_received}, reconnects={self.reconnections}, "
            f"errors={self.errors}, total_pnl={self.total_pnl:.4f}"
        )

    def save_json(self, path=METRICS_JSON_PATH):
        data = {
            "timestamp": time.time(),
            "quotes_sent": self.quotes_sent,
            "cancels_sent": self.cancels_sent,
            "fills_received": self.fills_received,
            "reconnections": self.reconnections,
            "errors": self.errors,
            "total_pnl": self.total_pnl,
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

# ========================
# --- RATE LIMITER ---
# ========================
class RateLimiter:
    def __init__(self, max_requests: int, window: float):
        self.max_requests = max_requests
        self.window = window
        self.requests: Deque[float] = deque()

    async def acquire(self):
        now = time.time()
        while self.requests and self.requests[0] < now - self.window:
            self.requests.popleft()
        if len(self.requests) >= self.max_requests:
            sleep_time = self.requests[0] + self.window - now + 0.005
            if sleep_time > 0:
                logger.debug(f"Rate limiter sleeping {sleep_time:.3f}s")
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
# --- ENHANCED MARKET MAKER ---
# ========================
class EnhancedMarketMaker:
    def __init__(self):
        self.stop_event = asyncio.Event()
        self.position_tracker: Optional[PositionTracker] = None
        self.order_manager: Optional[OrderManager] = None
        self.metrics = Metrics()
        self.rate_limiter = RateLimiter(MAX_REQUESTS_PER_SECOND, RATE_LIMIT_WINDOW)

        self.price_buffer: Deque[Tuple[float, float]] = deque()
        self.last_mid: Optional[float] = None
        self.last_quote_time = 0.0
        self.mid_lock = asyncio.Lock()

        self.fill_records: List[FillRecord] = []
        self._fill_lock = asyncio.Lock()

        coin_cfg = COIN_CONFIG.get(COIN, {"tick_size": 1.0})
        self.tick_size = coin_cfg["tick_size"]

    def setup_signal_handlers(self):
        signal.signal(signal.SIGINT, lambda s, f: self.stop_event.set())
        signal.signal(signal.SIGTERM, lambda s, f: self.stop_event.set())

    def render_table(self, mid: float, pos: float, spread_pct: float, last_fill_ago: float):
        t = Table(title=f"MM Enhanced - {COIN}")
        t.add_column("Mid")
        t.add_column("Pos")
        t.add_column("Spread%")
        t.add_column("PnL")
        t.add_column("LastFill")
        t.add_row(
            f"{mid:.2f}",
            f"{pos:.6f}",
            f"{spread_pct:.4f}",
            f"{self.metrics.total_pnl:.4f}",
            f"{last_fill_ago:.0f}s ago" if last_fill_ago < 999 else "N/A"
        )
        return t

    def update_price_buffer(self, mid: float):
        now = time.time()
        self.price_buffer.append((now, mid))
        cutoff = now - VOL_WINDOW_SECONDS
        while self.price_buffer and self.price_buffer[0][0] < cutoff:
            self.price_buffer.popleft()

    async def get_latest_mid(self) -> Optional[float]:
        """Get most recent mid-price from buffer."""
        async with self.mid_lock:
            if self.price_buffer:
                return self.price_buffer[-1][1]
            return None

    def compute_volatility(self) -> float:
        n = len(self.price_buffer)
        if n < 3:
            return 0.0
        prices = [p for _, p in self.price_buffer]
        mean = sum(prices) / n
        var = sum((p - mean) ** 2 for p in prices) / n
        sigma = sqrt(var)
        return sigma / mean if mean > 0 else 0.0

    def dynamic_spread_pct(self, base: float) -> float:
        rel_vol = self.compute_volatility()
        extra = VOL_SCALE * rel_vol
        spread = base + extra
        spread = max(MIN_SPREAD_PCT, min(spread, MAX_SPREAD_PCT))
        return spread

    def compute_skewed_spreads(self, mid: float, spread_pct: float, pos: float) -> Tuple[float, float]:
        if pos == 0:
            return spread_pct, spread_pct
        pos_ratio = pos / MAX_POSITION if MAX_POSITION > 0 else 0
        pos_ratio = max(-1.0, min(1.0, pos_ratio))
        bid_spread = spread_pct * (1.0 + POSITION_SKEW_FACTOR * max(0, -pos_ratio))
        ask_spread = spread_pct * (1.0 + POSITION_SKEW_FACTOR * max(0, pos_ratio))
        return bid_spread, ask_spread

    async def handle_l2book(self, data: dict):
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

        self.update_price_buffer(mid)
        spread_pct = self.dynamic_spread_pct(BASE_SPREAD_PCT)

        now = time.time()
        if self.last_mid is not None:
            rel_move = abs(mid - self.last_mid) / max(self.last_mid, 1.0)
            if rel_move < 0.0002 and (now - self.last_quote_time) < MIN_HOLD_SECONDS:
                return

        pos = await self.position_tracker.get_position()
        
        # Kill-switch: stop trading if position is too large
        abs_pos = abs(pos)
        if abs_pos > STOP_TRADING_THRESHOLD:
            logger.warning(
                f"Position {pos:.6f} exceeds stop threshold {STOP_TRADING_THRESHOLD}. "
                f"Halting new orders (only reduce-only allowed)."
            )
            return

        bid_spread, ask_spread = self.compute_skewed_spreads(mid, spread_pct, pos)

        buy_px = round_to_tick(mid * (1.0 - bid_spread), self.tick_size)
        sell_px = round_to_tick(mid * (1.0 + ask_spread), self.tick_size)
        notional = BASE_SIZE * mid
        if notional < MIN_ORDER_NOTIONAL:
            logger.debug(f"Notional {notional:.2f} < min {MIN_ORDER_NOTIONAL:.2f}")
            return

        can_place_buy = (pos + BASE_SIZE) <= MAX_POSITION
        can_place_sell = (pos - BASE_SIZE) >= -MAX_POSITION

        orders = []
        if can_place_buy:
            buy_order = {
                "coin": COIN,
                "is_buy": True,
                "limit_px": buy_px,
                "sz": BASE_SIZE,
                "reduce_only": False,
                "order_type": {"limit": {"tif": "Gtc"}},
            }
            if POST_ONLY:
                buy_order["post_only"] = True
            orders.append(buy_order)
        else:
            logger.debug(f"Skipping buy: pos {pos:.6f} + {BASE_SIZE} > MAX {MAX_POSITION}")

        if can_place_sell:
            sell_order = {
                "coin": COIN,
                "is_buy": False,
                "limit_px": sell_px,
                "sz": BASE_SIZE,
                "reduce_only": False,
                "order_type": {"limit": {"tif": "Gtc"}},
            }
            if POST_ONLY:
                sell_order["post_only"] = True
            orders.append(sell_order)
        else:
            logger.debug(f"Skipping sell: pos {pos:.6f} - {BASE_SIZE} < -MAX {-MAX_POSITION}")

        if not orders:
            return

        if (now - self.last_quote_time) < MIN_HOLD_SECONDS and self.last_quote_time > 0:
            logger.debug("Min hold time not elapsed")
            return

        if DRY_RUN:
            logger.info(
                f"[DRY RUN] buy {buy_px} (spread {bid_spread:.6f}) / "
                f"sell {sell_px} (spread {ask_spread:.6f}) size {BASE_SIZE}"
            )
            self.last_quote_time = now
            self.last_mid = mid
            return

        cancelled = await self.order_manager.cancel_all()
        self.metrics.cancels_sent += cancelled

        placed = await self.order_manager.place_orders(orders)
        if placed:
            self.metrics.quotes_sent += len(placed)
            self.last_quote_time = now
            self.last_mid = mid

        pos_now = await self.position_tracker.get_position()
        last_fill_ago = now - self.metrics.last_fill_time if self.metrics.last_fill_time > 0 else 999
        console.clear()
        console.print(self.render_table(mid, pos_now, spread_pct, last_fill_ago))

    async def append_fill_csv(self, rec: FillRecord):
        """Append a single fill to CSV immediately (real-time)."""
        try:
            file_exists = FILLS_CSV_PATH.exists()
            with open(FILLS_CSV_PATH, "a", newline="") as f:
                fieldnames = [
                    "timestamp", "datetime", "side", "size", "exec_price", 
                    "mid_price", "position_after", "pnl_incremental"
                ]
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                if not file_exists:
                    writer.writeheader()
                writer.writerow(rec.to_dict())
        except Exception as e:
            logger.error(f"Failed to append fill CSV: {e}")

    async def handle_user_fills(self, fill: dict):
        if not isinstance(fill, dict):
            return
        
        # SKIP snapshot fills (historical data from past)
        if fill.get("isSnapshot"):
            logger.debug("Skipping snapshot fill (historical data)")
            return
        
        if 'side' not in fill or 'sz' not in fill or 'px' not in fill:
            return
        if fill.get("coin") != COIN:
            return

        try:
            side = fill.get("side")
            size = float(fill.get("sz", 0))
            exec_price = float(fill.get("px", 0))
        except (ValueError, TypeError):
            return

        await self.position_tracker.update_from_fill(fill)
        pos_after = await self.position_tracker.get_position()

        mid = await self.get_latest_mid()
        if mid is None:
            mid = exec_price

        if side == "B":
            pnl_inc = (mid - exec_price) * size
        else:
            pnl_inc = (exec_price - mid) * size

        now = time.time()
        rec = FillRecord(
            timestamp=now,
            side=side,
            size=size,
            execution_price=exec_price,
            mid_price=mid,
            position_after=pos_after,
            pnl_incremental=pnl_inc,
        )

        async with self._fill_lock:
            self.fill_records.append(rec)

        await self.append_fill_csv(rec)

        self.metrics.fills_received += 1
        self.metrics.last_fill_time = now
        self.metrics.total_pnl += pnl_inc

        logger.info(
            f"Fill: side={side} sz={size:.6f} px={exec_price:.2f} mid={mid:.2f} "
            f"pnl_inc={pnl_inc:.4f} pos_after={pos_after:.6f}"
        )

    async def healthcheck_loop(self):
        while not self.stop_event.is_set():
            try:
                await asyncio.sleep(FILL_ALERT_THRESHOLD)
                now = time.time()
                if self.metrics.fills_received > 0:
                    last_fill_ago = now - self.metrics.last_fill_time
                    if last_fill_ago > FILL_ALERT_THRESHOLD:
                        logger.warning(
                            f"No fills for {last_fill_ago:.0f}s (threshold: {FILL_ALERT_THRESHOLD}s). "
                            f"Check spreads or market depth."
                        )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Healthcheck error: {e}")

    async def reconcile_loop(self):
        while not self.stop_event.is_set():
            try:
                await asyncio.sleep(RECONCILE_INTERVAL)
                await self.position_tracker.update_from_api()
                logger.debug(f"Reconciled position: {await self.position_tracker.get_position():.6f}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Reconcile error: {e}")

    async def metrics_loop(self):
        while not self.stop_event.is_set():
            try:
                await asyncio.sleep(METRICS_LOG_INTERVAL)
                self.metrics.log()
                self.metrics.save_json()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Metrics loop error: {e}")

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

    def save_fills_csv(self, path=FILLS_CSV_PATH):
        if not self.fill_records:
            logger.info("No fills to export")
            return
        try:
            with open(path, "w", newline="") as f:
                fieldnames = [
                    "timestamp", "datetime", "side", "size", "exec_price", 
                    "mid_price", "position_after", "pnl_incremental"
                ]
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                for rec in self.fill_records:
                    writer.writerow(rec.to_dict())
            logger.info(f"Saved {len(self.fill_records)} fills to {path}")
        except Exception as e:
            logger.error(f"Failed to save fills CSV: {e}")

    async def run(self):
        self.setup_signal_handlers()
        cfg = load_config()
        wallet = Account.from_key(cfg["secret_key"])
        API_URL = constants.TESTNET_API_URL if cfg["network"].lower() == "testnet" else constants.MAINNET_API_URL
        exchange = Exchange(wallet, base_url=API_URL)

        self.position_tracker = PositionTracker(exchange, COIN)
        self.order_manager = OrderManager(exchange, COIN, self.rate_limiter)

        await self.position_tracker.update_from_api()
        logger.info(f"Initial position: {await self.position_tracker.get_position():.6f}")
        logger.info(f"Tick size for {COIN}: {self.tick_size}")

        reconcile_task = asyncio.create_task(self.reconcile_loop())
        healthcheck_task = asyncio.create_task(self.healthcheck_loop())
        metrics_task = asyncio.create_task(self.metrics_loop())

        retry_count = 0
        reconnect_delay = 1

        while not self.stop_event.is_set() and retry_count < MAX_RECONNECT_ATTEMPTS:
            try:
                async with websockets.connect(URI, ping_interval=20, ping_timeout=60, close_timeout=10) as ws:
                    logger.info("WS connected")
                    retry_count = 0
                    reconnect_delay = 1
                    self.metrics.reconnections += 1

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
                                fills_data = data["data"]
                                is_snapshot = fills_data.get("isSnapshot", False) if isinstance(fills_data, dict) else False
                                if isinstance(fills_data, dict):
                                    fills = fills_data.get("fills", [])
                                elif isinstance(fills_data, list):
                                    fills = fills_data
                                else:
                                    fills = []
                                # Skip entire snapshot
                                if is_snapshot:
                                    logger.debug(f"Skipping snapshot with {len(fills)} fills")
                                    continue
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
                try:
                    await self.order_manager.cancel_all()
                except Exception as ee:
                    logger.error(f"Cancel on disconnect failed: {ee}")
                retry_count += 1
                self.metrics.reconnections += 1
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 30)

        reconcile_task.cancel()
        healthcheck_task.cancel()
        metrics_task.cancel()
        await asyncio.gather(reconcile_task, healthcheck_task, metrics_task, return_exceptions=True)

        try:
            cancelled = await self.order_manager.cancel_all()
            self.metrics.cancels_sent += cancelled
        except Exception as e:
            logger.error(f"Final cancel failed: {e}")

        self.metrics.log()
        self.metrics.save_json()
        self.save_fills_csv()
        logger.info("Bot stopped cleanly.")

# ========================
# --- ENTRYPOINT ---
# ========================
if __name__ == "__main__":
    bot = EnhancedMarketMaker()
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        logger.info("Interrupted by user.")
