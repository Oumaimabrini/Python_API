import asyncio
import time
import uuid
from datetime import datetime, timedelta
from typing import Dict, Optional, List
from enum import Enum
import aiohttp
import uvicorn
from fastapi import FastAPI, Security, HTTPException, Request, Depends, BackgroundTasks
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel
import requests
import aiohttp
import time

from fastapi import WebSocket, WebSocketDisconnect, HTTPException
import json

from contextlib import asynccontextmanager
import websockets

# BINANCE_WS_URL = "wss://stream.binance.com:9443/ws/btcusdt@depth5"
BINANCE_WS_URL = "wss://stream.binance.com:9443/ws/btcusdt@depth10"
KRAKEN_WS_URL = "wss://ws.kraken.com"
BASE_REST_SPOT_URL = "https://api.binance.com"
BASE_REST_KRAKEN_URL = "https://api.kraken.com/0/public/OHLC"

# Cache duration in seconds (e.g., 1 hour)
CACHE_DURATION = 3600

# Global cache variables
cached_trading_pairs = {
    "binance": {"timestamp": 0, "data": []},
    "kraken": {"timestamp": 0, "data": []}
}

# Unique definition of order_books
order_books: Dict[str, Dict[str, Dict[str, List]]] = {
    "binance": {},
    "kraken": {}
}


async def get_binance_trading_pairs():
    now = time.time()
    # Check the cache
    if now - cached_trading_pairs["binance"]["timestamp"] < CACHE_DURATION:
        return cached_trading_pairs["binance"]["data"]

    url = "https://api.binance.com/api/v3/exchangeInfo"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            data = await response.json()
            pairs = [symbol["symbol"] for symbol in data["symbols"]]
            cached_trading_pairs["binance"] = {"timestamp": now, "data": pairs}
            return pairs


async def get_kraken_trading_pairs():
    now = time.time()
    if now - cached_trading_pairs["kraken"]["timestamp"] < CACHE_DURATION:
        return cached_trading_pairs["kraken"]["data"]

    url = "https://api.kraken.com/0/public/AssetPairs"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            data = await response.json()
            pairs = list(data["result"].keys())
            cached_trading_pairs["kraken"] = {"timestamp": now, "data": pairs}
            return pairs


# Store the active pair for each exchange
active_pair = {
    "binance": None,
    "kraken": None
}


async def binance_orderbook_updater():
    while True:
        try:
            if not active_pair["binance"]:
                await asyncio.sleep(2)
                continue

            current_pair = active_pair["binance"]
            url = f"wss://stream.binance.com:9443/ws/{current_pair.lower()}@depth10"
            print(f"➡️ Connecting to Binance WebSocket for {current_pair}: {url}")

            async with websockets.connect(url) as websocket:
                async for message in websocket:
                    # Check if the active pair has changed
                    if active_pair["binance"] != current_pair:
                        print(
                            f"⚠️ Pair changed ({current_pair} -> {active_pair['binance']}). Disconnecting and reconnecting.")
                        break

                    data = json.loads(message)
                    if "bids" in data and "asks" in data:
                        order_books["binance"][current_pair] = {
                            "bids": data["bids"],
                            "asks": data["asks"]
                        }
                        # Debug
                        print(f"✅ Order book updated for {current_pair}")
        except Exception as e:
            print("❌ Exception in binance_orderbook_updater:", e)
            await asyncio.sleep(5)


