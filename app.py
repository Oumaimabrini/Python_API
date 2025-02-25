import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import asyncio
import websockets
import json
import time
from datetime import datetime, timedelta
from client import APIClient
import threading
import queue

# config de la page
st.set_page_config(page_title="OrderBook Stream", layout="wide")

orderbook_queue = queue.Queue()

# Initialize session state
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

# Sidebar for API key and exchange selection
with st.sidebar:
    st.header("Configuration")
    api_key = st.text_input("API Key", type="password")
    if api_key:
        st.session_state.client.api_key = api_key

    # Exchange and pair selection
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

    # Add interval selection dropdown
    intervals = ["1m", "5m", "15m", "30m", "1h", "3h", "6h", "12h", "1d", "3d", "1w"]
    selected_interval = st.selectbox("Select Kline Interval", intervals, index=0)
    st.session_state.selected_interval = selected_interval  # Store the selected interval

# Main content area with tabs
tab1, tab2, tab3 = st.tabs(["Market Data", "TWAP Trading", "Order History"])

# Market Data Tab
with tab1:
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Order Book")

        # Vérifier si des nouvelles données sont disponibles dans la queue
        if not orderbook_queue.empty():
            new_data = orderbook_queue.get()
            st.session_state["orderbook_data"] = new_data  # Met à jour la session
            st.write("✅ Mise à jour de l'order book reçue !")  # Debugging info
            st.experimental_rerun()  # Forcer Streamlit à recharger l'affichage


        # Function to format order book data
        def format_orderbook(data):
            if not data:
                return pd.DataFrame()
            df = pd.DataFrame(data, columns=['Price', 'Size'])
            df['Price'] = pd.to_numeric(df['Price'])
            df['Size'] = pd.to_numeric(df['Size'])
            return df


        orderbook_placeholder = st.empty()  # Créer un conteneur vide

        if not orderbook_queue.empty():
            new_data = orderbook_queue.get()
            st.session_state["orderbook_data"] = new_data  # Mettre à jour la session
            orderbook_placeholder.write(st.session_state["orderbook_data"])  # Mise à jour en temps réel

        # Display order book if data is available
        if (st.session_state.selected_exchange in st.session_state.orderbook_data and
                st.session_state.selected_pair in st.session_state.orderbook_data[st.session_state.selected_exchange]):
            ob_data = st.session_state.orderbook_data[st.session_state.selected_exchange][
                st.session_state.selected_pair]

            bids_df = format_orderbook(ob_data.get('bids', []))
            asks_df = format_orderbook(ob_data.get('asks', []))

            if not bids_df.empty and not asks_df.empty:
                fig = go.Figure()

                # Add bids in green
                fig.add_trace(go.Bar(
                    x=bids_df['Price'],
                    y=bids_df['Size'],
                    name='Bids',
                    marker_color='rgba(0, 255, 0, 0.5)'
                ))

                # Add asks in red
                fig.add_trace(go.Bar(
                    x=asks_df['Price'],
                    y=asks_df['Size'],
                    name='Asks',
                    marker_color='rgba(255, 0, 0, 0.5)'
                ))

                fig.update_layout(
                    title='Order Book Depth',
                    xaxis_title='Price',
                    yaxis_title='Size',
                    barmode='overlay'
                )

                st.plotly_chart(fig, use_container_width=True)
    with col2:
        st.subheader("Price Chart")
        # Fetch and display kline data using the selected interval
        if st.session_state.selected_exchange and st.session_state.selected_pair:
            try:
                kline_data = st.session_state.client.get_klines(
                    st.session_state.selected_exchange,
                    st.session_state.selected_pair,
                    interval=st.session_state.selected_interval,
                    limit=100
                )

                if kline_data and 'candles' in kline_data:
                    # Create a DataFrame from the candles data
                    df = pd.DataFrame(kline_data['candles'])

                    # Convert timestamp to datetime
                    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='s')

                    # Create Plotly candlestick chart
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
                        xaxis_rangeslider_visible=False  # Hide the range slider for a cleaner look
                    )

                    st.plotly_chart(fig, use_container_width=True)

                else:
                    st.warning(
                        f"No kline data available for {st.session_state.selected_pair} on {st.session_state.selected_exchange}")
                    st.write("Debug info:", kline_data)
            except Exception as e:
                st.error(f"Error fetching kline data: {str(e)}")

# TWAP Trading Tab
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
                    # Add order to session state
                    st.session_state.orders.append(response)
                else:
                    st.error("Failed to submit TWAP order")
            except Exception as e:
                st.error(f"Error submitting order: {str(e)}")

# Order History Tab
with tab3:
    st.subheader("Order History")

    if st.session_state.orders:
        # Create a dataframe of orders
        orders_df = pd.DataFrame(st.session_state.orders)

        # Update status of each order
        for i, order in orders_df.iterrows():
            status = st.session_state.client.get_twap_order_status(order['order_id'])
            if status:
                progress = status['executed_quantity'] / status['total_quantity'] * 100
                st.progress(progress)

                st.write(f"Order {order['order_id']}: {progress:.1f}% executed")

                # Display detailed status
                status_df = pd.DataFrame([status])
                st.dataframe(status_df)
    else:
        st.info("No orders found")


# Variable de session pour stocker les données
async def update_orderbook():
    uri = "ws://localhost:8000/ws/orderbook"
    try:
        async with websockets.connect(uri) as websocket:
            while True:
                data = await websocket.recv()
                parsed_data = json.loads(data)  # Convertir en JSON

                print("🟢 Streamlit Reçoit Order Book:", json.dumps(parsed_data, indent=4))

                # Stocker les données dans la queue pour mise à jour
                orderbook_queue.put(parsed_data)

                await asyncio.sleep(1)  # Pause pour éviter la surcharge du WebSocket
    except (websockets.exceptions.ConnectionClosed, asyncio.CancelledError):
        print("⚠ Connexion WebSocket perdue. Tentative de reconnexion...")
        await asyncio.sleep(5)
        await update_orderbook()


# Lancer l'update WebSocket en tâche de fond avec asyncio
# asyncio.create_task(update_orderbook())

# Interface Streamlit
st.title("OrderBook Live Updates")
st.write("Debug Orderbook Data:", json.dumps(st.session_state.get("orderbook_data", "No data received"), indent=4))
st.write("Debug Orderbook Data:", st.session_state.get("orderbook_data", "No data received"))
st.write(st.session_state["orderbook_data"])
st.write("Order Book Debug:", st.session_state.orderbook_data)


# Run the WebSocket updater in the background


def start_orderbook_updater():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    print(" Lancement de la connexion WebSocket vers le serveur...")
    loop.run_until_complete(update_orderbook())


# Lancer le WebSocket dans un thread sans bloquer Streamlit
orderbook_thread = threading.Thread(target=start_orderbook_updater, daemon=True)
orderbook_thread.start()

print("🔄 Thread WebSocket lancé pour récupérer l'order book en temps réel")
