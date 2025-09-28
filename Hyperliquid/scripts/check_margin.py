#!/usr/bin/env python3
"""
check_margin.py - Script pour vérifier la marge disponible sur Hyperliquid
"""
import json
from pathlib import Path
from eth_account import Account

# SDK imports
try:
    from hyperliquid.info import Info
    from hyperliquid.utils import constants
except Exception as e:
    print("Erreur import SDK ou eth_account:", e)
    raise

# --- Configuration (Reprise de votre code) ---
CONFIG_PATH = Path("config.json")

def load_config(path=CONFIG_PATH):
    if not path.exists():
        raise FileNotFoundError(f"Config introuvable: {path}")
    raw_lines = []
    with open(path, "r") as f:
        for line in f:
            # Supprime les commentaires // pour que json.loads fonctionne
            raw_lines.append(line.split("//")[0])
    cfg = json.loads("\n".join(raw_lines))
    if "secret_key" not in cfg:
        raise ValueError("config.json doit contenir 'secret_key'")
    cfg.setdefault("network", "testnet")
    return cfg
# ---------------------------------------------


def check_available_margin():
    """Charge la config, se connecte, et affiche la marge disponible."""
    try:
        cfg = load_config()
    except Exception as e:
        print(f"Erreur de configuration: {e}")
        return

    network = cfg.get("network", "testnet").lower()
    API_URL = constants.MAINNET_API_URL if network == "mainnet" else constants.TESTNET_API_URL
    SECRET = cfg["secret_key"]
    
    # 1. Initialiser le wallet et l'adresse
    wallet = Account.from_key(SECRET)
    wallet_address = wallet.address
    
    print(f"Connexion au réseau: {network.upper()} via API: {API_URL}")
    print(f"Adresse du compte: {wallet_address}")

    # 2. Initialiser le client Info
    info_client = Info(API_URL, skip_ws=True)

    # 3. Récupérer l'état de l'utilisateur
    try:
        user_state = info_client.user_state(wallet_address)
        
        # Vérification et extraction de la marge
        #available_margin = float(
        #    user_state.get("response", {})
        #              .get("margin", {})
        #              .get("available", 0) # Utilise 0 si l'information est manquante
        #)
        available_margin = float(
            user_state.get("withdrawable", 0)
        )
        
        # --- Affichage du résultat ---
        print("\n" + "="*50)
        print(f"✅ Marge disponible sur {network.upper()}: {available_margin:,.2f} USDC")
        print("="*50)

        # DEBUG pour le problème précédent: Afficher l'état complet
        print("\n--- État brut de l'utilisateur (pour le débogage) ---")
        print(json.dumps(user_state, indent=2))
        
    except Exception as e:
        print(f"\n❌ Erreur lors de l'appel API 'user_state': {e}")
        print("Veuillez vérifier votre connexion ou votre clé secrète.")


if __name__ == "__main__":
    check_available_margin()
