#!/usr/bin/env python3
"""
bot.py - Hyperliquid bot pour BTCUSD sur Testnet
Le Take-Profit (TP) est désormais récupéré directement depuis la clé "take_profit"
du fichier JSON, ignorant le calcul basé sur le Risk/Reward.
"""
import json
import argparse
import time
from pathlib import Path
from eth_account import Account

# SDK imports
try:
    from hyperliquid.exchange import Exchange
    from hyperliquid.utils import constants
    from hyperliquid.info import Info
except Exception as e:
    print("Erreur import SDK ou eth_account:", e)
    raise

# Chemins de configuration
CONFIG_PATH = Path("config.json")
PARAMS_PATH = Path("trade_params.json")

# --- Fonctions de chargement ---

def load_config(path=CONFIG_PATH):
    """Charge la clé secrète et le réseau (testnet/mainnet)"""
    if not path.exists():
        raise FileNotFoundError(f"Config introuvable: {path}")
    raw_lines = []
    with open(path, "r") as f:
        for line in f:
            raw_lines.append(line.split("//")[0])
    cfg = json.loads("\n".join(raw_lines))
    if "secret_key" not in cfg:
        raise ValueError("config.json doit contenir 'secret_key'")
    cfg.setdefault("network", "testnet")
    return cfg

def load_trade_params(path=PARAMS_PATH):
    """
    Charge les paramètres de trading en mappant les clés spécifiques de l'utilisateur.
    Charge maintenant explicitement la clé 'take_profit'.
    """
    if not path.exists():
        raise FileNotFoundError(f"Fichier de paramètres introuvable: {path}. Créez-le!")
        
    with open(path, "r") as f:
        full_params = json.load(f)

    # --- MAPPAGE ET VÉRIFICATION DES CLÉS ---
    try:
        params = {}
        params["entry_price"] = full_params["entry_price"]
        params["stop_loss"] = full_params["stop_loss"]
        # NOUVEAU: Récupère la valeur du TP fournie
        params["take_profit"] = full_params["take_profit"] 
        # Récupère le RR à des fins d'affichage
        params["risk_reward"] = full_params.get("risk_reward", 0.0)
        # Mappage de 'position_size_units' vers 'size'
        params["size"] = full_params["position_size_units"] 
        # Mappage de 'required_leverage_min' vers 'leverage'
        params["leverage"] = full_params.get("required_leverage_min", 1) 
        # Mappage de 'trade_direction' vers 'side'
        params["side"] = full_params["trade_direction"].lower() 
    except KeyError as e:
        raise ValueError(f"Clé manquante dans trade_params.json: {e}. Veuillez vérifier que 'take_profit' est présente.")

    # --- Validation et Conversion des Types ---
    try:
        params["entry_price"] = float(params["entry_price"])
        params["stop_loss"] = float(params["stop_loss"])
        params["take_profit"] = float(params["take_profit"]) # Convertir le TP
        params["size"] = float(params["size"])
        params["leverage"] = int(params["leverage"])
        params["risk_reward"] = float(params["risk_reward"])
    except ValueError as e:
        raise ValueError(f"Erreur de format pour un paramètre numérique: {e}")

    # Validation de la direction du trade
    if params["side"] == "long":
        params["side"] = "buy"
    elif params["side"] == "short":
        params["side"] = "sell"
    else:
        raise ValueError("Le paramètre 'trade_direction' doit être 'LONG' ou 'SHORT'.")
        
    return params

# --- Fonction Main ---

