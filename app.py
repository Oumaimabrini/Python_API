import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import asyncio
import websockets
import json
import time
from datetime import datetime, timedelta
import threading
import queue
import requests

from streamlit_autorefresh import st_autorefresh

# Configuration de la page
st.set_page_config(page_title="OrderBook Stream", layout="wide")
st_autorefresh(interval=60000, key="orderbook_refresh")  # Rafraîchit toutes les 60 secondes

# File pour les mises à jour de l'order book
orderbook_queue = queue.Queue()


# Import APIClient depuis notre propre fichier
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

    def get_orderbook(self, exchange: str, symbol: str):
        """Fetch orderbook data using REST API"""
        try:
            response = requests.get(f"{self.base_url}/orderbook/{exchange}/{symbol}")
            if response.status_code == 200:
                return response.json()
            else:
                print(f"Error fetching orderbook: Status {response.status_code}")
                return None
        except Exception as e:
            print(f"Error fetching orderbook: {e}")
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


# Initialisation de la session
if 'client' not in st.session_state:
    st.session_state.client = APIClient(base_url="http://localhost:8000")
if 'orderbook_data' not in st.session_state:
    st.session_state.orderbook_data = {}
if 'orders' not in st.session_state:
    st.session_state.orders = []
if 'selected_exchange' not in st.session_state:
    st.session_state.selected_exchange = None
if 'selected_pair' not in st.session_state:
    st.session_state.selected_pair = None

st.title("Cryptocurrency Trading Dashboard")

# Configuration dans la sidebar
with st.sidebar:
    st.header("Configuration")
    api_key = st.text_input("API Key", type="password")
    if api_key:
        st.session_state.client.api_key = api_key

    # Sélection d'exchange et de paire
    exchanges = st.session_state.client.list_exchanges()
    if exchanges:
        selected_exchange = st.selectbox("Select Exchange", exchanges)
        if selected_exchange != st.session_state.selected_exchange:
            st.session_state.selected_exchange = selected_exchange
            pairs = st.session_state.client.list_pairs(selected_exchange)
            st.session_state.pairs = pairs

        if hasattr(st.session_state, 'pairs'):
            selected_pair = st.selectbox("Select Trading Pair", st.session_state.pairs)
            if selected_pair != st.session_state.selected_pair:
                st.session_state.selected_pair = selected_pair

    # Sélection de l'intervalle pour les kline
    intervals = ["1m", "5m", "15m", "30m", "1h", "6h", "12h", "1d", "3d",
                 "1w"] if selected_exchange == "binance" else ["1m", "5m",
                                                               "15m", "30m",
                                                               "1h", "1d",
                                                               "1w"]
    selected_interval = st.selectbox("Select Kline Interval", intervals, index=0)
    st.session_state.selected_interval = selected_interval

# Définition des onglets de l'interface
tab1, tab2, tab3 = st.tabs(["Market Data", "TWAP Trading", "Order History"])

