import sys
import math
import json
from pathlib import Path

# Définition du chemin du fichier de sortie
OUTPUT_FILE = Path("trade_params.json")

def trade_calc(entry, sl, capital, risk_percent, rr=2):
    """
    Calcule la taille de position, le niveau de Take Profit, la perte max,
    le gain max, la valeur de la position, la marge requise et l'effet de levier minimal.

    :param entry: Prix d'entrée de la position ($)
    :param sl: Prix du Stop Loss ($)
    :param capital: Capital total du compte de trading ($)
    :param risk_percent: Pourcentage de capital à risquer par trade (%)
    :param rr: Ratio Risque/Récompense (ex: 2 pour 1:2)
    :return: Dictionnaire JSON du résultat ou de l'erreur.
    """

    # 1. Calcul du montant risqué (Montant en $)
    risk_amount = capital * (risk_percent / 100)

    # 2. Détermination de la distance de Stop Loss et vérification d'erreur
    distance_sl = abs(entry - sl)
    
    # Gestion des Erreurs
    if distance_sl == 0:
        return {"status": "error", "message": "Le Stop Loss ne peut pas être égal au Prix d'entrée (distance SL = 0)."}
    if sl <= 0 or entry <= 0:
        return {"status": "error", "message": "Les prix d'entrée et de Stop Loss doivent être strictement positifs (> 0)."}

    # 3. Calcul de la taille de position (Quantité d'unités d'actif)
    position_size = risk_amount / distance_sl

    # 4. Détermination de la direction du trade et du Take Profit (TP)
    reward_amount = distance_sl * rr
    
    if sl < entry:
        tp = entry + reward_amount
        direction = "LONG"
    else:
        tp = entry - reward_amount
        direction = "SHORT"
        
    # 5. Calcul de la valeur totale de la position (exposition en $)
    capital_position = position_size * entry

    # 6. CALCUL DE LEVIER MINIMAL
    marge_initiale_necessaire = capital_position
    levier_min_float = marge_initiale_necessaire / capital
    levier_min_requis = math.ceil(levier_min_float)
    
    # 7. Perte/Gain max
    loss = risk_amount
    gain = loss * rr

    # === APPLICATION DE L'ARRONDI À 5 DÉCIMALES MAX POUR LA COHÉRENCE ===
    position_size_rounded = round(position_size, 5)
    capital_position_rounded = round(capital_position, 5)
    tp_rounded = round(tp, 5)

    # Retourne un dictionnaire contenant toutes les données calculées
    return {
        "status": "success",
        "entry_price": entry, 
        "stop_loss": sl,
        "take_profit": tp_rounded,
        "risk_reward": rr,
        "capital": capital,
        "risk_percent": risk_percent,
        "risk_amount_usd": round(loss, 2),
        "position_size_units": position_size_rounded,
        "position_value_usd": capital_position_rounded,
        "trade_direction": direction,
        "required_leverage_min": int(levier_min_requis)
    }


# -------------------------------------------------------------------------------------
# === Bloc Principal d'Exécution du Script Interactif (Écriture dans le fichier) ===
# -------------------------------------------------------------------------------------

print("=== Calculateur de Trade Crypto avec Levier")

while True:
    try:
        entry_price = round(float(input("Prix d'entrée ($) : ")), 2)
        sl_price = round(float(input("Stop Loss ($) : ")), 2)
        
        capital = float(input("Capital total ($) : "))
        risk_percent = float(input("Risque par trade (%) : "))
        rr = float(input("Risk/Reward (ex: 2 pour 1:2) : "))
        
        # S'assurer que les pourcentages et ratios sont logiques
        if capital <= 0 or risk_percent <= 0 or rr <= 0:
            print("Erreur : Le capital, le risque et le ratio R/R doivent être positifs.")
            continue

        # Exécuter le calcul
        result = trade_calc(
            entry_price, sl_price, capital, risk_percent, rr
        )
        
        # --- NOUVEAU: Écriture dans le fichier JSON ---
        json_output = json.dumps(result, indent=4)
        
        with open(OUTPUT_FILE, "w") as f:
            f.write(json_output)
            
        print("\n--- RÉSULTAT ---")
        if result["status"] == "error":
            print(f"ERREUR: {result['message']}\nLe fichier {OUTPUT_FILE} a été mis à jour avec l'erreur.")
        else:
            print(f"✅ Calcul réussi. Le résultat complet a été écrit dans le fichier : {OUTPUT_FILE}")
            
        print("\n-------------------\n")
        
        # Modification de la question de continuation
        cont = input("Voulez-vous executer le trade dans Hyperliquide ? (o/n) : ").lower()
        if cont == "o":
            # Mise à jour de la réponse pour l'exécution du bot externe
            print(f"Les paramètres du trade sont maintenant disponibles dans {OUTPUT_FILE}.")
            print("Vous pouvez maintenant exécuter votre trade avec la commande: \npython3 perp_trade_position.py --coin BTC --execute")
            print("Au plaisir ! Bonne gestion de risque. 🚀")
            sys.exit()
        else:
            # Clarification du flow si l'utilisateur ne veut pas exécuter
            print("Retour à la saisie pour un nouveau calcul. 🔄")
            
    except ValueError:
        print("Erreur : Veuillez entrer un nombre valide pour chaque paramètre.\n")
    except Exception as e:
        print(f"Une erreur inattendue est survenue : {e}")
        sys.exit()