async def kraken_orderbook_updater():
    while True:
        try:
            if not active_pair["kraken"]:
                await asyncio.sleep(2)
                continue

            pair = active_pair["kraken"]
            async with websockets.connect("wss://ws.kraken.com") as websocket:
                subscribe_message = {
                    "event": "subscribe",
                    "pair": [pair],  # Set the active pair
                    "subscription": {"name": "book", "depth": 10}
                }
                await websocket.send(json.dumps(subscribe_message))

                async for message in websocket:
                    data = json.loads(message)
                    if isinstance(data, list) and len(data) > 1:
                        payload = data[1]
                        received_pair = data[-1]  # The last value is the pair
                        if received_pair == pair:
                            if "b" in payload or "bs" in payload:
                                bids_list = payload.get("bs") or payload.get("b")
                                order_books["kraken"][pair]["bids"] = [b[:2] for b in bids_list]
                            if "a" in payload or "as" in payload:
                                asks_list = payload.get("as") or payload.get("a")
                                order_books["kraken"][pair]["asks"] = [a[:2] for a in asks_list]
        except Exception as e:
            print("Exception in kraken_orderbook_updater:", e)
            await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    loop = asyncio.get_running_loop()
    loop.create_task(binance_orderbook_updater())
    loop.create_task(kraken_orderbook_updater())
    yield  # Wait for shutdown


app = FastAPI(title="Crypto Data & Paper Trading API", lifespan=lifespan)


@app.get("/orderbook/{exchange}/{pair}")
async def get_orderbook(exchange: str, pair: str):
    """Returns the stored order book for a given pair"""
    if exchange not in order_books:
        print(f"❌ Exchange not found: {exchange}")
        raise HTTPException(status_code=404, detail="Exchange not found")

    if pair not in order_books[exchange]:
        print(f"⚠️ Pair not found: {pair} in {exchange}")
        print("📋 Current content of order_books:", json.dumps(order_books[exchange], indent=4))
        raise HTTPException(status_code=404, detail="Pair not found")

    print(f"📤 Sending order book {exchange} - {pair}")  # Debug
    return order_books[exchange][pair]


# Global WebSocket: sends all pairs
@app.websocket("/ws/orderbook")
async def websocket_orderbook_global(websocket: WebSocket):
    """WebSocket that continuously sends the order book"""
    await websocket.accept()
    try:
        print("🔌 WebSocket client connected for global orderbook")
        while True:
            snapshot = {}

            for exch, pairs_data in order_books.items():
                snapshot[exch] = {}
                for p, ob in pairs_data.items():
                    if "bids" in ob and "asks" in ob and ob["bids"] and ob["asks"]:
                        snapshot[exch][p] = {
                            "bids": ob["bids"],
                            "asks": ob["asks"]
                        }

            if any(snapshot.values()):
                print(f"📡 [DEBUG] Sending WebSocket snapshot: {json.dumps(snapshot, indent=4)}")
                await websocket.send_text(json.dumps(snapshot))

            await asyncio.sleep(1)
    except WebSocketDisconnect:
        print("🔌 WebSocket client disconnected")
    except Exception as e:
        print(f"❌ WebSocket error: {e}")


@app.post("/set_active_pair/{exchange}/{pair}")
async def set_active_pair(exchange: str, pair: str):
    exchange = exchange.lower()

    if exchange not in active_pair:
        raise HTTPException(status_code=400, detail="Unsupported exchange")

    # 1) Retrieve the list of pairs on the exchange
    if exchange == "binance":
        all_pairs = await get_binance_trading_pairs()
    else:
        all_pairs = await get_kraken_trading_pairs()

    # Normalize
    pair_upper = pair.upper()
    # Check if the pair exists
    if pair_upper not in [p.upper() for p in all_pairs]:
        raise HTTPException(status_code=404, detail="This pair does not exist on this exchange.")

    # Update the active pair
    active_pair[exchange] = pair_upper

    # Initialize the order book if it doesn't exist
    order_books.setdefault(exchange, {})[pair_upper] = {"bids": [], "asks": []}

    return {"message": f"Active pair for {exchange} updated: {pair_upper}"}


