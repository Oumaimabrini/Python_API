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

from streamlit_autorefresh import st_autorefresh
from client import APIClient

# Rafraîchit la page toutes les 2 secondes pour l'affichage de l'order book
st.set_page_config(page_title="OrderBook Stream", layout="wide")
st_autorefresh(interval=2000, key="orderbook_refresh")

# File pour les mises à jour de l'order book
orderbook_queue = queue.Queue()

# ------------------------------------------------------------------------------
# 1) Initialisation de st.session_state
# ------------------------------------------------------------------------------
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

if 'ws_thread' not in st.session_state:
    st.session_state.ws_thread = None

# ------------------------------------------------------------------------------
# 2) Fonction asynchrone pour consommer le WebSocket du serveur
# ------------------------------------------------------------------------------
async def update_orderbook():
    """Récupération des données d'order book via WebSocket global."""
    uri = "ws://localhost:8000/ws/orderbook"
    while True:
        try:
            async with websockets.connect(uri) as websocket:
                st.write(f"🔌 [DEBUG] Connected to {uri} WebSocket!")
                while True:
                    data = await websocket.recv()
                    parsed_data = json.loads(data)
                    st.write(f"📩 [DEBUG] Données WebSocket reçues : {json.dumps(parsed_data, indent=4)}")
                    if parsed_data:
                        orderbook_queue.put(parsed_data)
                    await asyncio.sleep(1)
        except websockets.exceptions.ConnectionClosed as e:
            st.write(f"⚠️ WebSocket closed: {e}. Reconnecting in 5s...")
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            st.write("🔌 [DEBUG] WebSocket task cancelled.")
            break
        except Exception as e:
            st.write(f"❌ WebSocket error: {e}. Reconnecting in 5s...")
            await asyncio.sleep(5)

# ------------------------------------------------------------------------------
# 3) Thread pour lancer l'event loop asynchrone
# ------------------------------------------------------------------------------
def start_websocket_thread():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(update_orderbook())

# ------------------------------------------------------------------------------
# 4) Lancement conditionnel du thread WebSocket
# ------------------------------------------------------------------------------
def ensure_websocket_thread_running():
    if st.session_state.ws_thread is None or not st.session_state.ws_thread.is_alive():
        st.session_state.ws_thread = threading.Thread(target=start_websocket_thread, daemon=True)
        st.session_state.ws_thread.start()

ensure_websocket_thread_running()

# ------------------------------------------------------------------------------
# 5) Layout Streamlit
# ------------------------------------------------------------------------------
st.title("Cryptocurrency Trading Dashboard")

# Sidebar de configuration
with st.sidebar:
    st.header("Configuration")
    api_key = st.text_input("API Key", type="password")
    if api_key:
        st.session_state.client.api_key = api_key

    # Liste des exchanges
    exchanges = st.session_state.client.list_exchanges()
    if exchanges:
        selected_exchange = st.selectbox("Select Exchange", exchanges)
        if selected_exchange != st.session_state.selected_exchange:
            st.session_state.selected_exchange = selected_exchange
            pairs = st.session_state.client.list_pairs(selected_exchange)
            st.session_state.pairs = pairs or []

        # Liste des paires
        if hasattr(st.session_state, 'pairs'):
            selected_pair = st.selectbox("Select Trading Pair", st.session_state.pairs)
            if selected_pair != st.session_state.selected_pair:
                st.session_state.selected_pair = selected_pair
                if st.session_state.selected_exchange:
                    # Réinitialise l'order book pour la nouvelle paire
                    st.session_state.orderbook_data.setdefault(st.session_state.selected_exchange, {})[selected_pair] = {}
                # Appel unique pour informer le serveur de la nouvelle paire
                st.session_state.client.set_active_pair(
                    st.session_state.selected_exchange,
                    selected_pair
                )
    # Sélection de l'intervalle Kline (selon l'exchange)
    if st.session_state.selected_exchange == "binance":
        intervals = ["1m", "5m", "15m", "30m", "1h", "6h", "12h", "1d", "3d", "1w"]
    else:
        intervals = ["1m", "5m", "15m", "30m", "1h", "1d", "1w"]
    selected_interval = st.selectbox("Select Kline Interval", intervals, index=0)
    st.session_state.selected_interval = selected_interval

# Onglets
tab1, tab2, tab3 = st.tabs(["Market Data", "TWAP Trading", "Order History"])

