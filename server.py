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

from fastapi import WebSocket, WebSocketDisconnect

from contextlib import asynccontextmanager

import json
import websockets

BINANCE_WS_URL = "wss://stream.binance.com:9443/ws/btcusdt@depth5"
KRAKEN_WS_URL = "wss://ws.kraken.com"
BASE_REST_SPOT_URL = "https://api.binance.com"

# Durée du cache en secondes (par exemple 1 heure)
CACHE_DURATION = 3600

# Variables globales de cache
cached_trading_pairs = {
    "binance": {"timestamp": 0, "data": []},
    "kraken": {"timestamp": 0, "data": []}
}


async def get_binance_trading_pairs():
    now = time.time()
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
async def fetch_trading_pairs():
    """ Initialise SUPPORTED_EXCHANGES avec toutes les paires disponibles """
    binance_pairs = await get_binance_trading_pairs()
    kraken_pairs = await get_kraken_trading_pairs()

    return {
        "binance": binance_pairs,
        "kraken": kraken_pairs
    }

SUPPORTED_EXCHANGES = asyncio.run(fetch_trading_pairs())

#Initialisation des orders books, active_pair considere les paires active à mettre à jours
active_pair = {
    "binance": None,  # Exemple : "BTCUSDT"
    "kraken": None    # Exemple : "XBT/USD"
}

order_books = {
    "binance": {},
    "kraken": {}
}


'''async def binance_orderbook_updater():
    """Exemple simplifié de récupération d'un mini order book sur Binance."""
    async with websockets.connect(BINANCE_WS_URL) as websocket:
        async for message in websocket:
            data = json.loads(message)
            # data = { 'lastUpdateId':..., 'bids': [...], 'asks': [...] }
            # On stocke seulement pour la paire BTCUSDT en l'occurrence:
            try:
                if "bids" in data and "asks" in data:
                    # Standardisation dans le format interne
                    order_books["binance"]["BTCUSDT"]["bids"] = data["bids"]
                    order_books["binance"]["BTCUSDT"]["asks"] = data["asks"]
            except Exception as e:
                print(f"Erreur maj orderbook binance: {e}")'''


async def binance_orderbook_updater():
    # Attendre que la paire active soit définie pour Binance
    while active_pair["binance"] is None:
        await asyncio.sleep(3)
    pair = active_pair["binance"]
    ws_url = f"wss://stream.binance.com:9443/ws/{pair.lower()}@depth5"

    while True:
        try:
            async with websockets.connect(ws_url) as websocket:
                async for message in websocket:
                    data = json.loads(message)

                    # Si Binance renvoie un message d'erreur (ex: trop de requêtes)
                    if "code" in data:
                        print("Erreur Binance:", data)
                        # On attend un moment avant de tenter de se reconnecter
                        await asyncio.sleep(5)
                        break  # Sort de la boucle for pour relancer la connexion

                    symbol = data.get("s", "").upper()  # Récupère le symbole, ex: "BTCUSDT"
                    if symbol == pair:
                        order_books["binance"].setdefault(pair, {"bids": [], "asks": []})
                        order_books["binance"][pair]["bids"] = data.get("bids", [])[:10]
                        order_books["binance"][pair]["asks"] = data.get("asks", [])[:10]
        except Exception as e:
            print("Exception dans binance_orderbook_updater:", e)
            await asyncio.sleep(5)


'''async def kraken_orderbook_updater():
    """
    Exemple (très) simplifié de récupération de l'order book sur Kraken.
    Vraiment très basique: on s'abonne à XBT/USD.
    """
    async with websockets.connect(KRAKEN_WS_URL) as websocket:
        # Exemple de subscription au book pour XBT/USD
        subscribe_message = {
            "event": "subscribe",
            "pair": ["XBT/USD"],
            "subscription": {"name": "book", "depth": 5}
        }
        await websocket.send(json.dumps(subscribe_message))

        async for message in websocket:
            data = json.loads(message)
            # Kraken renvoie des messages sous forme de liste
            # ou d'event "system"
            if isinstance(data, list) and len(data) > 1:
                # On essaie d'extraire "bids" et "asks" si dispo
                # Souvent, c'est data[1] qui contient le payload
                payload = data[1]
                # payload peut contenir "b" et "a"
                # e.g. {'b': [['12345.1', '1.234', '1234567890']], 'a': [...], ...}
                try:
                    if "b" in payload:
                        order_books["kraken"]["XBT/USD"]["bids"] = payload["b"]
                    if "a" in payload:
                        order_books["kraken"]["XBT/USD"]["asks"] = payload["a"]
                except Exception as e:
                    print(f"Erreur parsing kraken orderbook: {e}")'''


