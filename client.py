import requests
import asyncio
import websockets
import json
import queue

orderbook_queue = queue.Queue()


class APIClient:
    def __init__(self, base_url: str = "http://localhost:8000", api_key: str = None):
        self.base_url = base_url
        self.api_key = api_key

    def get_headers(self):
        """Get headers with API key for authenticated requests"""
        return {"X-Token-ID": self.api_key} if self.api_key else {}

    def set_active_pair(self, exchange: str, pair: str):
        """Définit la paire active pour l'exchange en appelant l'endpoint dédié."""
        try:
            url = f"{self.base_url}/set_active_pair/{exchange}/{pair}"
            response = requests.post(url, headers=self.get_headers())
            if response.status_code == 200:
                return response.json()
            else:
                print(f"Error setting active pair: {response.status_code}, {response.text}")
                return None
        except Exception as e:
            print(f"Exception in set_active_pair: {e}")
            return None

    def check_status(self):
        """Check if the API is running"""
        try:
            response = requests.get(f"{self.base_url}/status")
            return response.json()
        except Exception as e:
            print(f"Error checking status: {e}")
            return None

    def list_exchanges(self):
        """Get list of supported exchanges"""
        try:
            response = requests.get(f"{self.base_url}/exchanges")
            return response.json()
        except Exception as e:
            print(f"Error fetching exchanges: {e}")
            return None

    def list_pairs(self, exchange: str):
        """Get trading pairs available on a given exchange"""
        try:
            response = requests.get(f"{self.base_url}/exchanges/{exchange}/pairs")
            return response.json()
        except Exception as e:
            print(f"Error fetching pairs for {exchange}: {e}")
            return None

    def get_klines(self, exchange: str, symbol: str, interval: str = "1m", limit: int = 5):
        """Fetch candlestick data for a given exchange and symbol"""
        try:
            response = requests.get(f"{self.base_url}/klines/{exchange}/{symbol}?interval={interval}&limit={limit}")
            return response.json()
        except Exception as e:
            print(f"Error fetching kline data: {e}")
            return None

    def get_orderbook(self, exchange: str, symbol: str):
        """Fetch order book data using REST API"""
        try:
            response = requests.get(f"{self.base_url}/orderbook/{exchange}/{symbol}")
            if response.status_code == 200:
                return response.json()
            else:
                print(f"Error fetching order book: Status {response.status_code}")
                return None
        except Exception as e:
            print(f"Error fetching order book: {e}")
            return None

    def get_data(self):
        """Fetch protected data that requires authentication"""
        if not self.api_key:
            print("No API key provided!")
            return None

        try:
            response = requests.get(f"{self.base_url}/data", headers=self.get_headers())

            if response.status_code == 403:
                print("Invalid API key!")
                return None

            return response.json()
        except Exception as e:
            print(f"Error fetching data: {e}")
            return None

    def submit_twap_order(self, exchange: str, pair: str, side: str, quantity: float, limit_price: float, duration: int,
                          slices: int):
        """Submit a TWAP order to the server"""
        if not self.api_key:
            print("No API key provided!")
            return None

        order_data = {
            "exchange": exchange,
            "pair": pair,
            "side": side,
            "total_quantity": quantity,
            "limit_price": limit_price,
            "duration_seconds": duration,
            "slices": slices
        }

        try:
            response = requests.post(f"{self.base_url}/orders/twap", json=order_data, headers=self.get_headers())

            if response.status_code != 200:
                print(f"Error: {response.status_code}, Details: {response.text}")
                return None

            return response.json()
        except Exception as e:
            print(f"Error submitting TWAP order: {e}")
            return None

    def get_twap_order_status(self, order_id: str):
        """Retrieve the status of a specific TWAP order"""
        try:
            response = requests.get(f"{self.base_url}/orders/{order_id}", headers=self.get_headers())
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Request error fetching order status: {e}")
            return None
        except Exception as e:
            print(f"Unexpected error fetching order status: {e}")
            return None


async def websocket_orderbook():
    """Connect to the WebSocket and listen for order book updates"""
    url = "ws://localhost:8000/ws/orderbook"
    async with websockets.connect(url) as websocket:
        print("Connected to WebSocket order book stream.")
        try:
            while True:
                data = await websocket.recv()
                order_book = json.loads(data)
                print(f"📩 Order Book update received:\n{json.dumps(order_book, indent=4)}")
                orderbook_queue.put(order_book)
        except websockets.exceptions.ConnectionClosed:
            print("WebSocket connection closed.")
            await asyncio.sleep(5)
            await websocket_orderbook()


def get_order_book(self, exchange: str, pair: str):
    """Fetch the order book via the REST API if the WebSocket is not working."""
    try:
        response = requests.get(f"{self.base_url}/orderbook/{exchange}/{pair}")

        if response.status_code == 200:
            return response.json()
        else:
            print(f"❌ Error {response.status_code} while retrieving the order book.")
            return None

    except Exception as e:
        print(f"❌ Error during the order book request: {e}")
        return None