# Onglet Market Data
with tab1:
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Order Book")

        # Tentative de mise à jour de l'order book depuis la file
        try:
            if not orderbook_queue.empty():
                new_data = orderbook_queue.get_nowait()
                st.session_state.orderbook_data = new_data
                st.info(f"🔍 Données reçues: {len(new_data)} exchanges")
            else:
                # Fallback sur la méthode REST si le WebSocket ne renvoie rien
                if st.session_state.selected_exchange and st.session_state.selected_pair:
                    ob_data = st.session_state.client.get_orderbook(st.session_state.selected_exchange,
                                                                    st.session_state.selected_pair)
                    if ob_data:
                        if st.session_state.selected_exchange not in st.session_state.orderbook_data:
                            st.session_state.orderbook_data[st.session_state.selected_exchange] = {}
                        st.session_state.orderbook_data[st.session_state.selected_exchange][
                            st.session_state.selected_pair] = ob_data
                        # st.info("📊 Order book récupéré via REST API")
                    else:
                        st.warning("🚨 Impossible de récupérer l'order book")
                else:
                    st.warning("🚨 Veuillez sélectionner un exchange et une paire")
        except Exception as e:
            st.error(f"Erreur lors de la mise à jour de l'order book: {str(e)}")


        # Fonction de formatage de l'order book
        def format_orderbook(data):
            if not data:
                return pd.DataFrame()
            df = pd.DataFrame(data, columns=['Price', 'Size'])
            df['Price'] = pd.to_numeric(df['Price'])
            df['Size'] = pd.to_numeric(df['Size'])
            return df


        orderbook_placeholder = st.empty()
        if st.session_state.selected_exchange in st.session_state.orderbook_data and st.session_state.selected_pair in st.session_state.orderbook_data.get(
                st.session_state.selected_exchange, {}):
            ob_data = st.session_state.orderbook_data[st.session_state.selected_exchange][
                st.session_state.selected_pair]
            bids_df = format_orderbook(ob_data.get('bids', []))
            asks_df = format_orderbook(ob_data.get('asks', []))

            if not bids_df.empty and not asks_df.empty:

                bids_df = bids_df.sort_values(by='Price', ascending=False)
                asks_df = asks_df.sort_values(by='Price', ascending=True)

                # Formater les prix et volumes avec plus de décimales pour les cryptos
                bids_df['Price'] = bids_df['Price'].apply(lambda x: f"{x:.2f}")
                bids_df['Size'] = bids_df['Size'].apply(lambda x: f"{x:.5f}")
                asks_df['Price'] = asks_df['Price'].apply(lambda x: f"{x:.2f}")
                asks_df['Size'] = asks_df['Size'].apply(lambda x: f"{x:.5f}")

                # Renommer les colonnes pour plus de clarté
                bids_df = bids_df.rename(columns={"Price": "Bid Price", "Size": "Volume"})
                asks_df = asks_df.rename(columns={"Price": "Ask Price", "Size": "Volume"})

                # TODO voir comment retirer l'index dataframe donc on passe par html ppur le moment

                # st.markdown("<h3 style='color: green;'>Bids 🟢</h3>", unsafe_allow_html=True)
                # st.dataframe(
                #   bids_df[['Bid Price', 'Volume']].style.hide(axis="index"),  # Afficher uniquement les colonnes souhaitées
                #  height=(10 + 1) * 35 + 3
                # )

                # st.markdown("<h3 style='color: red;'>Asks 🔴</h3>", unsafe_allow_html=True)
                # st.dataframe(
                #   asks_df[['Ask Price', 'Volume']].style.hide(axis="index"),  # Afficher uniquement les colonnes souhaitées
                #  height=(10 + 1) * 35 + 3
                # )

                bids_html = bids_df[['Bid Price', 'Volume']].to_html(index=False)
                asks_html = asks_df[['Ask Price', 'Volume']].to_html(index=False)

                # Affichage des Bids sans index
                st.markdown("<h3 style='color: green;'>Bids 🟢</h3>", unsafe_allow_html=True)
                st.markdown(bids_html, unsafe_allow_html=True)

                # Affichage des Asks sans index
                st.markdown("<h3 style='color: red;'>Asks 🔴</h3>", unsafe_allow_html=True)
                st.markdown(asks_html, unsafe_allow_html=True)
            else:
                st.warning("⚠️ Order book data incomplete.")
        else:
            st.warning("⚠️ No order book data available.")

    with col2:
        st.subheader("Price Chart")
        if st.session_state.selected_exchange and st.session_state.selected_pair:
            try:
                kline_data = st.session_state.client.get_klines(
                    st.session_state.selected_exchange,
                    st.session_state.selected_pair,
                    interval=st.session_state.selected_interval,
                    limit=100
                )
                if kline_data and 'candles' in kline_data:
                    df = pd.DataFrame(kline_data['candles'])
                    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='s')
                    fig = go.Figure(data=[go.Candlestick(
                        x=df['timestamp'],
                        open=df['open'],
                        high=df['high'],
                        low=df['low'],
                        close=df['close']
                    )])
                    fig.update_layout(
                        title=f"{st.session_state.selected_pair} Price Chart ({st.session_state.selected_interval})",
                        yaxis_title='Price',
                        xaxis_title='Time',
                        xaxis_rangeslider_visible=False
                    )
                    st.plotly_chart(fig, use_container_width=True)
                else:
                    st.warning(
                        f"No kline data available for {st.session_state.selected_pair} on {st.session_state.selected_exchange}")
                    st.write("Debug info:", kline_data)
            except Exception as e:
                st.error(f"Error fetching kline data: {str(e)}")

