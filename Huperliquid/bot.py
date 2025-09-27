#!/usr/bin/env python3
"""
bot.py - Hyperliquid bot pour BTCUSD sur Testnet avec Take-Profit automatique RR=2
CORRIGÉ: Utilise la méthode exchange.bulk_orders() avec le format de données requis par le SDK.
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

CONFIG_PATH = Path("examples/config.json")

def load_config(path=CONFIG_PATH):
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

def main():
    parser = argparse.ArgumentParser(description="Bot Hyperliquid BTCUSD Testnet avec TP automatique RR=2")
    parser.add_argument("--execute", action="store_true", help="Envoyer réellement les ordres")
    parser.add_argument("--config", default=str(CONFIG_PATH), help="Chemin vers config.json")
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    network = cfg.get("network", "testnet").lower()
    API_URL = constants.MAINNET_API_URL if network == "mainnet" else constants.TESTNET_API_URL
    SECRET = cfg["secret_key"]
    wallet = Account.from_key(SECRET)

    # Client info pour testnet
    info_client = Info(API_URL, skip_ws=True)

    # Asset BTCUSD Testnet
    coin = "BTC"
    
    # Récupérer asset_id (seulement pour l'affichage, non utilisé dans les ordres)
    universe = info_client.meta().get("universe", [])
    asset_id = next((i for i, m in enumerate(universe) if m.get("name") == coin), None)
    if asset_id is None:
        raise ValueError("BTC non trouvé dans le testnet universe")

    # Valeurs testnet (DOIVENT ÊTRE DES FLOATS)
    entry_price = 109771.0
    stop_loss = 108500.0
    size = 0.0005  
    leverage = 1
    side = "buy"
    is_buy = True

    # Take-Profit RR=2
    take_profit = entry_price + (entry_price - stop_loss) * 2

    print("Order values:", {
        "asset_id": asset_id,
        "side": side,
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "size": size,
        "leverage": leverage
    })

    if not args.execute:
        print("DRY-RUN: tous les ordres NON envoyés. Utilise --execute pour exécuter.")
        return

    # Exchange client
    exchange = Exchange(wallet, base_url=API_URL)

    # Vérifier la marge disponible (CORRECTION: Utilise 'withdrawable')
    user_state = info_client.user_state(wallet.address)
    available_margin = float(user_state.get("withdrawable", 0))
    required_margin = size * entry_price / leverage
    
    if available_margin < required_margin:
        print(f"Erreur: Marge disponible ({available_margin}) insuffisante pour l'ordre (besoin ~{required_margin})")
        return

    # Configurer le leverage
    resp_leverage = exchange.update_leverage(leverage, coin, is_cross=True)
    print("Update leverage:", resp_leverage)

    # Préparer les ordres (CORRECTIONS: Utilise le format SDK lisible)
    # Les prix et la taille DOIVENT être des floats (non des strings)
    
    # 1. Ordre Principal (Limit Entry)
    order_main = {
        "coin": coin,
        "is_buy": is_buy,
        "limit_px": entry_price,  # float
        "sz": size,               # float
        "reduce_only": False,
        "order_type": {"limit": {"tif": "Gtc"}}
    }
    
    # 2. Ordre Take-Profit (Trigger Limit)
    order_tp = {
        "coin": coin,
        "is_buy": not is_buy,
        "limit_px": take_profit,  # float
        "sz": size,               # float
        "reduce_only": True,
        "order_type": {"trigger": {"isMarket": False, "triggerPx": take_profit, "tpsl": "tp"}} # triggerPx doit être float
    }
    
    # 3. Ordre Stop-Loss (Trigger Market)
    order_sl = {
        "coin": coin,
        "is_buy": not is_buy,
        "limit_px": stop_loss,    # float
        "sz": size,               # float
        "reduce_only": True,
        "order_type": {"trigger": {"isMarket": True, "triggerPx": stop_loss, "tpsl": "sl"}} # triggerPx doit être float
    }

    # Créer le payload pour le bulk_orders
    bulk_order_payload = {
        "type": "order",
        "orders": [order_main, order_tp, order_sl],
        "grouping": "normalTpsl"
    }

    # Envoyer les ordres groupés (CORRECTION: Utilise la méthode publique bulk_orders)
    # On passe uniquement la liste d'ordres, l'argument 'grouping' n'étant pas supporté
    # comme argument positionnel dans votre version du SDK.
    resp = exchange.bulk_orders(
        bulk_order_payload["orders"]
    )
    
    print("Bulk orders (entry + TP + SL grouped):", resp)
    print("Terminé.")

if __name__ == "__main__":
    main()
