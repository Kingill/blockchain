#!/usr/bin/env python3
"""
Script de diagnostic pour Hyperliquid SDK
Affiche constructeur et méthodes disponibles dans Exchange
"""

import inspect

try:
    from hyperliquid.exchange import Exchange
except ImportError:
    print("Erreur: hyperliquid.exchange introuvable. Vérifie que le SDK est installé.")
    exit(1)

print("=== Inspect Exchange class ===\n")

# Signature du constructeur
print("Constructor (__init__) signature:")
print(inspect.signature(Exchange.__init__))
print("\n")

# Méthodes publiques
print("Méthodes publiques disponibles :")
methods = [m for m in dir(Exchange) if not m.startswith("_")]
for m in methods:
    print("-", m)
print("\n")

# Attributs / propriétés publiques
print("Attributs / propriétés publiques :")
attrs = [a for a in dir(Exchange) if not a.startswith("_") and not callable(getattr(Exchange, a))]
for a in attrs:
    print("-", a)

# Bonus: help complet
#print("\n=== Help(Exchange) ===")
#help(Exchange)
