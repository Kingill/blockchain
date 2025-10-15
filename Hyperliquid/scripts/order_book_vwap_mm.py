#!/usr/bin/env python3
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
BASE_SIZE = 0.0003
LEVELS = 2
BASE_SPREAD = 0.075
INTERVAL = 45
DRY_RUN = False
TICK_SIZE = 1.0
CONFIG_PATH = Path("config.json")
MAX_POSITION = 0.001
BIAS_FACTOR = 0.10
MAX_SHIFT_PCT = 0.02
BIAS_CLAMP = 0.8
SMOOTH_ALPHA = 0.35
CIRCUIT_BREAKER_MULT = 1.5
#MIN_REQUOTE_MOVE = 20
MIN_REQUOTE_MOVE = 50.0
CANCEL_DELAY = 1.0
RECONCILE_INTERVAL = 30
MAX_RECONNECT_ATTEMPTS = 10
HEARTBEAT_INTERVAL = 20  # Envoyer un heartbeat toutes les 20s

# Rate limiting
MAX_REQUESTS_PER_SECOND = 10
RATE_LIMIT_WINDOW = 1.0

# ========================
# --- LOGGING ---
# ========================
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)
console = Console()

# ========================
# --- DATA CLASSES ---
# ========================
@dataclass
class MarketState:
    mid_price: float
    mid_vwap: float
    best_bid: float
    best_ask: float
    timestamp: float

@dataclass
class Metrics:
    quotes_sent: int = 0
    cancels_sent: int = 0
    fills_received: int = 0
    errors: int = 0
    last_requote: float = 0
    reconnections: int = 0

    def log_summary(self):
        logger.info(f"Metrics - Quotes: {self.quotes_sent}, Cancels: {self.cancels_sent}, "
                    f"Fills: {self.fills_received}, Errors: {self.errors}, Reconnections: {self.reconnections}")

# ========================
# --- RATE LIMITER ---
# ========================
class RateLimiter:
    """Simple rate limiter using a sliding window."""
    def __init__(self, max_requests: int, window: float):
        self.max_requests = max_requests
        self.window = window
        self.requests = deque()
    
    async def acquire(self):
        """Wait until we can make a request without exceeding rate limit."""
        now = time.time()
        
        # Remove old requests outside the window
        while self.requests and self.requests[0] < now - self.window:
            self.requests.popleft()
        
        # If we're at the limit, wait
        if len(self.requests) >= self.max_requests:
            sleep_time = self.requests[0] + self.window - now + 0.01
            if sleep_time > 0:
                logger.debug(f"Rate limit reached, sleeping {sleep_time:.2f}s")
                await asyncio.sleep(sleep_time)
        
        self.requests.append(time.time())

# ========================
# --- UTILITIES ---
# ========================
def load_config(path=CONFIG_PATH):
    with open(path) as f:
        cfg = json.load(f)
    cfg.setdefault("network", "testnet")
    return cfg

def round_to_tick(price: float, tick_size: float) -> float:
    return round(price / tick_size) * tick_size

def vwap(levels: list, n: int = 5, fallback: float = 0) -> float:
    if not levels:
        return fallback
    
    total_px_vol = 0
    total_vol = 0
    
    for level in levels[:n]:
        # Validation robuste
        if not isinstance(level, dict):
            continue
        if 'px' not in level or 'sz' not in level:
            continue
        
        try:
            px = float(level['px'])
            sz = float(level['sz'])
            total_px_vol += px * sz
            total_vol += sz
        except (ValueError, TypeError):
            continue
    
    return total_px_vol / total_vol if total_vol > 0 else fallback

def render_market_table(mid, vwap_val, adj_mid, pos, bids_on, asks_on, circuit_breaker_active=False):
    table = Table(title="Market Maker BTC")
    table.add_column("Mid")
    table.add_column("VWAP")
    table.add_column("Adj Mid")
    table.add_column("Position")
    table.add_column("Bids")
    table.add_column("Asks")
    table.add_column("Status")
    
    status = "[red]CIRCUIT BREAKER![/red]" if circuit_breaker_active else "[green]RUNNING[/green]"
    
    table.add_row(
        f"{mid:.1f}", f"{vwap_val:.1f}", f"{adj_mid:.1f}", f"{pos:.6f}",
        "[green]ON[/green]" if bids_on else "[red]OFF[/red]",
        "[green]ON[/green]" if asks_on else "[red]OFF[/red]",
        status
    )
    return table

