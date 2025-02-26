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

from fastapi import WebSocket, WebSocketDisconnect, HTTPException
import json

from contextlib import asynccontextmanager

import json
import websockets

# BINANCE_WS_URL = "wss://stream.binance.com:9443/ws/btcusdt@depth5"
BINANCE_WS_URL = "wss://stream.binance.com:9443/ws/btcusdt@depth10"
KRAKEN_WS_URL = "wss://ws.kraken.com"
BASE_REST_SPOT_URL = "https://api.binance.com"
BASE_REST_KRAKEN_URL = "https://api.kraken.com/0/public/OHLC"

BINANCE_PAIRS = ["BTCUSDT", "ETHUSDT"]
KRAKEN_PAIRS = ["XBTUSD", "ETHUSD"]
KRAKEN_WS_PAIRS = ["XBT/USD", "ETH/USD"]

KRAKEN_MAPPING = {
    'XBTUSD': 'XXBTZUSD',
    'ETHUSD': 'XETHZUSD'
}


async def binance_orderbook_updater(pair):
    url = f"wss://stream.binance.com:9443/ws/{pair.lower()}@depth10"
    async with websockets.connect(url) as websocket:
        async for message in websocket:
            data = json.loads(message)
            if "bids" in data and "asks" in data:
                order_books["binance"][pair]["bids"] = data["bids"]
                order_books["binance"][pair]["asks"] = data["asks"]
                print(f"🔄 Mise à jour Binance Order Book {pair} :", json.dumps(order_books["binance"][pair], indent=4))
            # await asyncio.sleep(60)


async def start_binance_orderbooks():
    # Créer une tâche pour chaque paire de Binance
    tasks = [binance_orderbook_updater(pair) for pair in BINANCE_PAIRS]
    await asyncio.gather(*tasks)


async def kraken_orderbook_updater():
    async with websockets.connect(KRAKEN_WS_URL) as websocket:
        subscribe_message = {
            "event": "subscribe",
            "pair": KRAKEN_WS_PAIRS,
            "subscription": {"name": "book", "depth": 10}
        }
        await websocket.send(json.dumps(subscribe_message))

        async for message in websocket:
            print("📩 Kraken Message Reçu:", message)
            data = json.loads(message)

            if isinstance(data, list) and len(data) > 1:
                payload = data[1]  # Payload contenant les ordres
                pair = data[-1]  # Dernier élément = paire ("XBT/USD" ou "ETH/USD")

                # Initialiser l'order book si nécessaire
                if pair not in order_books["kraken"]:
                    order_books["kraken"][pair] = {"bids": [], "asks": []}

                # Mise à jour des bids et asks
                if "b" in payload:
                    order_books["kraken"][pair]["bids"] = payload["b"]
                if "a" in payload:
                    order_books["kraken"][pair]["asks"] = payload["a"]

                print(f"🔄 Mise à jour Kraken Order Book {pair} :", json.dumps(order_books["kraken"][pair], indent=4))
            # await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    loop = asyncio.get_running_loop()
    loop.create_task(start_binance_orderbooks())
    loop.create_task(kraken_orderbook_updater())
    yield  # Attente du shutdown


app = FastAPI(title="Crypto Data & Paper Trading API", lifespan=lifespan)

# Stocke la paire active pour chaque exchange
active_pair = {
    "binance": None,
    "kraken": None
}
# Stocke les Order Books
order_books = {
    "binance": {},
    "kraken": {}
}

@app.post("/set_active_pair/{exchange}/{pair}")
async def set_active_pair(exchange: str, pair: str):
    """Définit la paire active pour l'exchange spécifié."""
    exchange = exchange.lower()
    pair = pair.upper()

    if exchange not in active_pair:
        raise HTTPException(status_code=400, detail="Exchange non supporté")

    active_pair[exchange] = pair
    order_books.setdefault(exchange, {})[pair] = {"bids": [], "asks": []}

    return {"message": f"Paire active pour {exchange} mise à jour à {pair}"}

@app.websocket("/ws/orderbook/{exchange}")
async def websocket_orderbook(websocket: WebSocket, exchange: str):
    """WebSocket qui envoie l'Order Book de la paire active uniquement."""
    await websocket.accept()
    exchange = exchange.lower()

    try:
        while True:
            pair = active_pair.get(exchange)
            ob = order_books.get(exchange, {}).get(pair, {"bids": [], "asks": []})
            await websocket.send_text(json.dumps(ob))
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass

class APIClient:
    def __init__(self, base_url="http://localhost:8000", api_key=None):
        self.base_url = base_url
        self.api_key = api_key

    def get_headers(self):
        return {"X-Token-ID": self.api_key} if self.api_key else {}

    def set_active_pair(self, exchange: str, pair: str):
        """Définit la paire active pour l'exchange donné."""
        try:
            url = f"{self.base_url}/set_active_pair/{exchange}/{pair}"
            response = requests.post(url, headers=self.get_headers())
            return response.json() if response.status_code == 200 else None
        except Exception as e:
            print(f"Exception in set_active_pair: {e}")
            return None

    async def websocket_orderbook(self, exchange: str):
        """Connecte au WebSocket pour suivre l'order book de la paire active."""
        uri = f"ws://localhost:8000/ws/orderbook/{exchange}"
        async with websockets.connect(uri) as websocket:
            print(f"Connected to WebSocket order book stream for {exchange}.")
            try:
                while True:
                    data = await websocket.recv()
                    print(f"Order Book Update for {exchange}: {data}")
            except websockets.exceptions.ConnectionClosed:
                print("WebSocket connection closed.")