class APIClient:
    def __init__(self, base_url="http://localhost:8000", api_key=None):
        self.base_url = base_url
        self.api_key = api_key

    def get_headers(self):
        return {"X-Token-ID": self.api_key} if self.api_key else {}

    def set_active_pair(self, exchange: str, pair: str):
        """Sets the active pair for the given exchange."""
        try:
            url = f"{self.base_url}/set_active_pair/{exchange}/{pair}"
            response = requests.post(url, headers=self.get_headers())
            return response.json() if response.status_code == 200 else None
        except Exception as e:
            print(f"Exception in set_active_pair: {e}")
            return None

    async def websocket_orderbook(self, exchange: str):
        """Connects to the WebSocket to follow the order book of the active pair."""
        uri = f"ws://localhost:8000/ws/orderbook/{exchange}"
        async with websockets.connect(uri) as websocket:
            print(f"Connected to WebSocket order book stream for {exchange}.")
            try:
                while True:
                    data = await websocket.recv()
                    print(f"Order Book Update for {exchange}: {data}")
            except websockets.exceptions.ConnectionClosed:
                print("WebSocket connection closed.")


# Storage of client WebSockets
clients = []


def get_top_10_levels(order_book):
    """Retrieves the top 10 levels (bids & asks)"""
    return {
        "bids": order_book["bids"][:10],
        "asks": order_book["asks"][:10]
    }


@app.websocket("/ws/orderbook/{exchange}")
async def websocket_orderbook(websocket: WebSocket, exchange: str):
    """WebSocket that sends the Order Book of the active pair only."""
    await websocket.accept()
    exchange = exchange.lower()

    try:
        while True:
            pair = active_pair.get(exchange)
            ob = order_books.get(exchange, {}).get(pair, {"bids": [], "asks": []})
            aggregated = get_top_10_levels(ob)
            await websocket.send_text(json.dumps(aggregated))
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass


### =========================
### Enums and Pydantic Models
### =========================

class ClientType(str, Enum):
    ANONYMOUS = "anonymous"
    BASIC = "basic_client"
    PREMIUM = "premium_client"


# TWAP side / order type
class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


# Order model for paper-trading
class TWAPOrderRequest(BaseModel):
    exchange: str
    pair: str
    side: OrderSide
    total_quantity: float
    limit_price: float
    duration_seconds: int
    slices: int  # number of 'slices' in the duration


# Order creation response
class TWAPOrderResponse(BaseModel):
    order_id: str
    message: str


# Order information model
class TWAPOrderStatus(BaseModel):
    order_id: str
    exchange: str
    pair: str
    side: OrderSide
    total_quantity: float
    executed_quantity: float
    limit_price: float
    duration_seconds: int
    slices: int
    slices_executed: int
    is_completed: bool
    created_at: datetime
    updated_at: datetime


# Data response
class DataResponse(BaseModel):
    client_type: ClientType
    message: str
    timestamp: str
    remaining_tokens: float
    rate_limit: float


class StatusResponse(BaseModel):
    status: str


class RegisterResponse(BaseModel):
    api_key: str
    client_type: ClientType


### =========================
### Rate Limiting Management
### =========================
class TokenBucket:
    def __init__(self, rate: float, bucket_size: int):
        self.rate = rate
        self.bucket_size = bucket_size
        self.tokens = bucket_size
        self.last_update = time.time()

    def try_consume(self) -> bool:
        now = time.time()
        time_passed = now - self.last_update
        self.tokens = min(self.bucket_size, self.tokens + time_passed * self.rate)
        self.last_update = now

        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False


API_KEY_NAME = "X-Token-ID"
token_id_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

API_KEYS: Dict[str, Dict] = {}
DEFAULT_LIMITS = {
    ClientType.ANONYMOUS: {"rate_limit": 2, "bucket_size": 4},
    ClientType.BASIC: {"rate_limit": 5, "bucket_size": 10},
    ClientType.PREMIUM: {"rate_limit": 10, "bucket_size": 20},
}
rate_limiters: Dict[str, TokenBucket] = {}

### =========================================
### In-memory data for Order Books
### & Paper Trading
### =========================================


# In-memory storage of TWAP orders
# {order_id: {"request": TWAPOrderRequest, "status": {...}}}
twap_orders: Dict[str, Dict] = {}