# ========================
# --- COMPONENTS ---
# ========================
class PositionTracker:
    def __init__(self, exchange: Exchange, coin: str):
        self.exchange = exchange
        self.coin = coin
        self.position = 0.0
        self._lock = asyncio.Lock()  # Protection contre les race conditions

    async def update_from_api(self):
        """Update position from API with proper locking."""
        from hyperliquid.info import Info
        api_url = constants.TESTNET_API_URL if "testnet" in self.exchange.base_url else constants.MAINNET_API_URL
        info = Info(api_url, skip_ws=True)
        
        try:
            user_state = info.user_state(self.exchange.wallet.address)
        except Exception as e:
            logger.error(f"Failed to fetch user state: {e}")
            return self.position
        
        async with self._lock:
            position_updated = False
            if user_state and "assetPositions" in user_state:
                for asset in user_state["assetPositions"]:
                    pos_data = asset.get("position", {})
                    if pos_data.get("coin") == self.coin:
                        self.position = float(pos_data.get("szi", 0))
                        position_updated = True
                        break
            
            if not position_updated:
                self.position = 0.0

            return self.position

    async def update_from_fill(self, fill: dict):
        """Update position from fill with proper locking."""
        if not isinstance(fill, dict) or fill.get("coin") != self.coin:
            return
        
        try:
            sz = float(fill.get("sz", 0))
            side = fill.get("side")
        except (ValueError, TypeError):
            logger.error(f"Invalid fill data: {fill}")
            return
        
        async with self._lock:
            if side == "B":
                self.position += sz
            elif side == "A":
                self.position -= sz
            else:
                logger.warning(f"Unknown side in fill: {side}")

    async def get_position(self) -> float:
        """Thread-safe position getter."""
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
        """Cancel all open orders and return count of successfully cancelled orders."""
        async with self._lock:
            if not self.open_oids:
                return 0
            
            oids_to_cancel = list(self.open_oids)
        
        successfully_cancelled = 0
        failed_oids = []
        
        for oid in oids_to_cancel:
            try:
                await self.rate_limiter.acquire()
                resp = self.exchange.cancel(self.coin, oid)
                
                if resp and resp.get("status") == "ok":
                    async with self._lock:
                        self.open_oids.discard(oid)
                    successfully_cancelled += 1
                else:
                    logger.warning(f"Cancel OID {oid} failed: {resp}")
                    failed_oids.append(oid)
                    
            except Exception as e:
                logger.error(f"Exception cancelling OID {oid}: {e}")
                failed_oids.append(oid)
        
        if failed_oids:
            logger.warning(f"Failed to cancel {len(failed_oids)} orders: {failed_oids}")
        
        await asyncio.sleep(CANCEL_DELAY)
        return successfully_cancelled

    async def place_orders(self, orders: List[dict]) -> List[int]:
        """Place orders and return list of successfully placed OIDs."""
        if not orders:
            return []
        
        try:
            await self.rate_limiter.acquire()
            resp = self.exchange.bulk_orders(orders)
            
            if not resp or resp.get("status") != "ok":
                logger.error(f"Bulk orders failed: {resp}")
                return []
            
            oids = []
            async with self._lock:
                for st in resp.get("response", {}).get("data", {}).get("statuses", []):
                    if "resting" in st:
                        oid = st["resting"]["oid"]
                        oids.append(oid)
                        self.open_oids.add(oid)
                    elif "error" in st:
                        logger.warning(f"Order placement error: {st['error']}")
            
            return oids
            
        except Exception as e:
            logger.error(f"Place orders exception: {e}")
            return []

    async def get_open_count(self) -> int:
        """Get count of open orders."""
        async with self._lock:
            return len(self.open_oids)

