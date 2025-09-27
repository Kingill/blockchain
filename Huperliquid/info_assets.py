#!/usr/bin/env python3
"""
info_assets.py - Liste tous les actifs disponibles sur Hyperliquid (testnet ou mainnet)
"""

from hyperliquid.info import Info
from hyperliquid.utils import constants
import json

# Choix du réseau
USE_MAINNET = False
API_URL = constants.MAINNET_API_URL if USE_MAINNET else constants.TESTNET_API_URL

# Initialisation du client Info
info_client = Info(API_URL, skip_ws=True)

# Récupération des métadonnées
meta_data = info_client.meta()  # renvoie l'objet "Meta" complet

# "universe" contient tous les actifs
assets = meta_data.get("universe", [])

print(f"Nombre d'actifs disponibles: {len(assets)}\n")

# Affichage lisible
for asset in assets:
    symbol = asset.get("symbol", asset.get("name", "N/A"))
    asset_id = asset.get("asset_id", "N/A")
    print(f"{symbol} → asset_id: {asset_id}")

# Optionnel : sauvegarder dans un JSON
with open("assets.json", "w") as f:
    json.dump(assets, f, indent=2)

print("\nListe complète sauvegardée dans assets.json")