### =========================================
### Authentication & Rate Limiting Functions
### =========================================
async def get_rate_limiter(request: Request, api_key: Optional[str] = Security(token_id_header)):
    identifier = f"apikey_{api_key}" if api_key in API_KEYS else f"ip_{request.client.host}"

    if identifier not in rate_limiters:
        client_type = API_KEYS[api_key]["client_type"] if api_key in API_KEYS else ClientType.ANONYMOUS
        rate_limiters[identifier] = TokenBucket(
            DEFAULT_LIMITS[client_type]["rate_limit"],
            DEFAULT_LIMITS[client_type]["bucket_size"]
        )

    return rate_limiters[identifier], API_KEYS.get(api_key, {}).get("client_type", ClientType.ANONYMOUS)


### =========================================
### Routes / Endpoints
### =========================================

@app.post("/register", response_model=RegisterResponse)
async def register(client_type: ClientType):
    api_key = str(uuid.uuid4())[:8]  # Short API key
    API_KEYS[api_key] = {
        "client_type": client_type,
        "rate_limit": DEFAULT_LIMITS[client_type]["rate_limit"],
        "bucket_size": DEFAULT_LIMITS[client_type]["bucket_size"]
    }
    return {"api_key": api_key, "client_type": client_type}


@app.get("/status", response_model=StatusResponse)
async def get_status():
    return {"status": "operational"}


@app.get("/data", response_model=DataResponse)
async def get_data(request: Request, api_key: Optional[str] = Security(token_id_header)):
    rate_limiter, client_type = await get_rate_limiter(request, api_key)
    if not rate_limiter.try_consume():
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    return {
        "client_type": client_type,
        "message": "Here is some limited data",
        "timestamp": datetime.now().isoformat(),
        "remaining_tokens": rate_limiter.tokens,
        "rate_limit": rate_limiter.rate
    }


### =========================================
### Public Data Endpoints (Candles, Exchanges, Pairs)
### =========================================

@app.get("/exchanges", tags=["public"])
async def list_exchanges():
    """
    Returns the list of supported exchanges.
    """
    return ["binance", "kraken"]


@app.get("/exchanges/{exchange}/pairs", tags=["public"])
async def list_pairs(exchange: str):
    """
    Returns the list of available pairs on a given exchange.
    """
    if exchange not in ["binance", "kraken"]:
        raise HTTPException(status_code=404, detail="Exchange not supported")
    if exchange == "binance":
        pairs = await get_binance_trading_pairs()
    else:
        pairs = await get_kraken_trading_pairs()

    return pairs


@app.websocket("/ws/auth/orderbook/{exchange}")
async def websocket_orderbook_auth(websocket: WebSocket, exchange: str):
    # Retrieve the token from the request parameters
    token = websocket.query_params.get("token")
    if not token or token not in API_KEYS:
        await websocket.close(code=1008)
        return

    await websocket.accept()
    exchange = exchange.lower()
    try:
        while True:
            pair = active_pair.get(exchange)
            ob = order_books.get(exchange, {}).get(pair, {"bids": [], "asks": []})
            aggregated = get_top_10_levels(ob)
            await websocket.send_text(json.dumps(aggregated))
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass


async def get_kraken_klines(session: aiohttp.ClientSession, symbol: str, interval: str = "1m", limit: int = 5,
                            start_time: Optional[datetime] = None):
    """
    Fetch historical kline data from Kraken and format it to match the Binance format.
    """
    kraken_symbol = symbol.upper()

    # Convert Binance interval format to Kraken interval format
    interval_mapping = {
        "1m": "1",
        "5m": "5",
        "15m": "15",
        "30m": "30",
        "1h": "60",
        "1d": "1440",
        "1w": "10080"
    }

    kraken_interval = interval_mapping.get(interval, "60")  # Default to 1h if interval not found

    params = {
        'pair': kraken_symbol,
        'interval': kraken_interval,
        'count': limit
    }

    if start_time:
        params['since'] = int(start_time.timestamp())

    try:
        async with session.get(BASE_REST_KRAKEN_URL, params=params) as response:
            if response.status == 200:
                data = await response.json()

                if "result" in data and kraken_symbol in data["result"]:
                    candles = [
                        [
                            int(k[0]),  # timestamp
                            float(k[1]),  # open
                            float(k[2]),  # high
                            float(k[3]),  # low
                            float(k[4]),  # close
                            float(k[6])  # volume (Kraken uses k[6] instead of k[5])
                        ] for k in data["result"][kraken_symbol]
                    ]

                    return candles[:limit]  # Return just the list of candles
                else:
                    error_msg = f"Symbol not supported on Kraken or data not found: {data}"
                    print(error_msg)
                    raise HTTPException(status_code=404, detail=error_msg)
            else:
                error_msg = f"Error fetching Kraken klines: Status {response.status}"
                print(error_msg)
                raw_response = await response.text()
                print(f"Raw response: {raw_response}")
                raise HTTPException(status_code=response.status, detail=error_msg)
    except Exception as e:
        error_msg = f"Exception in fetching Kraken klines: {str(e)}"
        print(error_msg)
        raise HTTPException(status_code=500, detail=error_msg)