def main():
    parser = argparse.ArgumentParser(description="Bot Hyperliquid BTCUSD Testnet avec TP et SL basés sur les valeurs JSON")
    parser.add_argument("--execute", action="store_true", help="Envoyer réellement les ordres")
    parser.add_argument("--config", default=str(CONFIG_PATH), help="Chemin vers config.json")
    parser.add_argument("--params", default=str(PARAMS_PATH), help="Chemin vers trade_params.json")
    args = parser.parse_args()

    # 1. Charger les configurations (API, clé secrète)
    cfg = load_config(Path(args.config))
    network = cfg.get("network", "testnet").lower()
    API_URL = constants.MAINNET_API_URL if network == "mainnet" else constants.TESTNET_API_URL
    SECRET = cfg["secret_key"]
    wallet = Account.from_key(SECRET)

    # 2. Charger les paramètres de trading
    trade_params = load_trade_params(Path(args.params))

    # Client info pour testnet
    info_client = Info(API_URL, skip_ws=True)

    # Asset BTCUSD
    coin = "BTC"
    
    # Récupérer asset_id
    universe = info_client.meta().get("universe", [])
    asset_id = next((i for i, m in enumerate(universe) if m.get("name") == coin), None)
    if asset_id is None:
        raise ValueError(f"{coin} non trouvé dans le {network} universe")

    # Utiliser les valeurs chargées
    entry_price = trade_params["entry_price"]
    stop_loss = trade_params["stop_loss"]
    # UTILISATION DE LA VALEUR TP CHARGÉE DU JSON
    take_profit = trade_params["take_profit"] 
    # Utilisation du RR pour l'affichage uniquement
    risk_reward_from_json = trade_params["risk_reward"] 
    size = trade_params["size"]
    leverage = trade_params["leverage"]
    side = trade_params["side"]
    is_buy = (side == "buy")
    
    # 3. Vérifications de cohérence directionnelle (TP > Entrée > SL pour Long, etc.)
    if is_buy:
        if not (take_profit > entry_price and entry_price > stop_loss):
            print("AVERTISSEMENT: Cohérence prix (Long: TP > Entry > SL) non respectée dans le JSON!")
    else:
        if not (take_profit < entry_price and entry_price < stop_loss):
            print("AVERTISSEMENT: Cohérence prix (Short: TP < Entry < SL) non respectée dans le JSON!")


    print("Order values:", {
        "asset_id": asset_id,
        "side": side,
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "RR_from_JSON": risk_reward_from_json,
        "size": size,
        "leverage": leverage
    })

    if not args.execute:
        print("DRY-RUN: tous les ordres NON envoyés. Utilise --execute pour exécuter.")
        return

    # --- EXÉCUTION DES ORDRES ---
    
    # Exchange client
    exchange = Exchange(wallet, base_url=API_URL)

    # Vérifier la marge disponible
    user_state = info_client.user_state(wallet.address)
    available_margin = float(user_state.get("withdrawable", 0))
    required_margin = size * entry_price / leverage
    
    if available_margin < required_margin:
        print(f"Erreur: Marge disponible ({available_margin:.2f}) insuffisante pour l'ordre (besoin ~{required_margin:.2f})")
        return

    # Configurer le leverage
    resp_leverage = exchange.update_leverage(leverage, coin, is_cross=True)
    print("Update leverage:", resp_leverage)

    # Préparer les ordres (Utilise les valeurs directement chargées et arrondies au besoin)
    # Pour minimiser les erreurs d'incrément de prix (tick size), on arrondit à 1 décimale
    # Note: L'échange préfère souvent des entiers pour BTC, vous pouvez passer à round(..., 0) si 1 décimale échoue.
    entry_px_r = round(entry_price, 1)
    sl_px_r = round(stop_loss, 1)
    tp_px_r = round(take_profit, 1)

    # 1. Ordre Principal (Limit Entry)
    order_main = {
        "coin": coin,
        "is_buy": is_buy,
        "limit_px": entry_px_r,
        "sz": size,
        "reduce_only": False,
        "order_type": {"limit": {"tif": "Gtc"}}
    }
    
    # 2. Ordre Take-Profit (Trigger Limit)
    order_tp = {
        "coin": coin,
        "is_buy": not is_buy,
        "limit_px": tp_px_r,
        "sz": size,
        "reduce_only": True,
        "order_type": {"trigger": {"isMarket": False, "triggerPx": tp_px_r, "tpsl": "tp"}}
    }
    
    # 3. Ordre Stop-Loss (Trigger Market)
    order_sl = {
        "coin": coin,
        "is_buy": not is_buy,
        "limit_px": sl_px_r, # Limit_px pour un SL Market est souvent mis au SL trigger
        "sz": size,
        "reduce_only": True,
        "order_type": {"trigger": {"isMarket": True, "triggerPx": sl_px_r, "tpsl": "sl"}}
    }

    # Envoyer les ordres groupés
    resp = exchange.bulk_orders(
        [order_main, order_tp, order_sl]
    )
    
    print("Bulk orders (entry + TP + SL grouped):", resp)
    print("Terminé.")

if __name__ == "__main__":
    main()
