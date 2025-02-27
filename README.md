# Cryptocurrency Market Data & TWAP Paper Trading API

Ce projet propose une plateforme complète de **paper trading** (simulation d’ordres) basée sur des stratégies d’exécution **TWAP** (Time-Weighted Average Price) appliquées à des flux de données de marchés réels de **cryptomonnaies**. L’objectif principal est de collecter et normaliser des **carnets d’ordres** (order books) depuis plusieurs places d’échange (ex. : *Binance, Kraken*), puis de simuler l’exécution d’ordres selon différents paramètres (quantité totale, durée, prix limite, etc.).

# 1. Structure du projet

## `server.py`

- Implémente un serveur REST et WebSocket via FastAPI.
- Se connecte en temps réel aux API WebSocket de Binance et Kraken afin de récupérer et maintenir en mémoire le carnet d’ordres.
- Propose des endpoints publics pour consulter les données de marché (exchanges disponibles, paires prises en charge, historiques de chandeliers/K-Line, etc.).
- Implémente des endpoints protégés pour soumettre et suivre des ordres TWAP.
- Utilise un système basique de rate limiting pour gérer plusieurs types de clients (ANONYMOUS, BASIC, PREMIUM).

## `client.py`

- Fournit une classe APIClient en Python pour interagir facilement avec les endpoints du serveur.
- Montre comment se connecter aux routes REST (status, klines, TWAP, etc.) et comment souscrire au WebSocket pour recevoir le carnet d’ordres en direct.
- Contient une fonction asynchrone websocket_orderbook qui écoute les mises à jour du carnet d’ordres (order book) transmises par le serveur.

## `app.py` (interface utilisateur Streamlit)

- Offre une interface graphique simple (dashboard) pour :
- Configurer son API Key, sélectionner un exchange et une paire.
- Afficher en temps réel le carnet d’ordres (visualisation via plotly).
- Afficher l’historique des prix sous forme de bougies (candlesticks).
- Placer des ordres TWAP (choix du côté, quantité, prix limite, durée et nombre de tranches).
- Suivre l’historique d’ordres et le niveau d’exécution de chaque ordre TWAP.
  
# 2. Fonctionnalités principales

- Collecte de données de marché en temps réel

- Le serveur se connecte aux flux WebSocket de Binance et Kraken pour récupérer les carnets d’ordres sur certaines paires (ex. BTCUSDT, XBT/USD, etc.).
- Les carnets sont convertis dans un format commun et stockés en mémoire.
  
## Endpoints REST (API publique & privée)

API Publique :
GET /exchanges : liste les exchanges supportés.
GET /exchanges/{exchange}/pairs : liste les paires disponibles sur cet exchange.
GET /klines/{exchange}/{symbol} : retourne un historique de chandelles (klines) pour la paire demandée.
API Authentifiée :
POST /orders/twap : crée un nouvel ordre TWAP.
GET /orders/{order_id} : récupère le statut détaillé d’un ordre.
TWAP (Time-Weighted Average Price) Paper Trading

# 3. Guide d'exécution

Ce projet comprend un serveur backend et une application frontend utilisant Streamlit. Pour exécuter correctement l'application, il est nécessaire de lancer simultanément les fichiers server.py et app.py.

## Prérequis

Avant de démarrer, assurez-vous d'avoir :

- Python installé

- Streamlit installé :

`pip install streamlit`

- Toutes les dépendances nécessaires installées :

`pip install -r requirements.txt`

## Lancer l'application

Pour exécuter l'application, ouvrez un terminal et exécutez la commande suivante :

`python server.py & streamlit run app.py`

### Explication :

- `python server.py` & : Lance le serveur backend en tâche de fond.

- `streamlit run app.py` : Démarre l'application frontend Streamlit.

Une fois l'application démarrée, vous pourrez y accéder via l'URL affichée dans le terminal (généralement `http://localhost:8501`).

## Arrêter l'application

Pour arrêter l'application, utilisez :

- Sous Windows : `Ctrl + C` deux fois

- Sous macOS/Linux : `Ctrl + C`

Si le processus du serveur tourne toujours en arrière-plan, vous pouvez l'arrêter avec :

`pkill -f server.py`

(sous macOS/Linux) ou en fermant le terminal.

## Dépannage

- Si une erreur indique qu'un port est déjà utilisé, vérifiez qu'aucune autre instance du serveur n'est déjà en cours d'exécution.

- Si Streamlit ne se lance pas, assurez-vous qu'il est bien installé (`pip install streamlit`).