async def get_binance_klines(session, symbol: str, interval: str = '1m', start_time: datetime = None, limit: int = 5):
    """
    Fetch historical kline data from Binance.
    """
    endpoint = "/api/v3/klines"
    params = {
        'symbol': symbol,
        'interval': interval,
        'limit': limit
    }

    if start_time:
        params['startTime'] = int(start_time.timestamp() * 1000)

    try:
        async with session.get(f"{BASE_REST_SPOT_URL}{endpoint}", params=params) as response:
            if response.status == 200:
                data = await response.json()

                candles = [
                    [
                        int(k[0] / 1000),  # timestamp (convert from ms to s)
                        float(k[1]),  # open
                        float(k[2]),  # high
                        float(k[3]),  # low
                        float(k[4]),  # close
                        float(k[5])  # volume
                    ] for k in data
                ]

                return candles
            else:
                raise HTTPException(status_code=response.status,
                                    detail=f"Error fetching binance klines: {response.status}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Exception in fetching binance klines: {str(e)}")


@app.get("/klines/{exchange}/{symbol}", tags=["public"])
async def get_klines(exchange: str, symbol: str, interval: str = "1m", limit: int = 100):
    """
    Fetch real candlestick (kline) data from Binance or Kraken.
    """
    if exchange not in ["binance", "kraken"]:
        raise HTTPException(status_code=404, detail=f"Exchange not supported: {exchange}")

    # Check if the symbol is supported for this exchange
    # Retrieve the list
    if exchange == "binance":
        all_pairs = await get_binance_trading_pairs()
    else:
        all_pairs = await get_kraken_trading_pairs()

    if symbol.upper() not in [p.upper() for p in all_pairs]:
        raise HTTPException(status_code=404, detail=f"Symbol {symbol} not supported on {exchange}")
    try:
        async with aiohttp.ClientSession() as session:
            if exchange.lower() == "binance":
                candles = await get_binance_klines(session, symbol.upper(), interval, None, limit)
            elif exchange.lower() == "kraken":
                candles = await get_kraken_klines(session, symbol.upper(), interval, limit)
            else:
                raise HTTPException(status_code=404, detail=f"Exchange not supported: {exchange}")

        # Standardize the format for frontend consumption
        formatted_candles = []
        for candle in candles:
            formatted_candles.append({
                "timestamp": candle[0],
                "open": float(candle[1]),
                "high": float(candle[2]),
                "low": float(candle[3]),
                "close": float(candle[4]),
                "volume": float(candle[5])
            })

        return {
            "exchange": exchange.lower(),
            "symbol": symbol.upper(),
            "interval": interval,
            "candles": formatted_candles
        }
    except Exception as e:
        print(f"Error in get_klines: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching klines: {str(e)}")


### =========================================
### Paper Trading & TWAP
### =========================================