# Onglet TWAP Trading
with tab2:
    st.subheader("Create TWAP Order")
    if not api_key:
        st.warning("Please enter your API key in the sidebar to trade")
    else:
        col1, col2 = st.columns(2)
        with col1:
            side = st.selectbox("Order Side", ["buy", "sell"])
            quantity = st.number_input("Total Quantity", min_value=0.0, value=1.0, step=0.1)
            limit_price = st.number_input("Limit Price", min_value=0.0, value=0.0, step=100.0)
        with col2:
            duration = st.number_input("Duration (seconds)", min_value=60, value=300, step=60)
            slices = st.number_input("Number of Slices", min_value=2, value=5, step=1)
        if st.button("Submit TWAP Order"):
            try:
                response = st.session_state.client.submit_twap_order(
                    exchange=st.session_state.selected_exchange,
                    pair=st.session_state.selected_pair,
                    side=side,
                    quantity=quantity,
                    limit_price=limit_price,
                    duration=duration,
                    slices=slices
                )
                if response:
                    st.success(f"TWAP order submitted successfully! Order ID: {response['order_id']}")
                    st.session_state.orders.append(response)
                else:
                    st.error("Failed to submit TWAP order")
            except Exception as e:
                st.error(f"Error submitting order: {str(e)}")

# Onglet Order History
with tab3:
    st.subheader("Order History")
    if st.session_state.orders:
        orders_df = pd.DataFrame(st.session_state.orders)
        for i, order in orders_df.iterrows():
            status = st.session_state.client.get_twap_order_status(order['order_id'])
            if status:
                progress = status['executed_quantity'] / status['total_quantity'] * 100
                st.progress(progress)
                st.write(f"Order {order['order_id']}: {progress:.1f}% executed")
                status_df = pd.DataFrame([status])
                st.dataframe(status_df)
    else:
        st.info("No orders found")


# Fonction asynchrone pour mettre à jour l'order book via WebSocket
async def update_orderbook():
    """Récupération des données d'order book via WebSocket"""
    uri = "ws://localhost:8000/ws/orderbook"  # URL unifiée
    try:
        # Affichage pour le debug
        print(f"🔌 Connecting to WebSocket at {uri}...")
        async with websockets.connect(uri) as websocket:
            print("✅ Connected to WebSocket order book stream!")
            while True:
                try:
                    data = await websocket.recv()
                    parsed_data = json.loads(data)
                    # Affichage réduit pour éviter de surcharger la console
                    print(f"📩 Order Book Update received: {len(parsed_data)} exchanges")
                    # Mettre les données dans la file d'attente
                    orderbook_queue.put(parsed_data)
                except json.JSONDecodeError as e:
                    print(f"❌ Error decoding JSON: {e}")
                    continue
                await asyncio.sleep(1)  # Petit délai pour éviter de surcharger
    except (websockets.exceptions.ConnectionClosed, asyncio.CancelledError) as e:
        print(f"WebSocket connection closed or cancelled: {e}. Reconnecting in 5 seconds...")
        await asyncio.sleep(5)
        await update_orderbook()  # Reconnexion
    except Exception as e:
        print(f"❌ General WebSocket error: {e}. Reconnecting in 5 seconds...")
        await asyncio.sleep(5)
        await update_orderbook()  # Reconnexion


# Démarrage du thread pour mettre à jour l'order book
def start_orderbook_updater():
    # Configuration du nouvel event loop pour le thread
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    print("🚀 Starting WebSocket connection to the server...")
    try:
        loop.run_until_complete(update_orderbook())
    except Exception as e:
        print(f"❌ Fatal error in WebSocket thread: {e}")
        # Tentative de récupération via la méthode REST
        print("⚙️ Switching to REST API for orderbook data")


# Démarrage du thread WebSocket avec gestion d'erreur
try:
    websocket_thread = threading.Thread(target=start_orderbook_updater, daemon=True)
    websocket_thread.start()
    print("✅ WebSocket updater thread started")
except Exception as e:
    print(f"❌ Could not start WebSocket thread: {e}")
