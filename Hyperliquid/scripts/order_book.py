import asyncio
import websockets
import json
import traceback
import sys

# --- Configuration ---
URI = "wss://api.hyperliquid.xyz/ws"
COIN_SYMBOL = "BTC"
# ---------------------

async def hyperliquid_l2book_stream_final_fix():
    """Se connecte au WebSocket d'Hyperliquid et affiche les mises à jour du carnet d'ordres."""
    print(f"Connexion au WebSocket : {URI}")
    websocket = None  # Initialiser la variable de connexion

    try:
        # Établir la connexion
        async with websockets.connect(URI) as websocket:
            print("Connexion établie. Envoi de la demande d'abonnement...")
            
            subscription_message = {
                "method": "subscribe",
                "subscription": {
                    "type": "l2Book",
                    "coin": COIN_SYMBOL
                }
            }
            await websocket.send(json.dumps(subscription_message))
            print(f"Abonné au carnet d'ordres (l2Book) pour : {COIN_SYMBOL}")
            print("-" * 60)

            # Boucle de réception des messages (la plus susceptible d'être interrompue)
            async for message in websocket:
                data = json.loads(message)

                if data.get("channel") == "l2Book":
                    book_data = data.get("data", {})
                    levels = book_data.get("levels", [[], []])
                    bids = levels[0]
                    asks = levels[1]
                    
                    # --- Extraction et conversion des métriques clés ---
                    
                    best_bid = None
                    if bids and isinstance(bids[0], dict):
                        try:
                            best_bid = float(bids[0]['px'])
                        except (ValueError, KeyError):
                            pass

                    best_ask = None
                    if asks and isinstance(asks[0], dict):
                        try:
                            best_ask = float(asks[0]['px'])
                        except (ValueError, KeyError):
                            pass
                    
                    mid_price = None
                    if best_bid is not None and best_ask is not None:
                        mid_price = (best_bid + best_ask) / 2.0
                    
                    # --- Affichage des données ---
                    
                    mid_str = f"| Mid: {mid_price:.1f}" if mid_price else ""
                    print(f"--- {COIN_SYMBOL} @ {book_data.get('time')} ---")
                    print(f"Best Bid: {best_bid} | Best Ask: {best_ask} {mid_str}")
                    
                    print("\nVentes (Asks) :")
                    for item in asks[:5]:
                        if isinstance(item, dict) and 'px' in item and 'sz' in item:
                            try:
                                price, size = float(item['px']), float(item['sz'])
                                print(f"  Prix: {price:<10.1f} | Taille: {size:.5f}")
                            except ValueError:
                                print(f"  ATTENTION: Niveau de Vente (Ask) ignoré, donnée non numérique: {item}")
                            
                    print("Achats (Bids) :")
                    for item in bids[:5]:
                        if isinstance(item, dict) and 'px' in item and 'sz' in item:
                            try:
                                price, size = float(item['px']), float(item['sz'])
                                print(f"  Prix: {price:<10.1f} | Taille: {size:.5f}")
                            except ValueError:
                                print(f"  ATTENTION: Niveau d'Achat (Bid) ignoré, donnée non numérique: {item}")
                            
                    print("-" * 60)

    except websockets.exceptions.ConnectionClosed as e:
        print(f"\n❌ Erreur de Connexion: Connexion fermée. Code: {e.code}, Raison: {e.reason}")
    except Exception as e:
        print(f"\n❌ Une erreur inattendue est survenue: {e}")
        traceback.print_exc()

# --- Bloc principal pour gérer l'interruption clavier ---
if __name__ == "__main__":
    try:
        asyncio.run(hyperliquid_l2book_stream_final_fix())
    except KeyboardInterrupt:
        # Ceci capture Ctrl+C
        print("\n\n✅ Interruption par l'utilisateur (Ctrl+C). Fermeture propre du programme.")
    except RuntimeError as e:
        # Gérer les erreurs courantes d'exécution asynchrone après Ctrl+C
        if "was never retrieved" in str(e):
            print("\n\n✅ Interruption par l'utilisateur (Ctrl+C). Fermeture propre du programme.")
        else:
            raise
