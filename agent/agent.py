"""
Agent minimal — un LLM local (Ollama) qui utilise un outil via MCP.
===================================================================

C'EST QUOI UN "AGENT" ICI ?
---------------------------
Un agent, dans sa forme la plus simple, c'est une boucle :

    question utilisateur
        → le LLM réfléchit
        → soit il répond directement,
        → soit il demande à appeler un OUTIL
            → on exécute l'outil (ici : via MCP)
            → on redonne le résultat au LLM
            → le LLM formule la réponse finale

Le point crucial à comprendre : LE LLM N'APPELLE JAMAIS L'OUTIL LUI-MÊME.
Il produit seulement un message structuré du genre « je voudrais appeler
get_weather avec ville="Paris" ». C'est CE programme (l'agent) qui :
  1. détecte cette demande,
  2. fait le vrai appel via le protocole MCP,
  3. renvoie le résultat au LLM pour qu'il termine sa réponse.

LES DEUX MONDES QUE CET AGENT RELIE
-----------------------------------
  - Côté MCP    : le serveur (mcp-server/server.py) expose ses outils
                  via JSON-RPC sur stdio.
  - Côté LLM    : Ollama fait tourner llama3.2 en local et supporte le
                  "tool calling" (le modèle sait produire des demandes
                  d'appel d'outil structurées).

L'agent traduit entre les deux : il convertit la description MCP des
outils au format attendu par Ollama, et inversement transmet les appels
du LLM vers MCP.

PRÉREQUIS : Ollama installé et le modèle téléchargé (ollama pull llama3.2).
LANCEMENT : python agent/agent.py   (depuis la racine du projet)
"""

import asyncio
import shutil
import sys

# --- Côté LLM : le client Python officiel d'Ollama -----------------------
import ollama

# --- Côté MCP : le SDK officiel, partie "client" -------------------------
# ClientSession        = gère la conversation JSON-RPC avec le serveur
#                        (initialize, tools/list, tools/call...)
# StdioServerParameters = décrit COMMENT lancer le serveur (quelle commande)
# stdio_client         = lance le serveur en sous-processus et branche
#                        ses stdin/stdout sur notre session
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Le modèle local à utiliser. llama3.2 (3B) est petit (~2 Go) et supporte
# le tool calling. Si vous avez plus de RAM, essayez "qwen2.5:7b".
MODELE = "llama3.2"


def outils_mcp_vers_ollama(outils_mcp):
    """Convertit la liste d'outils MCP au format attendu par Ollama.

    MCP et Ollama décrivent les outils presque pareil (tous deux basés sur
    JSON Schema), mais avec des enveloppes différentes :

      MCP (réponse à tools/list)        Ollama (paramètre tools=...)
      --------------------------        --------------------------------
      tool.name                    →    function.name
      tool.description             →    function.description
      tool.inputSchema             →    function.parameters
      (le JSON Schema des params)

    Cette traduction est LE rôle central d'un agent MCP : rendre les
    outils du serveur compréhensibles par le LLM.
    """
    outils_ollama = []
    for outil in outils_mcp:
        outils_ollama.append({
            "type": "function",
            "function": {
                "name": outil.name,
                "description": outil.description,
                # inputSchema est le JSON Schema généré côté serveur à
                # partir de la signature Python de la fonction décorée.
                "parameters": outil.inputSchema,
            },
        })
    return outils_ollama