@app.post("/orders/twap", response_model=TWAPOrderResponse)
async def create_twap_order(
        request: Request,
        twap_request: TWAPOrderRequest,
        background_tasks: BackgroundTasks,
        api_key: Optional[str] = Security(token_id_header)
):
    """
    Submits a TWAP (Time-Weighted Average Price) order.
    Requires a token_id (ex-api_key).
    """
    # Rate limit check
    rate_limiter, client_type = await get_rate_limiter(request, api_key)
    if not rate_limiter.try_consume():
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    exch = twap_request.exchange.lower()
    if exch not in ["binance", "kraken"]:
        raise HTTPException(status_code=400, detail="Exchange not supported")
    # Retrieve the list of pairs on the exchange
    if exch == "binance":
        pairs = await get_binance_trading_pairs()
    else:
        pairs = await get_kraken_trading_pairs()

    if twap_request.pair.upper() not in [p.upper() for p in pairs]:
        raise HTTPException(status_code=400, detail="Pair not supported on this exchange")

    # Generate an order ID
    order_id = str(uuid.uuid4())
    # Create the internal structure
    now = datetime.utcnow()
    twap_orders[order_id] = {
        "request": twap_request,
        "status": {
            "order_id": order_id,
            "exchange": twap_request.exchange,
            "pair": twap_request.pair,
            "side": twap_request.side,
            "total_quantity": twap_request.total_quantity,
            "executed_quantity": 0.0,
            "limit_price": twap_request.limit_price,
            "duration_seconds": twap_request.duration_seconds,
            "slices": twap_request.slices,
            "slices_executed": 0,
            "is_completed": False,
            "created_at": now,
            "updated_at": now
        }
    }

    # Launch the background task that handles TWAP execution
    background_tasks.add_task(execute_twap_order, order_id)

    return TWAPOrderResponse(order_id=order_id, message="TWAP order created")


@app.get("/orders/{order_id}", response_model=TWAPOrderStatus)
async def get_twap_order_status(order_id: str):
    """
    Retrieve the status of an existing TWAP order.
    """
    if order_id not in twap_orders:
        raise HTTPException(status_code=404, detail="Order not found")

    return TWAPOrderStatus(**twap_orders[order_id]["status"])


@app.get("/orders", tags=["authenticated"])
async def list_orders(api_key: Optional[str] = Security(token_id_header)):
    """
    List all TWAP orders (open or closed).
    Optionally, filter by token (here, we return all orders as the token is not stored in the order).
    """
    return [order_data["status"] for order_data in twap_orders.values()]


async def execute_twap_order(order_id: str):
    """
    Handles the step-by-step execution of the TWAP order over the specified duration.
    Splits the quantity into 'slices' and attempts to execute at each interval.
    """
    # Retrieve the order object
    order_data = twap_orders[order_id]
    twap_req: TWAPOrderRequest = order_data["request"]
    status = order_data["status"]

    interval = twap_req.duration_seconds / twap_req.slices
    quantity_per_slice = twap_req.total_quantity / twap_req.slices

    for i in range(twap_req.slices):
        if status["is_completed"]:
            break

        await asyncio.sleep(interval)  # Wait for the duration of a 'slice'

        # Check the current price in the order book
        # (Simplification: take the best ask for BUY, the best bid for SELL)
        ob = order_books[twap_req.exchange].get(twap_req.pair, {})
        best_ask = float(ob["asks"][0][0]) if ob["asks"] else None
        best_bid = float(ob["bids"][0][0]) if ob["bids"] else None

        can_execute = False
        executed_amount = 0.0

        if twap_req.side == OrderSide.BUY and best_ask is not None:
            # Check that the ask <= limit_price
            if best_ask <= twap_req.limit_price:
                can_execute = True
        elif twap_req.side == OrderSide.SELL and best_bid is not None:
            # Check that the bid >= limit_price
            if best_bid >= twap_req.limit_price:
                can_execute = True

        if can_execute:
            executed_amount = quantity_per_slice
            status["executed_quantity"] += executed_amount

        status["slices_executed"] += 1
        status["updated_at"] = datetime.utcnow()

        # Check if we have executed everything
        if status["executed_quantity"] >= twap_req.total_quantity:
            status["is_completed"] = True
            break

    # If we haven't executed everything by the end of the slices, we consider the order completed anyway
    status["is_completed"] = True
    status["updated_at"] = datetime.utcnow()


### =========================================
### Launcher
### =========================================

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
