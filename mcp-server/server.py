"""
Serveur MCP minimal — expose UN SEUL outil : la météo réelle.
================================================================

C'EST QUOI UN SERVEUR MCP ?
---------------------------
MCP (Model Context Protocol) est un protocole standardisé qui permet à un
LLM (via un "client" MCP, souvent un agent) de découvrir et d'appeler des
outils exposés par un "serveur" MCP.

L'idée clé : le serveur ne sait RIEN du LLM. Il se contente de déclarer
« voici mes outils, voici leurs paramètres » et de répondre quand on les
appelle. C'est le client/agent qui fait le lien avec le LLM.

COMMENT ÇA COMMUNIQUE ?
-----------------------
Ici on utilise le transport "stdio" : le serveur lit des messages JSON-RPC
sur son entrée standard (stdin) et répond sur sa sortie standard (stdout).
Concrètement, l'agent lance ce fichier comme un sous-processus et lui
"parle" en JSON. Pas de HTTP, pas de port réseau — c'est le mode le plus
simple, parfait pour apprendre.

Le dialogue JSON-RPC ressemble à ça (géré automatiquement par le SDK) :

  1. Client → Serveur : {"method": "initialize", ...}
     → poignée de main : chacun annonce sa version du protocole et ses
       capacités (le serveur dit "je propose des tools").

  2. Client → Serveur : {"method": "tools/list"}
     → le serveur répond avec la liste de ses outils : nom, description,
       et un JSON Schema décrivant les paramètres attendus.
       C'est CE schéma que l'agent transmettra au LLM pour qu'il sache
       quel outil existe et comment l'appeler.

  3. Client → Serveur : {"method": "tools/call",
                         "params": {"name": "get_weather",
                                    "arguments": {"ville": "Paris"}}}
     → le serveur exécute la fonction Python correspondante et renvoie
       le résultat sous forme de "content" (ici du texte).

LE SDK OFFICIEL (FastMCP)
-------------------------
Écrire ce JSON-RPC à la main serait laborieux. Le SDK Python officiel
(`pip install mcp`) fournit `FastMCP`, qui fait tout le travail :
  - le décorateur @mcp.tool() enregistre une fonction comme outil MCP ;
  - la signature Python (types + docstring) est convertie AUTOMATIQUEMENT
    en JSON Schema pour la réponse à tools/list ;
  - mcp.run() démarre la boucle qui lit stdin / écrit stdout.
"""

import requests

# FastMCP est la façon "haut niveau" d'écrire un serveur MCP en Python.
from mcp.server.fastmcp import FastMCP

# On crée le serveur en lui donnant un nom. Ce nom est envoyé au client
# pendant la poignée de main "initialize" — il sert à identifier le serveur
# (utile quand un agent est connecté à plusieurs serveurs à la fois).
mcp = FastMCP("meteo-reelle")

# Open-Meteo (https://open-meteo.com) : API météo gratuite, sans clé
# d'API et sans inscription — parfaite pour ce bac à sable. On l'appelle
# en deux temps :
#   1. le géocodage : transformer un nom de ville ("Paris") en coordonnées
#      GPS (latitude/longitude), car l'API météo ne comprend pas les noms
#      de ville, seulement des coordonnées ;
#   2. la prévision : donner ces coordonnées pour obtenir la météo actuelle.
URL_GEOCODAGE = "https://geocoding-api.open-meteo.com/v1/search"
URL_METEO = "https://api.open-meteo.com/v1/forecast"

# Open-Meteo renvoie un "code météo" numérique (norme WMO), pas un texte.
# On traduit les codes les plus courants en français. Liste complète :
# https://open-meteo.com/en/docs#weathervariables
CODES_METEO = {
    0: "ciel dégagé",
    1: "plutôt dégagé",
    2: "partiellement nuageux",
    3: "couvert",
    45: "brouillard",
    48: "brouillard givrant",
    51: "bruine légère",
    53: "bruine modérée",
    55: "bruine dense",
    61: "pluie légère",
    63: "pluie modérée",
    65: "forte pluie",
    71: "neige légère",
    73: "neige modérée",
    75: "forte neige",
    80: "averses",
    95: "orage",
}


# Le décorateur @mcp.tool() transforme cette simple fonction Python en
# outil MCP. Sous le capot, le SDK :
#   - prend le NOM de la fonction  → devient le nom de l'outil ("get_weather")
#   - prend la DOCSTRING           → devient la description de l'outil
#     (c'est ce texte que lira le LLM pour décider QUAND utiliser l'outil,
#      donc il doit être clair et précis !)
#   - prend les ANNOTATIONS de type (ville: str) → deviennent le JSON Schema
#     des paramètres : {"type": "object",
#                       "properties": {"ville": {"type": "string"}},
#                       "required": ["ville"]}
@mcp.tool()
def get_weather(ville: str) -> str:
    """Retourne la météo actuelle réelle pour une ville donnée.

    Args:
        ville: Le nom de la ville (par exemple "Paris" ou "Tokyo").
    """
    # ÉTAPE 1 — Géocodage : convertir "Paris" en coordonnées GPS.
    # Un vrai appel réseau, comme n'importe quel outil ferait pour aller
    # chercher une donnée réelle (fichier, base de données, autre API...).
    reponse_geo = requests.get(
        URL_GEOCODAGE,
        params={"name": ville, "count": 1, "language": "fr", "format": "json"},
        timeout=10,
    )
    reponse_geo.raise_for_status()
    resultats = reponse_geo.json().get("results")

    if not resultats:
        # Une ville introuvable n'est pas un bug : c'est un résultat valide
        # que le LLM doit pouvoir lire et expliquer à l'utilisateur.
        return f"Ville « {ville} » introuvable."

    lieu = resultats[0]
    latitude, longitude = lieu["latitude"], lieu["longitude"]
    nom_trouve = lieu["name"]

    # ÉTAPE 2 — Prévision : demander la météo actuelle à ces coordonnées.
    reponse_meteo = requests.get(
        URL_METEO,
        params={
            "latitude": latitude,
            "longitude": longitude,
            "current": "temperature_2m,weather_code",
            "timezone": "auto",
        },
        timeout=10,
    )
    reponse_meteo.raise_for_status()
    actuel = reponse_meteo.json()["current"]

    temperature = actuel["temperature_2m"]
    code = actuel["weather_code"]
    description = CODES_METEO.get(code, f"code météo {code}")

    # La valeur de retour : on renvoie une simple chaîne de caractères.
    # Le SDK l'emballe automatiquement dans le format de réponse MCP :
    #   {"content": [{"type": "text", "text": "..."}]}
    # MCP impose ce format "content" car un outil pourrait aussi renvoyer
    # des images ou d'autres types de contenu — ici, juste du texte.
    return f"À {nom_trouve} : {description}, {temperature}°C (source : open-meteo.com)."


if __name__ == "__main__":
    # Démarre le serveur en mode stdio : il attend maintenant les messages
    # JSON-RPC du client sur stdin et répondra sur stdout.
    #
    # IMPORTANT : c'est pour ça qu'on ne met JAMAIS de print() dans un
    # serveur MCP stdio — stdout est réservé au protocole ! Pour déboguer,
    # il faudrait écrire sur stderr (par ex. print(..., file=sys.stderr)).
    #
    # Note : on ne lance pas ce fichier à la main. C'est l'agent qui le
    # démarre comme sous-processus (voir agent/agent.py).
    mcp.run(transport="stdio")