async def kraken_orderbook_updater():
    # Attendre que la paire active soit définie pour Kraken
    while active_pair["kraken"] is None:
        await asyncio.sleep(1)
    pair = active_pair["kraken"]

    while True:
        try:
            async with websockets.connect(KRAKEN_WS_URL) as websocket:
                subscribe_message = {
                    "event": "subscribe",
                    "pair": [pair],
                    "subscription": {"name": "book", "depth": 5}
                }
                await websocket.send(json.dumps(subscribe_message))

                async for message in websocket:
                    data = json.loads(message)

                    # Si Kraken renvoie un message d'erreur
                    if isinstance(data, dict) and data.get("event") == "error":
                        print("Erreur Kraken:", data)
                        await asyncio.sleep(5)
                        break  # Relancer la connexion après attente

                    if isinstance(data, list) and len(data) > 1:
                        payload = data[1]
                        received_pair = data[-1]  # La paire est souvent en dernier élément
                        if received_pair == pair:
                            order_books["kraken"].setdefault(pair, {"bids": [], "asks": []})
                            if "b" in payload:
                                order_books["kraken"][pair]["bids"] = payload["b"][:10]
                            if "a" in payload:
                                order_books["kraken"][pair]["asks"] = payload["a"][:10]
        except Exception as e:
            print("Exception dans kraken_orderbook_updater:", e)
            await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    loop = asyncio.get_running_loop()
    loop.create_task(binance_orderbook_updater())
    loop.create_task(kraken_orderbook_updater())
    yield  # Attente du shutdown


app = FastAPI(title="Crypto Data & Paper Trading API", lifespan=lifespan)

# Stockage des WebSockets clients
clients = []


def get_top_10_levels(order_book):
    """ Récupère les 10 meilleurs niveaux (bids & asks) """
    return {
        "bids": order_book["bids"][:10],
        "asks": order_book["asks"][:10]
    }


'''@app.websocket("/ws/orderbook")
async def websocket_orderbook(websocket: WebSocket):
    """ WebSocket permettant aux clients de recevoir l'order book en temps réel """
    await websocket.accept()
    clients.append(websocket)
    try:
        while True:
            # Vérifie si `order_books` est bien défini
            snapshot = {}
            for exchange, pairs in order_books.items():
                snapshot[exchange] = {}
                for pair, order_book in pairs.items():
                    snapshot[exchange][pair] = get_top_10_levels(order_book)

            await websocket.send_text(json.dumps(snapshot))
            await asyncio.sleep(1)  # Envoi toutes les secondes
    except WebSocketDisconnect:
        clients.remove(websocket)
'''

@app.websocket("/ws/orderbook/{exchange}")
async def websocket_orderbook(websocket: WebSocket, exchange: str):
    await websocket.accept()
    exchange = exchange.lower()

    try:
        while True:
            pair = active_pair.get(exchange)
            if pair is None:
                # Si aucune paire active n'est définie, on envoie un message d'erreur
                await websocket.send_text(json.dumps({"error": "Aucune paire active définie pour cet exchange"}))
            else:
                # Récupération de l'order book de la paire active
                ob = order_books.get(exchange, {}).get(pair, {"bids": [], "asks": []})
                await websocket.send_text(json.dumps(ob))
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass


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


@app.post("/set_active_pair/{exchange}/{pair}")
async def set_active_pair(exchange: str, pair: str):
    exchange = exchange.lower()
    pair = pair.upper()

    if exchange not in active_pair:
        raise HTTPException(status_code=400, detail="Exchange non supporté")

    active_pair[exchange] = pair
    # Initialisation de l'order book pour cette paire si ce n'est pas déjà fait
    order_books.setdefault(exchange, {})[pair] = {"bids": [], "asks": []}

    return {"message": f"Paire active pour {exchange} mise à jour à {pair}"}


# Simulation d'un endpoint pour récupérer des chandeliers
# Ici, on n'a pas implémenté la logique de vrai stockage historique,
# donc on retourne des données fictives pour illustrer
# @app.get("/candlesticks", tags=["public"])
async def get_historical_klines(session, symbol: str, interval: str = '1m', start_time: datetime = None,
                                limit: int = 5):
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
                raise HTTPException(status_code=response.status, detail=f"Error fetching klines: {response.status}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Exception in fetching klines: {str(e)}")


@app.get("/klines/{exchange}/{symbol}", tags=["public"])
async def get_klines(exchange: str, symbol: str, interval: str = "1m", limit: int = 5):
    """
    Fetch real candlestick (kline) data from Binance.
    """
    if exchange.lower() not in SUPPORTED_EXCHANGES:
        raise HTTPException(status_code=404, detail="Exchange not supported")
    if symbol.upper() not in SUPPORTED_EXCHANGES[exchange.lower()]:
        raise HTTPException(status_code=404, detail="Symbol not supported on this exchange")

    async with aiohttp.ClientSession() as session:
        candles = await get_historical_klines(session, symbol.upper(), interval, limit=limit)

    return {
        "exchange": exchange.lower(),
        "symbol": symbol.upper(),
        "interval": interval,
        "limit": limit,
        "candles": candles
    }


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
