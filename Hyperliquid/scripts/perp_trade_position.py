#!/usr/bin/env python3
"""
perp_trade_position.py - Hyperliquid bot pour un actif perpétuel spécifié sur Testnet
Correction: Les prix (Entrée, TP, SL) sont désormais arrondis au TICK_SIZE exact de l'actif
pour éviter l'erreur 'Invalid TP/SL price'.
"""
import json
import argparse
import time
from pathlib import Path
from eth_account import Account
import decimal # Import nécessaire pour la gestion de la précision

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

# --- Fonctions Utilitaires ---

def round_to_tick(price, tick_size):
    """
    Arrondit le prix au multiple le plus proche du tick_size.
    Ceci est CRUCIAL pour respecter les règles de prix de l'exchange (Ex: BTC doit finir par .0).
    """
    if tick_size is None or tick_size <= 0:
        return price
        
    # Utiliser round() sur la division pour trouver le multiple le plus proche, puis multiplier par le tick_size
    # Note: On utilise float pour cette opération simple d'arrondi à cause de math.
    rounded_price = round(price / tick_size) * tick_size
    
    # Assurer une précision correcte après l'arrondi (évite les erreurs de flottant de Python)
    # Calcule le nombre de décimales du tick_size
    num_decimals = abs(decimal.Decimal(str(tick_size)).as_tuple().exponent)
    
    return round(rounded_price, num_decimals)


# --- Fonctions de chargement (Inchangées) ---

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
        params["take_profit"] = full_params["take_profit"] 
        params["risk_reward"] = full_params.get("risk_reward", 0.0)
        params["size"] = full_params["position_size_units"] 
        params["leverage"] = full_params.get("required_leverage_min", 1) 
        params["side"] = full_params["trade_direction"].lower() 
    except KeyError as e:
        raise ValueError(f"Clé manquante dans trade_params.json: {e}. Veuillez vérifier que 'take_profit' est présente.")

    # --- Validation et Conversion des Types ---
    try:
        params["entry_price"] = float(params["entry_price"])
        params["stop_loss"] = float(params["stop_loss"])
        params["take_profit"] = float(params["take_profit"]) 
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

# --- Fonction Main (Mise à jour pour l'arrondi au tick_size) ---

def main():
    parser = argparse.ArgumentParser(description="Bot Hyperliquid avec sélection d'actif dynamique et arrondi basé sur la précision de l'échange.")
    parser.add_argument("--execute", action="store_true", help="Envoyer réellement les ordres")
    parser.add_argument("--config", default=str(CONFIG_PATH), help="Chemin vers config.json")
    parser.add_argument("--params", default=str(PARAMS_PATH), help="Chemin vers trade_params.json")
    parser.add_argument("--coin", required=True, help="La crypto à trader (e.g., BTC, ETH, SOL).") 
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

    # Actif choisi par l'utilisateur
    coin = args.coin
    
    # Récupérer asset_id et l'information de l'actif
    universe = info_client.meta().get("universe", [])
    
    # Trouver les informations complètes de l'actif
    asset_data = next((m for m in universe if m.get("name") == coin), None)
    
    if asset_data is None:
        raise ValueError(f"L'actif '{coin}' n'a pas été trouvé dans le {network} universe.")
        
    asset_id = asset_data.get("assetId")

    # MODIFICATION CLÉ: Récupérer le TICK_SIZE exact
    TICK_SIZE = asset_data.get("tick_sz", 0)
    if TICK_SIZE is not None:
        TICK_SIZE = float(TICK_SIZE)

    # Calculer le nombre de décimales à partir du tick_size pour l'affichage
    if TICK_SIZE > 0:
        num_decimals = abs(decimal.Decimal(str(TICK_SIZE)).as_tuple().exponent)
    else:
        num_decimals = 0
        
    print(f"INFO: Actif sélectionné: {coin} (ID: {asset_id}). Le TICK_SIZE est {TICK_SIZE}. Les prix seront arrondis à cette précision.")

    # Utiliser les valeurs chargées
    entry_price = trade_params["entry_price"]
    stop_loss = trade_params["stop_loss"]
    take_profit = trade_params["take_profit"] 
    risk_reward_from_json = trade_params["risk_reward"] 
    size = trade_params["size"]
    leverage = trade_params["leverage"]
    side = trade_params["side"]
    is_buy = (side == "buy")
    
    # 3. Vérifications de cohérence directionnelle (Inchangées)
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
        "risk_reward": risk_reward_from_json,
        "size": size,
        "leverage": leverage
    })

    if not args.execute:
        print("DRY-RUN: tous les ordres NON envoyés. Utilise --execute pour exécuter.")
        return

    # --- EXÉCUTION DES ORDRES ---
    
    # Exchange client
    exchange = Exchange(wallet, base_url=API_URL)

    # Vérifier la marge disponible (Inchangée)
    user_state = info_client.user_state(wallet.address)
    available_margin = float(user_state.get("withdrawable", 0))
    required_margin = size * entry_price / leverage
    
    if available_margin < required_margin:
        print(f"Erreur: Marge disponible ({available_margin:.2f}) insuffisante pour l'ordre (besoin ~{required_margin:.2f})")
        return

    # Configurer le leverage (Inchangée)
    resp_leverage = exchange.update_leverage(leverage, coin, is_cross=True)
    print("Update leverage:", resp_leverage)

    # Préparer les ordres avec le NOUVEL arrondi au tick_size
    entry_px_r = round_to_tick(entry_price, TICK_SIZE)
    sl_px_r = round_to_tick(stop_loss, TICK_SIZE)
    tp_px_r = round_to_tick(take_profit, TICK_SIZE)
    
    print(f"Prix arrondis AU TICK pour envoi: Entrée={entry_px_r}, SL={sl_px_r}, TP={tp_px_r}")

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
        "limit_px": tp_px_r, # Le limit_px du TP doit également être au bon prix
        "sz": size,
        "reduce_only": True,
        # Le triggerPx du TP doit être un prix valide, et le limit_px aussi
        "order_type": {"trigger": {"isMarket": False, "triggerPx": tp_px_r, "tpsl": "tp"}}
    }
    
    # 3. Ordre Stop-Loss (Trigger Market)
    order_sl = {
        "coin": coin,
        "is_buy": not is_buy,
        # Pour un Trigger Market, le limit_px n'est pas utilisé pour l'exécution, mais il doit rester un prix valide.
        # En pratique, on pourrait utiliser le prix du SL arrondi.
        "limit_px": sl_px_r, 
        "sz": size,
        "reduce_only": True,
        # Le triggerPx du SL doit être un prix valide
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
