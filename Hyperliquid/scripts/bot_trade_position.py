#!/usr/bin/env python3
"""
bot_trade_position.py - Hyperliquid bot pour un actif perpétuel spécifié sur Testnet
Le Take-Profit (TP) est désormais récupéré directement depuis la clé "take_profit"
du fichier JSON, ignorant le calcul basé sur le Risk/Reward.

---
Mise à jour: La crypto est sélectionnée via l'argument --coin.
L'arrondi des prix (Entrée, TP, SL) est maintenant dynamique
en utilisant la valeur 'szDecimals' ou 'tick_sz' de l'actif.
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

# --- Fonction Main (Modifiée pour le choix de la crypto et l'arrondi dynamique) ---

def main():
    parser = argparse.ArgumentParser(description="Bot Hyperliquid avec sélection d'actif dynamique et arrondi basé sur la précision de l'échange.")
    parser.add_argument("--execute", action="store_true", help="Envoyer réellement les ordres")
    parser.add_argument("--config", default=str(CONFIG_PATH), help="Chemin vers config.json")
    parser.add_argument("--params", default=str(PARAMS_PATH), help="Chemin vers trade_params.json")
    # NOUVEAU: Argument obligatoire pour l'actif
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

    # MODIFICATION CLÉ: Déterminer le nombre de décimales pour l'arrondi.
    # On privilégie 'szDecimals' ou 'tick_sz'. Pour la précision des prix, c'est 'tick_sz' qui est souvent utilisé.
    # La librairie Hyperliquid SDK arrondit souvent au tick size exact, mais le nombre de décimales 
    # est suffisant pour notre fonction round().
    
    # Tentative d'utilisation de 'tick_sz' pour une meilleure précision (si disponible)
    # Dans l'univers HL, le tick_sz est souvent un float (ex: 0.1, 0.0001)
    TICK_SIZE = asset_data.get("tick_sz", None)
    
    # Si 'tick_sz' n'est pas un nombre utile, on utilise 'szDecimals' (comme dans list_perp_assets.py)
    if TICK_SIZE is None or TICK_SIZE >= 1.0: 
        # Si tick_sz est grand (ex: 1.0) ou inexistant, on prend szDecimals pour le nombre de décimales à utiliser
        num_decimals = asset_data.get("szDecimals", 0)
    else:
        # Calcule le nombre de décimales à partir du tick_size float
        import decimal
        # Utiliser Decimal pour éviter les erreurs d'arrondi des floats
        num_decimals = abs(decimal.Decimal(str(TICK_SIZE)).as_tuple().exponent)
    
    # Affichage pour l'utilisateur
    print(f"INFO: Actif sélectionné: {coin} (ID: {asset_id}). Les prix seront arrondis à {num_decimals} décimales.")

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

    # Préparer les ordres avec l'arrondi dynamique
    entry_px_r = round(entry_price, num_decimals)
    sl_px_r = round(stop_loss, num_decimals)
    tp_px_r = round(take_profit, num_decimals)
    
    print(f"Prix arrondis pour envoi: Entrée={entry_px_r}, SL={sl_px_r}, TP={tp_px_r}")

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
        "limit_px": sl_px_r, 
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
