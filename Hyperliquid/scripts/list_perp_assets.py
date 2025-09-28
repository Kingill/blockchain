#!/usr/bin/env python3
"""
list_perp_assets.py - Liste toutes les paires perpétuelles Hyperliquid avec asset_id
"""

from hyperliquid.info import Info
from hyperliquid.utils import constants
import json

# Choix du réseau
# Si vous voulez l'utiliser sur le Testnet, laissez à False.
# L'API Testnet sera alors utilisée.
USE_MAINNET = False
API_URL = constants.MAINNET_API_URL if USE_MAINNET else constants.TESTNET_API_URL

# Initialisation du client Info
info_client = Info(API_URL, skip_ws=True)

# Récupération des métadonnées
# 'meta()' retourne les métadonnées pour les PERPÉTUELS.
meta_data = info_client.meta()
assets = meta_data.get("universe", []) # La liste 'universe' contient les perpétuels.

# Correction : Tous les éléments dans 'assets' sont des marchés perpétuels.
# La clé 'asset_id' est simplement leur index dans cette liste 'universe'.
perp_markets = assets # Nul besoin de filtrer !

print(f"Nombre de marchés perpétuels ({'Mainnet' if USE_MAINNET else 'Testnet'}): {len(perp_markets)}\n")

# Affichage lisible
for i, market in enumerate(perp_markets):
    # L'asset_id est implicitement l'index 'i' pour les perpétuels.
    symbol = market.get("name", "N/A") 
    asset_id = i 
    
    # Autres infos utiles
    max_leverage = market.get("maxLeverage", "N/A")
    sz_decimals = market.get("szDecimals", "N/A")
    
    print(f"[{asset_id:02d}] {symbol:<6} | Max Leverage: {max_leverage:<4} | Taille Décimales: {sz_decimals}")

# Sauvegarde optionnelle dans un fichier JSON
# Ajoutons l'asset_id (l'index) à chaque objet avant la sauvegarde pour la clarté
perp_markets_with_id = []
for i, market in enumerate(perp_markets):
    market_with_id = market.copy()
    market_with_id['asset_id'] = i
    perp_markets_with_id.append(market_with_id)

filename = f"perp_assets_{'mainnet' if USE_MAINNET else 'testnet'}.json"
with open(filename, "w") as f:
    json.dump(perp_markets_with_id, f, indent=2)

print(f"\nListe complète des perpétuels sauvegardée dans {filename}")
