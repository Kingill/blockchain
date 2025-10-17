#!/bin/bash

# Nom du dossier pour l'environnement virtuel
VENV_NAME=".venv"

echo "Création de l'environnement virtuel local dans le dossier $VENV_NAME..."
# Crée un dossier env local
python3 -m venv $VENV_NAME

# Vérifie si la création a réussi
if [ $? -ne 0 ]; then
    echo "Erreur lors de la création de l'environnement virtuel. Assurez-vous que python3 et python3-venv (ou équivalent) sont installés."
    exit 1
fi

echo "Activation de l'environnement virtuel..."
# Active l’environnement virtuel (Note: l'activation dans le script ne persiste pas après l'exécution)
source $VENV_NAME/bin/activate

echo "Installation des dépendances Python..."

# Installation du SDK et des dépendances spécifiques
pip install hyperliquid-python-sdk \
            hyperliquid \
            eth-account \
            websockets \
            rich

# Affiche un message de fin
echo "✅ Environnement virtuel créé et dépendances installées."
echo "Pour l'utiliser, lancez: source $VENV_NAME/bin/activate"