# ========================
# --- BOT CLASS ---
# ========================
class MarketMakerBot:
    def __init__(self):
        self.stop_event = asyncio.Event()
        self.position_tracker: Optional[PositionTracker] = None
        self.order_manager: Optional[OrderManager] = None
        self.metrics = Metrics()
        self.mid_adj_ema: Optional[float] = None
        self.circuit_breaker_active = False
        self.rate_limiter = RateLimiter(MAX_REQUESTS_PER_SECOND, RATE_LIMIT_WINDOW)
        self.last_heartbeat = time.time()

    def setup_signal_handlers(self):
        signal.signal(signal.SIGINT, lambda s, f: self.stop_event.set())
        signal.signal(signal.SIGTERM, lambda s, f: self.stop_event.set())

    async def handle_l2book(self, data: dict):
        """Handle L2 book updates with improved validation."""
        levels = data.get("levels", [[], []])
        if len(levels) < 2:
            logger.warning("Invalid levels structure")
            return
        
        bids, asks = levels[0], levels[1]

        # Validation robuste
        if not bids or not asks:
            logger.warning("Empty bids or asks")
            return
        
        if not isinstance(bids[0], dict) or not isinstance(asks[0], dict):
            logger.warning("Invalid bid/ask format")
            return
        
        if 'px' not in bids[0] or 'px' not in asks[0]:
            logger.warning("Missing 'px' in top of book")
            return

        try:
            best_bid = float(bids[0]['px'])
            best_ask = float(asks[0]['px'])
        except (ValueError, TypeError, KeyError) as e:
            logger.error(f"Error parsing best bid/ask: {e}")
            return
        
        mid_price = (best_bid + best_ask) / 2

        # Skip requote if price hasn't moved enough
        if self.mid_adj_ema is not None and abs(mid_price - self.mid_adj_ema) < MIN_REQUOTE_MOVE:
            return

        mid_vwap = (vwap(bids, fallback=mid_price) + vwap(asks, fallback=mid_price)) / 2
        self.mid_adj_ema = mid_price if self.mid_adj_ema is None else SMOOTH_ALPHA * mid_price + (1 - SMOOTH_ALPHA) * self.mid_adj_ema

        pos = await self.position_tracker.get_position()
        norm_bias = max(min(pos / MAX_POSITION, BIAS_CLAMP), -BIAS_CLAMP)
        shift_px = -norm_bias * BIAS_FACTOR * mid_vwap * MAX_SHIFT_PCT
        adj_mid = self.mid_adj_ema + shift_px

        # Circuit breaker with graceful degradation
        bids_enabled = True
        asks_enabled = True
        
        if abs(pos) > MAX_POSITION * CIRCUIT_BREAKER_MULT:
            self.circuit_breaker_active = True
            logger.critical(f"Circuit breaker! Pos: {pos:.6f} > {MAX_POSITION * CIRCUIT_BREAKER_MULT:.6f}")
            
            # Instead of stopping, disable the dangerous side
            if pos > 0:  # Long position: disable buys
                bids_enabled = False
                logger.warning("Disabling BID side due to large long position")
            else:  # Short position: disable sells
                asks_enabled = False
                logger.warning("Disabling ASK side due to large short position")
        else:
            if self.circuit_breaker_active:
                logger.info("Circuit breaker cleared - resuming normal operation")
                self.circuit_breaker_active = False

        # Build orders
        orders = []
        for i in range(1, LEVELS + 1):
            spread = BASE_SPREAD / 100 * i
            buy_px = round_to_tick(adj_mid * (1 - spread), TICK_SIZE)
            sell_px = round_to_tick(adj_mid * (1 + spread), TICK_SIZE)
            
            if bids_enabled:
                orders.append({
                    "coin": COIN, 
                    "is_buy": True, 
                    "limit_px": buy_px, 
                    "sz": BASE_SIZE,
                    "reduce_only": False, 
                    "order_type": {"limit": {"tif": "Gtc"}}
                })
            
            if asks_enabled:
                orders.append({
                    "coin": COIN, 
                    "is_buy": False, 
                    "limit_px": sell_px, 
                    "sz": BASE_SIZE,
                    "reduce_only": False, 
                    "order_type": {"limit": {"tif": "Gtc"}}
                })

        if not DRY_RUN and self.order_manager and orders:
            cancelled_count = await self.order_manager.cancel_all()
            self.metrics.cancels_sent += cancelled_count
            
            placed_oids = await self.order_manager.place_orders(orders)
            if placed_oids:
                self.metrics.quotes_sent += 1

        # UI Update
        if time.time() - self.metrics.last_requote >= 1.0:
            console.clear()
            console.print(render_market_table(
                mid_price, mid_vwap, adj_mid, pos, 
                bids_enabled, asks_enabled, self.circuit_breaker_active
            ))
            self.metrics.last_requote = time.time()

    async def handle_user_fills(self, fill: dict):
        """Handle fill updates."""
        if not isinstance(fill, dict):
            logger.error(f"Invalid fill type: {type(fill)}")
            return
        
        # Validate required fields
        if 'side' not in fill or 'sz' not in fill:
            logger.warning(f"Fill missing required fields: {fill}")
            return
        
        await self.position_tracker.update_from_fill(fill)
        self.metrics.fills_received += 1
        pos = await self.position_tracker.get_position()
        logger.info(f"FILL! Side: {fill.get('side')}, Sz: {fill.get('sz')}, Px: {fill.get('px', 'N/A')}, New Pos: {pos:.6f}")

    async def reconcile_position_loop(self):
        """Background task to reconcile position with API."""
        while not self.stop_event.is_set():
            try:
                await asyncio.sleep(RECONCILE_INTERVAL)
                if self.stop_event.is_set():
                    break
                
                old_pos = await self.position_tracker.get_position()
                new_pos = await self.position_tracker.update_from_api()
                
                if abs(old_pos - new_pos) > 1e-6:
                    logger.warning(f"Position reconciliation: API={new_pos:.6f} vs Local={old_pos:.6f}")
                else:
                    logger.debug("Position reconciliation: OK")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Reconciliation error: {e}")

    async def heartbeat_loop(self, ws):
        """Send periodic heartbeats to keep connection alive."""
        while not self.stop_event.is_set():
            try:
                await asyncio.sleep(HEARTBEAT_INTERVAL)
                if self.stop_event.is_set():
                    break
                
                # Send a ping or dummy message
                await ws.ping()
                self.last_heartbeat = time.time()
                logger.debug("Heartbeat sent")
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Heartbeat error: {e}")
                break

    async def run(self):
        """Main bot loop."""
        self.setup_signal_handlers()
        cfg = load_config()
        wallet = Account.from_key(cfg["secret_key"])
        API_URL = constants.TESTNET_API_URL if cfg["network"].lower() == "testnet" else constants.MAINNET_API_URL
        exchange = Exchange(wallet, base_url=API_URL)
        
        self.position_tracker = PositionTracker(exchange, COIN)
        self.order_manager = OrderManager(exchange, COIN, self.rate_limiter)

        await self.position_tracker.update_from_api()
        initial_pos = await self.position_tracker.get_position()
        logger.info(f"Initial position: {initial_pos:.6f}")
        
        reconcile_task = asyncio.create_task(self.reconcile_position_loop())

        retry_count = 0
        reconnect_delay = 1
        
        while not self.stop_event.is_set() and retry_count < MAX_RECONNECT_ATTEMPTS:
            try:
                async with websockets.connect(
                    URI,
                    ping_interval=20,
                    ping_timeout=60,
                    close_timeout=10
                ) as ws:
                    logger.info("WebSocket connected")
                    retry_count = 0
                    reconnect_delay = 1
                    self.metrics.reconnections += 1

                    # Subscribe to channels
                    await ws.send(json.dumps({
                        "method": "subscribe",
                        "subscription": {"type": "l2Book", "coin": COIN}
                    }))
                    await ws.send(json.dumps({
                        "method": "subscribe",
                        "subscription": {"type": "userFills", "user": wallet.address}
                    }))
                    
                    # Start heartbeat task
                    heartbeat_task = asyncio.create_task(self.heartbeat_loop(ws))

                    # Message loop
                    while not self.stop_event.is_set():
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=INTERVAL)
                            
                            try:
                                data = json.loads(msg)
                            except json.JSONDecodeError:
                                logger.warning(f"Non-JSON message: {msg[:50]}...")
                                continue

                            if not isinstance(data, dict):
                                continue

                            channel = data.get("channel")
                            if channel == "l2Book" and data.get("data"):
                                await self.handle_l2book(data["data"])
                            elif channel == "userFills" and data.get("data"):
                                fills_data = data["data"]
                                # userFills can be a snapshot (dict) or update (list)
                                if isinstance(fills_data, dict):
                                    # Snapshot format: {"isSnapshot": true, "fills": [...]}
                                    fills = fills_data.get("fills", [])
                                elif isinstance(fills_data, list):
                                    # Update format: direct list of fills
                                    fills = fills_data
                                else:
                                    logger.warning(f"Unexpected userFills format: {type(fills_data)}")
                                    continue
                                
                                for fill in fills:
                                    if isinstance(fill, dict):
                                        await self.handle_user_fills(fill)
                                    else:
                                        logger.warning(f"Invalid fill object: {type(fill)}")
                            
                        except asyncio.TimeoutError:
                            # Timeout is normal - just means no messages in INTERVAL
                            continue
                        
                        except websockets.exceptions.ConnectionClosedError:
                            logger.warning("Connection closed during message receive")
                            break
                    
                    # Clean up heartbeat task
                    heartbeat_task.cancel()
                    await asyncio.gather(heartbeat_task, return_exceptions=True)

            except websockets.exceptions.ConnectionClosedError as e:
                logger.error(f"WebSocket disconnected: {e}")
                await self.order_manager.cancel_all()
                retry_count += 1
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 30)
                
            except Exception as e:
                logger.error(f"WebSocket error: {e}", exc_info=True)
                await self.order_manager.cancel_all()
                retry_count += 1
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 30)

        # Shutdown
        reconcile_task.cancel()
        await asyncio.gather(reconcile_task, return_exceptions=True)
        
        if retry_count >= MAX_RECONNECT_ATTEMPTS:
            logger.critical("Max reconnection attempts reached")
        
        await self.order_manager.cancel_all()
        self.metrics.log_summary()
        logger.info("Bot stopped - all orders cancelled")


# ========================
# --- ENTRYPOINT ---
# ========================
if __name__ == "__main__":
    bot = MarketMakerBot()
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt - shutting down...")