# Stockage des WebSockets clients
clients = []


def get_top_10_levels(order_book):
    """ Récupère les 10 meilleurs niveaux (bids & asks) """
    return {
        "bids": order_book["bids"][:10],
        "asks": order_book["asks"][:10]
    }


@app.websocket("/ws/orderbook")
async def websocket_orderbook(websocket: WebSocket):
    """ WebSocket permettant aux clients de recevoir l'order book en temps réel """
    await websocket.accept()
    clients.append(websocket)
    try:
        while True:
            # Extraire les données de chaque exchange/pair
            snapshot = {}
            for exchange, pairs in order_books.items():
                snapshot[exchange] = {}
                for pair, order_book in pairs.items():
                    if "bids" in order_book and "asks" in order_book and order_book["bids"] and order_book["asks"]:
                        snapshot[exchange][pair] = {
                            "bids": order_book["bids"],
                            "asks": order_book["asks"]
                        }

            # Envoyer seulement si des données sont disponibles
            if any(pairs for pairs in snapshot.values()):
                print("✅ WebSocket - Envoi de l'Order Book actualisé")
                await websocket.send_text(json.dumps(snapshot))
            else:
                print("⚠️ Pas de données d'order book à envoyer")

            await asyncio.sleep(1)  # Envoi toutes les secondes
    except WebSocketDisconnect:
        clients.remove(websocket)


### =========================
### Enums et modèles Pydantic
### =========================

class ClientType(str, Enum):
    ANONYMOUS = "anonymous"
    BASIC = "basic_client"
    PREMIUM = "premium_client"


# TWAP side / type d'ordre
class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


# Modèle d'ordre pour le paper-trading
class TWAPOrderRequest(BaseModel):
    exchange: str
    pair: str
    side: OrderSide
    total_quantity: float
    limit_price: float
    duration_seconds: int
    slices: int  # nombre de 'tranches' dans la durée


# Réponse de création d'ordre
class TWAPOrderResponse(BaseModel):
    order_id: str
    message: str


# Modèle d'information d'ordre
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


# Réponse pour data
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
### Gestion du Rate Limiting
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
### Données en mémoire pour Order Books
### & Paper Trading
### =========================================

# Liste "hardcodée" des échanges et paires supportées
# (à ajuster selon nos besoins)
SUPPORTED_EXCHANGES = {
    "binance": BINANCE_PAIRS,
    "kraken": KRAKEN_PAIRS
}

# Stockage en mémoire de l'order book
# Structure: order_books[exchange][pair] = {"bids": [...], "asks": [...]}
# ✅ Vérifier que order_books est bien défini avant toute utilisation
order_books: Dict[str, Dict[str, Dict[str, List]]] = {
    "binance": {"BTCUSDT": {"bids": [], "asks": []}},
    "kraken": {"XBT/USD": {"bids": [], "asks": []}}
}

for ex in SUPPORTED_EXCHANGES:
    for pair in SUPPORTED_EXCHANGES[ex]:
        order_books[ex][pair] = {
            "bids": [],
            "asks": []
        }

# Stockage en mémoire des ordres TWAP
# {order_id: {"request": TWAPOrderRequest, "status": {...}}}
twap_orders: Dict[str, Dict] = {}