async def boucle_agent(session: ClientSession, question: str):
    """Une passe complète de la boucle agentique pour une question."""

    # ÉTAPE A — Découverte des outils : requête MCP "tools/list".
    # Le serveur répond avec le nom, la description et le schéma des
    # paramètres de chaque outil. L'agent ne connaît RIEN à l'avance :
    # tout est découvert dynamiquement. C'est ça la force de MCP — on
    # pourrait brancher un autre serveur sans changer une ligne d'agent.
    reponse_liste = await session.list_tools()
    outils = outils_mcp_vers_ollama(reponse_liste.tools)
    print(f"[agent] Outils découverts via MCP : "
          f"{[o['function']['name'] for o in outils]}")

    # L'historique de conversation qu'on enverra au LLM. On y ajoutera
    # au fur et à mesure : la question, la demande d'outil du LLM, le
    # résultat de l'outil... Le LLM est "sans mémoire" : à chaque appel
    # il ne voit QUE cet historique.
    messages = [{"role": "user", "content": question}]

    # ÉTAPE B — Premier appel au LLM, en lui donnant la liste des outils.
    # Ollama injecte cette liste dans le prompt du modèle ; le modèle peut
    # alors répondre soit avec du texte, soit avec des "tool_calls".
    reponse = ollama.chat(model=MODELE, messages=messages, tools=outils)

    # ÉTAPE C — Le LLM a-t-il demandé un outil ?
    # On boucle car le modèle pourrait enchaîner plusieurs appels d'outils
    # avant de donner sa réponse finale (ici, en pratique, un seul suffit).
    while reponse.message.tool_calls:
        # On garde la demande du LLM dans l'historique : au prochain tour,
        # il doit "se souvenir" qu'il a demandé cet appel.
        messages.append(reponse.message)

        for appel in reponse.message.tool_calls:
            nom = appel.function.name
            arguments = appel.function.arguments  # déjà un dict Python
            print(f"[agent] Le LLM demande l'outil : {nom}({arguments})")

            # ÉTAPE D — L'appel MCP proprement dit : requête "tools/call".
            # Le SDK envoie au serveur (via stdout du sous-processus) :
            #   {"method": "tools/call",
            #    "params": {"name": "get_weather",
            #               "arguments": {"ville": "Paris"}}}
            # et le serveur exécute la fonction Python puis répond.
            resultat = await session.call_tool(nom, arguments=arguments)

            # Le résultat MCP est une liste de blocs "content" (car un
            # outil peut renvoyer du texte, des images...). Notre outil
            # ne renvoie que du texte : on concatène les blocs texte.
            texte = "".join(
                bloc.text for bloc in resultat.content
                if getattr(bloc, "type", None) == "text"
            )
            print(f"[agent] Réponse du serveur MCP : {texte}")

            # ÉTAPE E — On redonne ce résultat au LLM, avec le rôle
            # spécial "tool" : le modèle comprend alors « voici le retour
            # de l'outil que j'ai demandé » et peut formuler sa réponse.
            messages.append({"role": "tool", "content": texte,
                             "tool_name": nom})

        # On rappelle le LLM avec l'historique enrichi du résultat.
        reponse = ollama.chat(model=MODELE, messages=messages, tools=outils)

    # ÉTAPE F — Plus de tool_calls : le message est la réponse finale.
    print(f"\n[llm] {reponse.message.content}\n")


async def main():
    # Vérifications amicales avant de démarrer, pour des erreurs claires.
    if shutil.which("ollama") is None:
        sys.exit("Ollama n'est pas installé. Voir le README (section "
                 "Prérequis) : https://ollama.com/download")
    try:
        modeles_locaux = [m.model for m in ollama.list().models]
    except Exception:
        sys.exit("Impossible de contacter Ollama. Est-il démarré ? "
                 "Lancez `ollama serve` dans un autre terminal.")
    if not any(m.startswith(MODELE) for m in modeles_locaux):
        sys.exit(f"Le modèle {MODELE} n'est pas téléchargé. "
                 f"Lancez : ollama pull {MODELE}")

    # Description du serveur MCP à lancer : la commande et ses arguments.
    # L'agent va exécuter `python mcp-server/server.py` en sous-processus
    # et communiquer avec lui via ses stdin/stdout. C'est ça, le transport
    # "stdio" : le serveur n'existe que le temps de la session.
    parametres_serveur = StdioServerParameters(
        command=sys.executable,            # le même interpréteur Python
        args=["mcp-server/server.py"],     # chemin relatif à la racine
    )

    # stdio_client lance le sous-processus ; ClientSession ouvre ensuite
    # la conversation JSON-RPC par-dessus.
    async with stdio_client(parametres_serveur) as (lecture, ecriture):
        async with ClientSession(lecture, ecriture) as session:
            # La poignée de main MCP : requête "initialize". Client et
            # serveur échangent leurs versions du protocole et leurs
            # capacités. Obligatoire avant tout autre appel.
            await session.initialize()
            print("[agent] Connecté au serveur MCP (poignée de main OK).\n")

            # Petite boucle interactive : posez vos questions !
            print("Posez une question météo (ex: « Quel temps fait-il à "
                  "Paris ? »). Tapez « quit » pour sortir.\n")
            while True:
                question = input("Vous > ").strip()
                if question.lower() in ("quit", "exit", "q"):
                    break
                if question:
                    await boucle_agent(session, question)


if __name__ == "__main__":
    # Le SDK MCP est asynchrone (asyncio), d'où ce point d'entrée.
    asyncio.run(main())
