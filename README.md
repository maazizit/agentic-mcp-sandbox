# agentic-mcp-sandbox

Bac à sable **pédagogique et jetable** pour comprendre le protocole
[MCP (Model Context Protocol)](https://modelcontextprotocol.io) : un seul
serveur MCP qui expose une météo factice, un seul agent qui l'interroge,
et un LLM 100 % local via [Ollama](https://ollama.com). Pas de cloud, pas
d'API payante, pas de complexité inutile.

> **Débutant·e sur MCP ?** Commence par [TUTORIEL.md](TUTORIEL.md) : il
> explique de zéro ce qu'est un LLM, un agent, MCP, et montre message par
> message ce qui circule entre eux. Puis reviens ici pour lancer le projet.

## L'idée en une image

```
┌─────────────┐   question    ┌──────────────────┐   JSON-RPC (stdio)   ┌──────────────────┐
│ Vous        │ ────────────► │ agent/agent.py   │ ◄──────────────────► │ mcp-server/      │
│ (terminal)  │ ◄──────────── │ (le client MCP)  │  initialize          │   server.py      │
└─────────────┘   réponse     │        ▲         │  tools/list          │ (le serveur MCP) │
                              │        │ HTTP    │  tools/call          │                  │
                              │        ▼ local   │                      │  @mcp.tool()     │
                              │ Ollama (llama3.2)│                      │  get_weather()   │
                              └──────────────────┘                      └──────────────────┘
```

- **Le serveur MCP** ([mcp-server/server.py](mcp-server/server.py)) expose
  un unique outil `get_weather(ville)` qui renvoie une météo inventée. Il
  ne sait rien du LLM : il déclare son outil et répond quand on l'appelle.
- **L'agent** ([agent/agent.py](agent/agent.py)) lance le serveur en
  sous-processus, découvre ses outils via MCP, les présente au LLM local,
  et exécute les appels d'outils que le LLM demande.
- **Le LLM** (llama3.2 via Ollama) décide *quand* utiliser l'outil et
  formule la réponse finale. Il ne fait que produire des messages : c'est
  l'agent qui exécute réellement les appels.

Le code est volontairement très commenté : chaque étape du protocole
(`initialize`, `tools/list`, `tools/call`, format des réponses) est
expliquée là où elle se produit. **Lisez les deux fichiers Python, c'est
là qu'est le cours.**

## Prérequis

1. **Python 3.10+** (le SDK `mcp` l'exige)

2. **Ollama** — le moteur qui fait tourner le LLM en local :
   - Linux : `curl -fsSL https://ollama.com/install.sh | sh`
   - macOS / Windows : installeur sur <https://ollama.com/download>

3. **Le modèle llama3.2** (~2 Go, tourne sans GPU sur une machine standard,
   et surtout : il supporte le *tool calling*, indispensable ici) :

   ```bash
   ollama pull llama3.2
   ```

## Installation

```bash
git clone https://github.com/maazizit/agentic-mcp-sandbox.git
cd agentic-mcp-sandbox

# Environnement virtuel recommandé
python3 -m venv .venv
source .venv/bin/activate        # Windows : .venv\Scripts\activate

pip install -r requirements.txt
```

## Lancement

Il n'y a **qu'une seule commande** : on ne lance pas le serveur à la main,
c'est l'agent qui le démarre comme sous-processus (transport stdio).

```bash
# Si Ollama ne tourne pas déjà en tâche de fond : `ollama serve` dans
# un autre terminal (souvent inutile, l'installeur crée un service).

python agent/agent.py
```

Puis posez une question :

```
Vous > Quel temps fait-il à Paris ?
[agent] Outils découverts via MCP : ['get_weather']
[agent] Le LLM demande l'outil : get_weather({'ville': 'Paris'})
[agent] Réponse du serveur MCP : À Paris : nuageux, 12°C (données factices).

[llm] Il fait actuellement nuageux à Paris, avec une température de 12°C.
```

## Ce qui se passe sous le capot, étape par étape

1. **Lancement du serveur** — l'agent exécute
   `python mcp-server/server.py` en sous-processus. Ils communiquent en
   JSON-RPC via stdin/stdout du sous-processus : c'est le transport
   **stdio**, le plus simple de MCP (pas de HTTP, pas de port).

2. **`initialize`** — la poignée de main : client et serveur échangent
   leurs versions du protocole et leurs capacités ("je propose des tools").

3. **`tools/list`** — l'agent demande la liste des outils. Le serveur
   répond avec, pour chaque outil : son nom, sa description (tirée de la
   docstring Python !) et un JSON Schema de ses paramètres (généré depuis
   les annotations de type). L'agent ne connaît rien à l'avance — tout
   est découvert dynamiquement.

4. **Traduction MCP → Ollama** — l'agent convertit ces descriptions au
   format `tools` d'Ollama et les joint à la question envoyée au LLM.

5. **Le LLM décide** — llama3.2 voit la question et la liste d'outils.
   S'il juge l'outil utile, il ne répond pas du texte mais un
   `tool_call` structuré : `get_weather(ville="Paris")`.

6. **`tools/call`** — l'agent exécute la demande via MCP. Le serveur
   lance la fonction Python et renvoie le résultat dans un bloc
   `content` de type texte.

7. **Réponse finale** — l'agent renvoie ce résultat au LLM (rôle
   `tool` dans l'historique), qui formule alors la réponse en langage
   naturel.

## Structure du projet

```
agentic-mcp-sandbox/
├── mcp-server/
│   └── server.py        # Le serveur MCP (FastMCP) : 1 outil, get_weather
├── agent/
│   └── agent.py         # L'agent : client MCP + boucle avec Ollama
├── README.md            # Ce fichier
├── requirements.txt     # 2 dépendances : mcp, ollama
└── LICENSE              # MIT
```

## Pour aller plus loin (exercices)

- Ajoutez un **deuxième outil** au serveur (ex. `get_time(fuseau)`) — vous
  verrez que l'agent le découvre tout seul, sans modification.
- Remplacez la météo factice par la **lecture d'un fichier texte local**
  pour voir un outil qui touche vraiment à votre machine.
- Mettez un `print(..., file=sys.stderr)` dans le serveur pour observer
  quand il est réellement appelé (jamais `print()` tout court : stdout
  est réservé au protocole !).
- Essayez un autre modèle : `ollama pull qwen2.5:7b` puis changez
  `MODELE` dans `agent/agent.py` — souvent plus fiable sur les appels
  d'outils si votre machine a assez de RAM (~5 Go).