### =========================================
### Fonctions d’authentification & rate limiting
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
    api_key = str(uuid.uuid4())[:8]  # Clé API courte
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
        "message": "Voici des données limitées",
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
    Retourne la liste des exchanges supportés.
    """
    return list(SUPPORTED_EXCHANGES.keys())


@app.get("/exchanges/{exchange}/pairs", tags=["public"])
async def list_pairs(exchange: str):
    """
    Retourne la liste des paires disponibles sur un exchange donné.
    """
    if exchange not in SUPPORTED_EXCHANGES:
        raise HTTPException(status_code=404, detail="Exchange not supported")
    return SUPPORTED_EXCHANGES[exchange]


# Simulation d'un endpoint pour récupérer des chandeliers
# Ici, on n'a pas implémenté la logique de vrai stockage historique,
# donc on retourne des données fictives pour illustrer
# @app.get("/candlesticks", tags=["public"])
async def get_kraken_klines(session: aiohttp.ClientSession, symbol: str, interval: str = "1m", limit: int = 5,
                            start_time: Optional[datetime] = None):
    """
    Fetch historical kline data from Kraken and format it to match the Binance format.
    """
    kraken_symbol = symbol.upper()

    if kraken_symbol not in KRAKEN_MAPPING:
        raise HTTPException(status_code=400, detail=f"Unsupported symbol for Kraken: {kraken_symbol}")

    # Convert Binance interval format to Kraken interval format
    interval_mapping = {
        "1m": "1",
        "5m": "5",
        "15m": "15",
        "30m": "30",
        "1h": "60",
        "3h": "180",
        "6h": "360",
        "12h": "720",
        "1d": "1440",
        "3d": "4320",
        "1w": "10080"
    }

    kraken_interval = interval_mapping.get(interval, "60")  # Default to 1h if interval not found

    params = {
        'pair': KRAKEN_MAPPING[kraken_symbol],
        'interval': kraken_interval,
        'count': limit
    }

    if start_time:
        params['since'] = int(start_time.timestamp())

    try:
        async with session.get(BASE_REST_KRAKEN_URL, params=params) as response:
            if response.status == 200:
                data = await response.json()

                if "result" in data and KRAKEN_MAPPING[kraken_symbol] in data["result"]:
                    candles = [
                        [
                            int(k[0]),  # timestamp
                            float(k[1]),  # open
                            float(k[2]),  # high
                            float(k[3]),  # low
                            float(k[4]),  # close
                            float(k[6])  # volume (Kraken uses k[6] instead of k[5])
                        ] for k in data["result"][KRAKEN_MAPPING[kraken_symbol]]
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
    if exchange.lower() not in SUPPORTED_EXCHANGES:
        raise HTTPException(status_code=404, detail=f"Exchange not supported: {exchange}")

    # Check if the symbol is supported for this exchange
    if symbol.upper() not in SUPPORTED_EXCHANGES[exchange.lower()]:
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
    Soumet un ordre TWAP (Time-Weighted Average Price).
    Nécessite un token_id (ex-api_key).
    """
    # Vérif du rate limit
    rate_limiter, client_type = await get_rate_limiter(request, api_key)
    if not rate_limiter.try_consume():
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    # Vérif de l'exchange et pair
    if twap_request.exchange not in SUPPORTED_EXCHANGES:
        raise HTTPException(status_code=400, detail="Exchange not supported")
    if twap_request.pair not in SUPPORTED_EXCHANGES[twap_request.exchange]:
        raise HTTPException(status_code=400, detail="Pair not supported on this exchange")

    # Génération d'un ID d'ordre
    order_id = str(uuid.uuid4())
    # Création de la structure interne
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

    # Lancement de la tâche en arrière-plan qui gère l'exécution TWAP
    background_tasks.add_task(execute_twap_order, order_id)

    return TWAPOrderResponse(order_id=order_id, message="TWAP order created")


@app.get("/orders/{order_id}", response_model=TWAPOrderStatus)
async def get_twap_order_status(order_id: str):
    """
    Récupérer le statut d'un ordre TWAP existant.
    """
    if order_id not in twap_orders:
        raise HTTPException(status_code=404, detail="Order not found")

    return TWAPOrderStatus(**twap_orders[order_id]["status"])


async def execute_twap_order(order_id: str):
    """
    Gère l'exécution pas-à-pas de l'ordre TWAP sur la durée spécifiée.
    Divise la quantité en 'slices' et tente d'exécuter à chaque intervalle.
    """
    # On récupère l'objet d'ordre
    order_data = twap_orders[order_id]
    twap_req: TWAPOrderRequest = order_data["request"]
    status = order_data["status"]

    interval = twap_req.duration_seconds / twap_req.slices
    quantity_per_slice = twap_req.total_quantity / twap_req.slices

    for i in range(twap_req.slices):
        if status["is_completed"]:
            break

        await asyncio.sleep(interval)  # On attend le temps d'un 'slice'

        # On regarde le prix actuel dans l'order book
        # (Simplification: on prend le meilleur ask pour BUY, le meilleur bid pour SELL)
        ob = order_books[twap_req.exchange].get(twap_req.pair, {})
        best_ask = float(ob["asks"][0][0]) if ob["asks"] else None
        best_bid = float(ob["bids"][0][0]) if ob["bids"] else None

        can_execute = False
        executed_amount = 0.0

        if twap_req.side == OrderSide.BUY and best_ask is not None:
            # On vérifie que le ask <= limit_price
            if best_ask <= twap_req.limit_price:
                can_execute = True
        elif twap_req.side == OrderSide.SELL and best_bid is not None:
            # On vérifie que le bid >= limit_price
            if best_bid >= twap_req.limit_price:
                can_execute = True

        if can_execute:
            executed_amount = quantity_per_slice
            status["executed_quantity"] += executed_amount

        status["slices_executed"] += 1
        status["updated_at"] = datetime.utcnow()

        # Vérifier si on a tout exécuté
        if status["executed_quantity"] >= twap_req.total_quantity:
            status["is_completed"] = True
            break

    # Si on n'a pas tout exécuté au terme des slices, on considère l'ordre terminé malgré tout
    status["is_completed"] = True
    status["updated_at"] = datetime.utcnow()


### =========================================
### Lanceur
### =========================================

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
