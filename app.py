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

# Configuration initiale de Streamlit
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
                while True:
                    data = await websocket.recv()
                    parsed_data = json.loads(data)
                    if parsed_data:
                        orderbook_queue.put(parsed_data)
                    await asyncio.sleep(1)
        except websockets.exceptions.ConnectionClosed:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            break
        except Exception:
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
                    st.session_state.orderbook_data.setdefault(st.session_state.selected_exchange, {})[
                        selected_pair] = {}
                st.session_state.client.set_active_pair(
                    st.session_state.selected_exchange,
                    selected_pair
                )
    # Sélection de l'intervalle Kline
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
            for exch, pairs_data in new_data.items():
                st.session_state.orderbook_data.setdefault(exch, {})
                for pair_symbol, ob in pairs_data.items():
                    if ob.get("bids") and ob.get("asks"):
                        st.session_state.orderbook_data[exch][pair_symbol] = ob

        # Fallback REST : uniquement si aucune donnée n'est présente
        if st.session_state.selected_exchange and st.session_state.selected_pair:
            current = st.session_state.orderbook_data.get(st.session_state.selected_exchange, {}).get(
                st.session_state.selected_pair, {})
            if not (current.get('bids') and current.get('asks')):
                ob_data = st.session_state.client.get_orderbook(
                    st.session_state.selected_exchange,
                    st.session_state.selected_pair
                )
                if ob_data and ob_data.get('bids') and ob_data.get('asks'):
                    st.session_state.orderbook_data.setdefault(st.session_state.selected_exchange, {})[
                        st.session_state.selected_pair] = ob_data


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


        exchange = st.session_state.selected_exchange
        pair = st.session_state.selected_pair

        if exchange and pair:
            ob_data = st.session_state.orderbook_data.get(exchange, {}).get(pair, {})
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
        for order in st.session_state.orders:
            try:
                order_id = order.get('order_id')
                if not order_id:
                    st.warning("Order missing ID")
                    continue

                status = st.session_state.client.get_twap_order_status(order_id)

                if status:
                    # Create an expander for each order for better organization
                    with st.expander(f"Order {order_id} - {status.get('pair', 'Unknown Pair')}"):
                        col1, col2 = st.columns(2)

                        with col1:
                            st.write("**Order Details:**")
                            st.write(f"Exchange: {status.get('exchange', 'N/A')}")
                            st.write(f"Pair: {status.get('pair', 'N/A')}")
                            st.write(f"Side: {status.get('side', 'N/A')}")
                            st.write(f"Limit Price: {status.get('limit_price', 'N/A')}")

                        with col2:
                            st.write("**Execution Details:**")
                            st.write(f"Slices: {status.get('slices_executed', 0)}/{status.get('slices', 0)}")
                            st.write(f"Created: {status.get('created_at', 'N/A')}")
                            st.write(f"Updated: {status.get('updated_at', 'N/A')}")
                            st.write(f"Completed: {'Yes' if status.get('is_completed', False) else 'No'}")

                        # Calculate and show progress
                        if 'executed_quantity' in status and 'total_quantity' in status and status[
                            'total_quantity'] > 0:
                            progress = status['slices_executed'] / status['slices']
                            st.progress(progress)

                else:
                    st.warning(f"Could not retrieve status for order {order_id}")
            except Exception as e:
                st.error(f"Error processing order: {str(e)}")
    else:
        st.info("No orders found. Create a TWAP order in the Trading tab to get started.")
