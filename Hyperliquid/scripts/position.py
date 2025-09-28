import sys
import math

def trade_calc(entry, sl, capital, risk_percent, rr=2):
    """
    Calcule la taille de position, le niveau de Take Profit, la perte max,
    le gain max, la valeur de la position, la marge requise et l'effet de levier minimal.

    :param entry: Prix d'entrée de la position ($)
    :param sl: Prix du Stop Loss ($)
    :param capital: Capital total du compte de trading ($)
    :param risk_percent: Pourcentage de capital à risquer par trade (%)
    :param rr: Ratio Risque/Récompense (ex: 2 pour 1:2)
    :return: Taille de position (unités), TP ($), Perte ($), Gain ($), Valeur de Position ($), Direction, Marge ($), Levier Min.
    """

    # 1. Calcul du montant risqué (Montant en $)
    risk_amount = capital * (risk_percent / 100)

    # 2. Détermination de la distance de Stop Loss et vérification d'erreur
    distance_sl = abs(entry - sl)
    
    if distance_sl == 0:
        return None, "Erreur: Le Stop Loss ne peut pas être égal au Prix d'entrée (distance SL = 0).", None, None, None, None, None, None
    
    if sl <= 0 or entry <= 0:
        return None, "Erreur: Les prix d'entrée et de Stop Loss doivent être strictement positifs (> 0).", None, None, None, None, None, None

    # 3. Calcul de la taille de position (Quantité d'unités d'actif)
    position_size = risk_amount / distance_sl

    # 4. Détermination de la direction du trade et du Take Profit (TP)
    reward_amount = distance_sl * rr
    
    if sl < entry:
        # Position Longue (Achat): SL est en dessous de l'entrée
        tp = entry + reward_amount
        direction = "LONG"
    else: # sl > entry
        # Position Courte (Vente): SL est au-dessus de l'entrée
        tp = entry - reward_amount
        direction = "SHORT"
        
    # 5. Calcul de la valeur totale de la position (exposition en $)
    capital_position = position_size * entry

    # 6. CALCUL DE LEVIER MINIMAL (Adaptation Anti-"Not Enough Margin")
    # Marge requise est égale à la valeur de la position.
    marge_initiale_necessaire = capital_position
    
    # Levier minimal requis pour que Marge Initiale <= Capital
    # Levier Min = Valeur de la Position / Capital Disponible
    levier_min_float = marge_initiale_necessaire / capital
    
    # Nous prenons le plafond (ceil) pour être certain d'avoir la marge suffisante
    # Ex: 3.82x devient 4x
    levier_min_requis = math.ceil(levier_min_float)
    
    # 7. Perte/Gain max
    loss = risk_amount
    gain = loss * rr

    return position_size, tp, loss, gain, capital_position, direction, marge_initiale_necessaire, levier_min_requis


# -------------------------------------------------------------------------------------
# === Bloc Principal d'Exécution du Script Interactif ===
# -------------------------------------------------------------------------------------

print("=== Calculateur de Trade Crypto avec Levier Hyperliquid ===\n")

while True:
    try:
        # Demander les entrées utilisateur
        entry_price = float(input("Prix d'entrée ($) : "))
        sl_price = float(input("Stop Loss ($) : "))
        capital = float(input("Capital total ($) : "))
        risk_percent = float(input("Risque par trade (%) : "))
        rr = float(input("Risk/Reward (ex: 2 pour 1:2) : "))
        
        # S'assurer que les pourcentages et ratios sont logiques
        if capital <= 0 or risk_percent <= 0 or rr <= 0:
            print("Erreur : Le capital, le risque et le ratio R/R doivent être positifs.")
            continue

        # Exécuter le calcul
        size, tp, loss, gain, cap_pos, direction, marge_req_theorique, levier_min = trade_calc(
            entry_price, sl_price, capital, risk_percent, rr
        )
        
        # Afficher le résultat
        if size is None:
            # Afficher le message d'erreur retourné par la fonction
            print(f"\nERREUR: {tp}\n")
        else:
            print("\n--- RÉSULTAT DU CALCUL & EXIGENCES ---")
            print(f" - Direction du Trade : {direction}")
            print(f" - Risque (1R) : {loss:.2f} $ ({risk_percent:.2f}% du capital)")
            print("-" * 37)
            
            # Affichage de la Taille de Position et de l'Exposition
            print(f" - Taille de Position (Unités) : {size:.8f} unités")
            print(f" - Valeur de la Position (Exposition) : {cap_pos:.2f} $")
            
            # Affichage du Levier
            if levier_min <= 1:
                print("\n ** AUCUN LEVIER NÉCESSAIRE **")
                print(f" La position est plus petite que votre capital ({cap_pos:.2f} $ < {capital:.2f} $).")
                print(" Vous pouvez utiliser un levier x1.")
            else:
                print("\n ** LEVIER NÉCESSAIRE (ANTI-'Not Enough Margin') **")
                print(f" La valeur de la position ({cap_pos:.2f} $) est supérieure à votre capital.")
                print(f" Marge théorique requise si levier x1 : {marge_req_theorique:.2f} $")
                print(f" Levier minimum requis : {levier_min}x")
                print(f" Utilisez un levier de {levier_min}x ou plus. (ex: x5, x10)")

            print("-" * 37)
            print(f" - Niveau de Take Profit (TP) : {tp:.3f} $")
            print(f" - Gain potentiel (à {rr:.1f}R) : {gain:.2f} $")
            print("--------------------------------------\n")
        
        # Demander si l'utilisateur veut continuer
        cont = input("Veux-tu calculer un autre trade ? (o/n) : ").lower()
        if cont != "o":
            print("Au plaisir ! Bonne gestion de risque. 🚀")
            sys.exit() 
            
    except ValueError:
        print("Erreur : Veuillez entrer un nombre valide pour chaque paramètre.\n")
    except Exception as e:
        print(f"Une erreur inattendue est survenue : {e}")
        sys.exit()