# ------------------------------------------------------------------------------
# 6) Onglet Market Data
# ------------------------------------------------------------------------------
with tab1:
    col1, col2 = st.columns(2)

    # Partie gauche : Order Book
    with col1:
        st.subheader("Order Book")

        # Fusion des snapshots depuis la file d'attente
        if not orderbook_queue.empty():
            new_data = orderbook_queue.get_nowait()
            st.write("📥 [DEBUG] Nouveau snapshot reçu :", new_data)
            for exch, pairs_data in new_data.items():
                st.session_state.orderbook_data.setdefault(exch, {})
                for pair_symbol, ob in pairs_data.items():
                    # Mettre à jour seulement si les données sont complètes
                    if ob.get("bids") and ob.get("asks"):
                        st.session_state.orderbook_data[exch][pair_symbol] = ob

        st.write("Queue size:", orderbook_queue.qsize())

        # Fallback REST : uniquement si aucune donnée n'est présente
        if st.session_state.selected_exchange and st.session_state.selected_pair:
            current = st.session_state.orderbook_data.get(st.session_state.selected_exchange, {}).get(st.session_state.selected_pair, {})
            if not (current.get('bids') and current.get('asks')):
                ob_data = st.session_state.client.get_orderbook(
                    st.session_state.selected_exchange,
                    st.session_state.selected_pair
                )
                if ob_data and ob_data.get('bids') and ob_data.get('asks'):
                    st.session_state.orderbook_data.setdefault(st.session_state.selected_exchange, {})[st.session_state.selected_pair] = ob_data

        # Affichage du contenu pour débogage
        exchange = st.session_state.selected_exchange
        pair = st.session_state.selected_pair
        st.write("DEBUG – orderbook_data pour", exchange, pair, ":",
                 st.session_state.orderbook_data.get(exchange, {}).get(pair, {}))

        def format_orderbook(data):
            if not data:
                st.warning("⚠️ Aucun ordre reçu (bids/asks vides)")
                return pd.DataFrame()
            try:
                df = pd.DataFrame(data, columns=['Price', 'Volume'])
                df['Price'] = pd.to_numeric(df['Price'])
                df['Volume'] = pd.to_numeric(df['Volume'])
                return df
            except Exception as e:
                st.error(f"🚨 Erreur dans format_orderbook: {e}")
                return pd.DataFrame()

        if exchange and pair:
            ob_data = st.session_state.orderbook_data.get(exchange, {}).get(pair, {})
            # Si aucune donnée n'est encore disponible, afficher un message de chargement
            if not (ob_data.get('bids') and ob_data.get('asks')):
                st.info("Chargement des données d'order book pour la paire sélectionnée...")
            else:
                bids_df = format_orderbook(ob_data.get('bids', []))
                asks_df = format_orderbook(ob_data.get('asks', []))
                if not bids_df.empty and not asks_df.empty:
                    bids_df = bids_df.sort_values(by='Price', ascending=False)
                    asks_df = asks_df.sort_values(by='Price', ascending=True)
                    bids_df['Price'] = bids_df['Price'].apply(lambda x: f"{x:.2f}")
                    bids_df['Volume'] = bids_df['Volume'].apply(lambda x: f"{x:.5f}")
                    asks_df['Price'] = asks_df['Price'].apply(lambda x: f"{x:.2f}")
                    asks_df['Volume'] = asks_df['Volume'].apply(lambda x: f"{x:.5f}")

                    st.markdown("<h3 style='color: green;'>Bids 🟢</h3>", unsafe_allow_html=True)
                    st.markdown(bids_df.to_html(index=False), unsafe_allow_html=True)
                    st.markdown("<h3 style='color: red;'>Asks 🔴</h3>", unsafe_allow_html=True)
                    st.markdown(asks_df.to_html(index=False), unsafe_allow_html=True)
                else:
                    st.warning("⚠️ Order book data incomplete.")
        else:
            st.warning("⚠️ Please select an exchange and pair.")

    # Partie droite : Graphique (kline)
    with col2:
        st.subheader("Price Chart")
        if exchange and pair:
            try:
                kline_data = st.session_state.client.get_klines(
                    exchange,
                    pair,
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
                        title=f"{pair} ({st.session_state.selected_interval})",
                        yaxis_title='Price',
                        xaxis_title='Time',
                        xaxis_rangeslider_visible=False
                    )
                    st.plotly_chart(fig, use_container_width=True)
                else:
                    st.warning(f"No kline data for {pair} on {exchange}")
            except Exception as e:
                st.error(f"Error fetching kline data: {str(e)}")

# ------------------------------------------------------------------------------
# 7) Onglet TWAP Trading
# ------------------------------------------------------------------------------
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

# ------------------------------------------------------------------------------
# 8) Onglet Order History
# ------------------------------------------------------------------------------
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

