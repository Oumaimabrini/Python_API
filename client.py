import requests
import asyncio
import websockets
import json
from datetime import datetime


class APIClient:
    def __init__(self, base_url: str = "http://localhost:8000", api_key: str = None):
        self.base_url = base_url
        self.api_key = api_key

    def get_headers(self):
        """Get headers with API key for authenticated requests"""
        return {"X-Token-ID": self.api_key} if self.api_key else {}

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
            response = requests.get(f"{self.base_url}/orders/{order_id}")
            return response.json()
        except Exception as e:
            print(f"Error fetching order status: {e}")
            return None


async def websocket_orderbook():
    """Connect to the WebSocket and listen for order book updates"""
    uri = "ws://localhost:8000/ws/orderbook"

    async with websockets.connect(uri) as websocket:
        print("Connected to WebSocket order book stream.")
        try:
            while True:
                data = await websocket.recv()
                order_book = json.loads(data)
                print(f" Order Book Update: {order_book}")
        except websockets.exceptions.ConnectionClosed:
            print("WebSocket connection closed.")


def test_api():
    """Run tests on the API client"""
    client = APIClient(api_key="your_api_key_here")

    print("\n✅ Checking API status...")
    status = client.check_status()
    print(f"Status: {status}")

    print("\n✅ Listing exchanges...")
    exchanges = client.list_exchanges()
    print(f"Exchanges: {exchanges}")

    if exchanges:
        exchange = exchanges[0]  # Select the first available exchange
        print(f"\n✅ Listing pairs for {exchange}...")
        pairs = client.list_pairs(exchange)
        print(f"Pairs: {pairs}")

        if pairs:
            symbol = pairs[0]
            print(f"\n✅ Fetching kline data for {exchange} - {symbol}...")
            kline_data = client.get_klines(exchange, symbol)
            print(f"Kline data: {kline_data}")

    print("\n✅ Submitting a TWAP order...")
    twap_response = client.submit_twap_order(
        exchange="binance",
        pair="BTCUSDT",
        side="buy",
        quantity=0.5,
        limit_price=45000.0,
        duration=60,
        slices=5
    )
    print(f"TWAP Order Response: {twap_response}")

    if twap_response:
        order_id = twap_response.get("order_id")
        print("\n✅ Checking TWAP order status...")
        order_status = client.get_twap_order_status(order_id)
        print(f"Order Status: {order_status}")

    print("\n✅ Starting WebSocket listener for order book updates...")
    asyncio.run(websocket_orderbook())


if __name__ == "__main__":
    test_api()
