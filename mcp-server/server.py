"""
Serveur MCP minimal — expose UN SEUL outil : une météo factice.
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

import random

# FastMCP est la façon "haut niveau" d'écrire un serveur MCP en Python.
from mcp.server.fastmcp import FastMCP

# On crée le serveur en lui donnant un nom. Ce nom est envoyé au client
# pendant la poignée de main "initialize" — il sert à identifier le serveur
# (utile quand un agent est connecté à plusieurs serveurs à la fois).
mcp = FastMCP("meteo-factice")


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
    """Retourne la météo actuelle pour une ville donnée.

    Args:
        ville: Le nom de la ville (par exemple "Paris" ou "Tokyo").
    """
    # Données 100% factices : l'objectif est de comprendre le protocole,
    # pas de faire de la vraie météo. On utilise un tirage aléatoire mais
    # "seedé" avec le nom de la ville pour qu'une même ville donne toujours
    # la même réponse (plus facile à déboguer).
    rng = random.Random(ville.lower())
    # Chaque condition a sa plage de températures plausible, pour éviter
    # les absurdités du genre "neigeux, 26°C".
    conditions = {
        "ensoleillé": (18, 35),
        "nuageux": (8, 22),
        "pluvieux": (5, 18),
        "neigeux": (-5, 2),
        "venteux": (5, 20),
    }
    meteo = rng.choice(list(conditions))
    temperature = rng.randint(*conditions[meteo])

    # La valeur de retour : on renvoie une simple chaîne de caractères.
    # Le SDK l'emballe automatiquement dans le format de réponse MCP :
    #   {"content": [{"type": "text", "text": "..."}]}
    # MCP impose ce format "content" car un outil pourrait aussi renvoyer
    # des images ou d'autres types de contenu — ici, juste du texte.
    return f"À {ville} : {meteo}, {temperature}°C (données factices)."


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