from streamlit_autorefresh import st_autorefresh
from client import APIClient

# Rafraîchit la page toutes les 2 secondes pour l'affichage de l'order book
st.set_page_config(page_title="OrderBook Stream", layout="wide")
st_autorefresh(interval=2000, key="orderbook_refresh")

# File pour les mises à jour de l'order book
orderbook_queue = queue.Queue()

# ------------------------------------------------------------------------------
# 1) Initialisation de st.session_state
# ------------------------------------------------------------------------------
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

if 'ws_thread' not in st.session_state:
    st.session_state.ws_thread = None

# ------------------------------------------------------------------------------
# 2) Fonction asynchrone pour consommer le WebSocket du serveur
# ------------------------------------------------------------------------------
async def update_orderbook():
    """Récupération des données d'order book via WebSocket global."""
    uri = "ws://localhost:8000/ws/orderbook"
    while True:
        try:
            async with websockets.connect(uri) as websocket:
                st.write(f"🔌 [DEBUG] Connected to {uri} WebSocket!")
                while True:
                    data = await websocket.recv()
                    parsed_data = json.loads(data)
                    st.write(f"📩 [DEBUG] Données WebSocket reçues : {json.dumps(parsed_data, indent=4)}")
                    if parsed_data:
                        orderbook_queue.put(parsed_data)
                    await asyncio.sleep(1)
        except websockets.exceptions.ConnectionClosed as e:
            st.write(f"⚠️ WebSocket closed: {e}. Reconnecting in 5s...")
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            st.write("🔌 [DEBUG] WebSocket task cancelled.")
            break
        except Exception as e:
            st.write(f"❌ WebSocket error: {e}. Reconnecting in 5s...")
            await asyncio.sleep(5)

# ------------------------------------------------------------------------------
# 3) Thread pour lancer l'event loop asynchrone
# ------------------------------------------------------------------------------
def start_websocket_thread():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(update_orderbook())

# ------------------------------------------------------------------------------
# 4) Lancement conditionnel du thread WebSocket
# ------------------------------------------------------------------------------
def ensure_websocket_thread_running():
    if st.session_state.ws_thread is None or not st.session_state.ws_thread.is_alive():
        st.session_state.ws_thread = threading.Thread(target=start_websocket_thread, daemon=True)
        st.session_state.ws_thread.start()

ensure_websocket_thread_running()

# ------------------------------------------------------------------------------
# 5) Layout Streamlit
# ------------------------------------------------------------------------------
st.title("Cryptocurrency Trading Dashboard")

# Sidebar de configuration
with st.sidebar:
    st.header("Configuration")
    api_key = st.text_input("API Key", type="password")
    if api_key:
        st.session_state.client.api_key = api_key

    # Liste des exchanges
    exchanges = st.session_state.client.list_exchanges()
    if exchanges:
        selected_exchange = st.selectbox("Select Exchange", exchanges)
        if selected_exchange != st.session_state.selected_exchange:
            st.session_state.selected_exchange = selected_exchange
            pairs = st.session_state.client.list_pairs(selected_exchange)
            st.session_state.pairs = pairs or []

        # Liste des paires
        if hasattr(st.session_state, 'pairs'):
            selected_pair = st.selectbox("Select Trading Pair", st.session_state.pairs)
            if selected_pair != st.session_state.selected_pair:
                st.session_state.selected_pair = selected_pair
                if st.session_state.selected_exchange:
                    # Réinitialise l'order book pour la nouvelle paire
                    st.session_state.orderbook_data.setdefault(st.session_state.selected_exchange, {})[selected_pair] = {}
                # Appel unique pour informer le serveur de la nouvelle paire
                st.session_state.client.set_active_pair(
                    st.session_state.selected_exchange,
                    selected_pair
                )
    # Sélection de l'intervalle Kline (selon l'exchange)
    if st.session_state.selected_exchange == "binance":
        intervals = ["1m", "5m", "15m", "30m", "1h", "6h", "12h", "1d", "3d", "1w"]
    else:
        intervals = ["1m", "5m", "15m", "30m", "1h", "1d", "1w"]
    selected_interval = st.selectbox("Select Kline Interval", intervals, index=0)
    st.session_state.selected_interval = selected_interval

# Onglets
tab1, tab2, tab3 = st.tabs(["Market Data", "TWAP Trading", "Order History"])

# ------------------------------------------------------------------------------
# 6) Onglet Market Data
# ------------------------------------------------------------------------------
with tab1:
    col1, col2 = st.columns(2)

    # Partie gauche : Order Book
    with col1:
        st.subheader("Order Book")

        # Fusion des snapshots depuis la file d'attente
        if not orderbook_queue.empty():
            new_data = orderbook_queue.get_nowait()
            st.write("📥 [DEBUG] Nouveau snapshot reçu :", new_data)
            for exch, pairs_data in new_data.items():
                st.session_state.orderbook_data.setdefault(exch, {})
                for pair_symbol, ob in pairs_data.items():
                    # Mettre à jour seulement si les données sont complètes
                    if ob.get("bids") and ob.get("asks"):
                        st.session_state.orderbook_data[exch][pair_symbol] = ob

        st.write("Queue size:", orderbook_queue.qsize())

        # Fallback REST : uniquement si aucune donnée n'est présente
        if st.session_state.selected_exchange and st.session_state.selected_pair:
            current = st.session_state.orderbook_data.get(st.session_state.selected_exchange, {}).get(st.session_state.selected_pair, {})
            if not (current.get('bids') and current.get('asks')):
                ob_data = st.session_state.client.get_orderbook(
                    st.session_state.selected_exchange,
                    st.session_state.selected_pair
                )
                if ob_data and ob_data.get('bids') and ob_data.get('asks'):
                    st.session_state.orderbook_data.setdefault(st.session_state.selected_exchange, {})[st.session_state.selected_pair] = ob_data

        # Affichage du contenu pour débogage
        exchange = st.session_state.selected_exchange
        pair = st.session_state.selected_pair
        st.write("DEBUG – orderbook_data pour", exchange, pair, ":",
                 st.session_state.orderbook_data.get(exchange, {}).get(pair, {}))

        def format_orderbook(data):
            if not data:
                st.warning("⚠️ Aucun ordre reçu (bids/asks vides)")
                return pd.DataFrame()
            try:
                df = pd.DataFrame(data, columns=['Price', 'Volume'])
                df['Price'] = pd.to_numeric(df['Price'])
                df['Volume'] = pd.to_numeric(df['Volume'])
                return df
            except Exception as e:
                st.error(f"🚨 Erreur dans format_orderbook: {e}")
                return pd.DataFrame()

        if exchange and pair:
            ob_data = st.session_state.orderbook_data.get(exchange, {}).get(pair, {})
            # Si aucune donnée n'est encore disponible, afficher un message de chargement
            if not (ob_data.get('bids') and ob_data.get('asks')):
                st.info("Chargement des données d'order book pour la paire sélectionnée...")
            else:
                bids_df = format_orderbook(ob_data.get('bids', []))
                asks_df = format_orderbook(ob_data.get('asks', []))
                if not bids_df.empty and not asks_df.empty:
                    bids_df = bids_df.sort_values(by='Price', ascending=False)
                    asks_df = asks_df.sort_values(by='Price', ascending=True)
                    bids_df['Price'] = bids_df['Price'].apply(lambda x: f"{x:.2f}")
                    bids_df['Volume'] = bids_df['Volume'].apply(lambda x: f"{x:.5f}")
                    asks_df['Price'] = asks_df['Price'].apply(lambda x: f"{x:.2f}")
                    asks_df['Volume'] = asks_df['Volume'].apply(lambda x: f"{x:.5f}")

                    st.markdown("<h3 style='color: green;'>Bids 🟢</h3>", unsafe_allow_html=True)
                    st.markdown(bids_df.to_html(index=False), unsafe_allow_html=True)
                    st.markdown("<h3 style='color: red;'>Asks 🔴</h3>", unsafe_allow_html=True)
                    st.markdown(asks_df.to_html(index=False), unsafe_allow_html=True)
                else:
                    st.warning("⚠️ Order book data incomplete.")
        else:
            st.warning("⚠️ Please select an exchange and pair.")

    # Partie droite : Graphique (kline)
    with col2:
        st.subheader("Price Chart")
        if exchange and pair:
            try:
                kline_data = st.session_state.client.get_klines(
                    exchange,
                    pair,
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
                        title=f"{pair} ({st.session_state.selected_interval})",
                        yaxis_title='Price',
                        xaxis_title='Time',
                        xaxis_rangeslider_visible=False
                    )
                    st.plotly_chart(fig, use_container_width=True)
                else:
                    st.warning(f"No kline data for {pair} on {exchange}")
            except Exception as e:
                st.error(f"Error fetching kline data: {str(e)}")

# ------------------------------------------------------------------------------
# 7) Onglet TWAP Trading
# ------------------------------------------------------------------------------
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

# ------------------------------------------------------------------------------
# 8) Onglet Order History
# ------------------------------------------------------------------------------
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
