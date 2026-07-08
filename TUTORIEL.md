# Tutoriel : LLM, agent, MCP — qui parle à qui, et comment

Ce tutoriel part de zéro. Il explique les trois briques du projet (le LLM,
l'agent, le serveur MCP), puis montre **message par message** ce qui circule
entre elles quand tu tapes « Quel temps fait-il à Paris ? ».

Prends ton temps, lis dans l'ordre, et garde `agent/agent.py` et
`mcp-server/server.py` ouverts à côté : chaque section pointe vers le code.

---

## 1. Les trois briques (vue d'ensemble)

Imagine un restaurant :

| Rôle au restaurant | Dans ce projet | Fichier |
|---|---|---|
| **Le client** qui commande | Toi, dans le terminal | — |
| **Le serveur** (la personne) qui prend la commande, va en cuisine, rapporte le plat | **L'agent** | `agent/agent.py` |
| **Le chef** qui réfléchit et décide quoi faire | **Le LLM** (llama3.2 via Ollama) | tourne dans Ollama |
| **La cuisine** avec ses équipements | **Le serveur MCP** et son outil `get_weather` | `mcp-server/server.py` |

Le point le plus important de tout ce tutoriel :

> **Le LLM ne peut RIEN faire d'autre que produire du texte.** Il ne peut pas
> ouvrir un fichier, appeler une API, ni exécuter du code. Quand on dit qu'un
> LLM « utilise un outil », en réalité il *écrit une demande* (« j'aimerais
> qu'on appelle get_weather avec ville=Paris ») et c'est **l'agent** — un
> programme Python ordinaire — qui exécute vraiment l'appel et lui rapporte
> le résultat.

Si tu retiens ça, tu as compris 50 % du sujet.

---

## 2. Brique n°1 : le LLM (llama3.2 via Ollama)

### C'est quoi ?

Un LLM (Large Language Model) est un modèle qui, étant donné un texte
d'entrée (le « prompt »), prédit la suite la plus plausible, mot par mot.
ChatGPT, Claude, Llama... même principe.

**Ollama** est un logiciel qui fait tourner des LLM *sur ta machine* :
il télécharge les modèles (`ollama pull llama3.2`), les charge en mémoire,
et expose une petite API HTTP locale (sur `http://localhost:11434`) pour
leur parler. La bibliothèque Python `ollama` que l'agent utilise ne fait
qu'envoyer des requêtes HTTP à cette API locale.

### Deux propriétés à bien comprendre

**a) Le LLM est sans mémoire (« stateless »).** Il ne se souvient de rien
entre deux appels. Si tu veux qu'il « se souvienne » de la conversation, tu
dois lui renvoyer TOUT l'historique à chaque appel. C'est exactement ce que
fait la liste `messages` dans `agent.py` : on y empile la question, la
demande d'outil, le résultat de l'outil... et on renvoie le tout à chaque
tour.

**b) Le LLM ne produit que du texte structuré.** Le « tool calling » (section
suivante) n'est qu'une convention de format sur ce texte.

### Le tool calling

Les modèles récents (llama3.2, qwen2.5, mistral...) ont été entraînés à un
format spécial : si on leur fournit une liste d'outils disponibles, ils
peuvent répondre non pas avec une phrase, mais avec un **appel d'outil
structuré**. Concrètement, quand l'agent envoie à Ollama :

```python
ollama.chat(
    model="llama3.2",
    messages=[{"role": "user", "content": "Quel temps fait-il à Paris ?"}],
    tools=[...la description de get_weather...],
)
```

le modèle peut répondre deux choses :

- **Du texte normal** : `message.content = "Bonjour ! ..."` — quand il n'a
  pas besoin d'outil ;
- **Une demande d'outil** : `message.tool_calls = [get_weather(ville="Paris")]`
  — quand il juge qu'un outil l'aiderait.

C'est le modèle qui *décide* (grâce à la description de l'outil qu'on lui a
fournie), mais c'est l'agent qui *exécute*.

---

## 3. Brique n°2 : le serveur MCP

### Le problème que MCP résout

Avant MCP, chaque application d'IA inventait sa propre façon de brancher des
outils : un format pour ChatGPT, un autre pour Claude, un autre pour chaque
framework... Si tu écrivais un outil « lire mes mails », il fallait le
réécrire pour chaque écosystème.

**MCP (Model Context Protocol)** est un standard ouvert (créé par Anthropic
fin 2024) qui dit : « voici LE format universel pour qu'un programme expose
des outils à un LLM ». Comme USB : peu importe la marque du clavier et de
l'ordinateur, la prise est la même.

- Un **serveur MCP** = un programme qui expose des outils (et éventuellement
  des ressources et des prompts — on n'utilise que les outils ici).
- Un **client MCP** = le programme qui s'y connecte et appelle ces outils.
  Dans ce projet, le client est *dans* l'agent.

Conséquence géniale : notre `server.py` météo pourrait être branché tel quel
sur Claude Desktop, Cursor, ou n'importe quel client MCP — sans changer une
ligne. Et notre agent pourrait se connecter à n'importe quel serveur MCP
existant (il y en a des centaines : GitHub, bases de données, navigateurs...).

### Comment le serveur et le client se parlent : JSON-RPC sur stdio

Deux notions :

**JSON-RPC** : un format tout simple pour dire « appelle telle méthode avec
tels paramètres » en JSON. Une requête ressemble à :

```json
{"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
```

et la réponse reprend le même `id` pour qu'on sache à quelle question elle
répond.

**stdio (standard input/output)** : le canal de communication. L'agent lance
`python mcp-server/server.py` comme **sous-processus**, puis :
- ce que l'agent écrit sur le **stdin** du sous-processus = les requêtes ;
- ce que le serveur écrit sur son **stdout** = les réponses.

Pas de réseau, pas de port, pas de HTTP. Le serveur n'existe que le temps de
la session : quand l'agent se ferme, le serveur meurt avec lui. (MCP propose
aussi un transport HTTP pour les serveurs distants — même protocole, autre
tuyau.)

> C'est pour ça qu'on ne met jamais de `print()` dans un serveur MCP stdio :
> stdout est réservé aux messages du protocole. Un `print()` au milieu
> casserait le dialogue JSON. Pour déboguer : `print(..., file=sys.stderr)`.

### Les trois méthodes du protocole utilisées ici

| Méthode | Qui l'envoie | À quoi ça sert |
|---|---|---|
| `initialize` | client → serveur | Poignée de main : versions du protocole, capacités (« je propose des tools ») |
| `tools/list` | client → serveur | « Quels outils as-tu ? » → nom, description, schéma des paramètres |
| `tools/call` | client → serveur | « Exécute tel outil avec tels arguments » → résultat |

Dans `server.py`, tu n'écris aucune de ces méthodes toi-même : le SDK
(`FastMCP`) les gère. Toi tu écris juste une fonction Python décorée avec
`@mcp.tool()`, et le SDK fabrique automatiquement la réponse à `tools/list`
à partir de :
- le **nom de la fonction** → le nom de l'outil ;
- la **docstring** → la description (celle que lira le LLM !) ;
- les **annotations de type** (`ville: str`) → un **JSON Schema** des
  paramètres, c'est-à-dire une description formelle : « cet outil prend un
  objet avec une propriété `ville` de type string, obligatoire ».

---

## 4. Brique n°3 : l'agent — le chef d'orchestre

L'agent (`agent/agent.py`) est le seul programme qui parle aux deux mondes :

```
            (bibliothèque ollama,            (SDK mcp, JSON-RPC
             HTTP local)                      sur stdio)
   LLM  ◄──────────────────►  AGENT  ◄──────────────────►  SERVEUR MCP
 llama3.2                   agent.py                       server.py
```

Son travail, dans l'ordre :

1. **Lancer** le serveur MCP en sous-processus et faire la poignée de main
   (`initialize`).
2. **Découvrir** les outils (`tools/list`). L'agent ne connaît rien à
   l'avance — tu pourrais ajouter un outil au serveur, il le découvrirait
   tout seul.
3. **Traduire** la description MCP des outils vers le format d'Ollama
   (fonction `outils_mcp_vers_ollama` — regarde-la : c'est presque du
   copier-coller de champs, car les deux formats reposent sur JSON Schema).
4. **Interroger le LLM** avec ta question + la liste d'outils.
5. Si le LLM répond par un `tool_call` : **exécuter** l'appel via MCP
   (`tools/call`), ajouter le résultat à l'historique avec le rôle `"tool"`,
   et **rappeler le LLM**.
6. Quand le LLM répond du texte sans tool_call : c'est la **réponse finale**,
   on te l'affiche.

Cette boucle « LLM → outil → LLM → ... jusqu'à réponse finale » s'appelle la
**boucle agentique**. Tous les agents, même les plus sophistiqués (Claude
Code inclus !), sont cette même boucle avec plus d'outils et plus de
garde-fous.

---

## 5. Le film complet, message par message

Voici TOUT ce qui circule quand tu tapes « Quel temps fait-il à Paris ? ».
(JSON simplifié pour la lisibilité.)

**① Toi → Agent** (via `input()` dans le terminal)

```
Quel temps fait-il à Paris ?
```

**② Agent → Serveur MCP** — au démarrage, poignée de main puis découverte :

```json
{"method": "initialize", "params": {"protocolVersion": "...", "clientInfo": {...}}}
{"method": "tools/list"}
```

**③ Serveur MCP → Agent** — « voici mon outil » :

```json
{"result": {"tools": [{
  "name": "get_weather",
  "description": "Retourne la météo actuelle pour une ville donnée.",
  "inputSchema": {
    "type": "object",
    "properties": {"ville": {"type": "string"}},
    "required": ["ville"]
  }
}]}}
```

**④ Agent → LLM** (via l'API locale d'Ollama) — question + outils traduits :

```json
{
  "model": "llama3.2",
  "messages": [{"role": "user", "content": "Quel temps fait-il à Paris ?"}],
  "tools": [{"type": "function", "function": {
    "name": "get_weather",
    "description": "Retourne la météo actuelle pour une ville donnée.",
    "parameters": {"type": "object", "properties": {"ville": {"type": "string"}}, "required": ["ville"]}
  }}]
}
```

**⑤ LLM → Agent** — le modèle décide d'utiliser l'outil. Il ne l'exécute
pas : il *demande* :

```json
{"message": {"role": "assistant", "tool_calls": [
  {"function": {"name": "get_weather", "arguments": {"ville": "Paris"}}}
]}}
```

**⑥ Agent → Serveur MCP** — l'agent exécute la demande, via le protocole :

```json
{"method": "tools/call", "params": {"name": "get_weather", "arguments": {"ville": "Paris"}}}
```

**⑦ Serveur MCP → Agent** — la fonction Python a tourné, voici le résultat :

```json
{"result": {"content": [
  {"type": "text", "text": "À Paris : neigeux, 2°C (données factices)."}
]}}
```

(Le résultat est une **liste de blocs** `content` car un outil pourrait
renvoyer du texte ET des images, etc. Ici, un seul bloc texte.)

**⑧ Agent → LLM** — deuxième appel, avec l'historique complet : la question,
la demande d'outil du LLM (pour qu'il « se souvienne » de l'avoir faite), et
le résultat avec le rôle `"tool"` :

```json
{"messages": [
  {"role": "user", "content": "Quel temps fait-il à Paris ?"},
  {"role": "assistant", "tool_calls": [{"function": {"name": "get_weather", "arguments": {"ville": "Paris"}}}]},
  {"role": "tool", "tool_name": "get_weather", "content": "À Paris : neigeux, 2°C (données factices)."}
]}
```

**⑨ LLM → Agent** — cette fois, plus de tool_call, juste du texte :

```json
{"message": {"role": "assistant", "content": "Il neige actuellement à Paris, avec une température de 2°C."}}
```

**⑩ Agent → Toi** : l'agent affiche cette réponse. Fin de la boucle.

Retiens la symétrie : **④→⑤** le LLM reçoit la question et demande un outil ;
**⑥→⑦** l'agent exécute via MCP ; **⑧→⑨** le LLM reçoit le résultat et
formule la réponse. Deux appels au LLM, un appel MCP.

---

## 6. Glossaire

| Terme | Définition courte |
|---|---|
| **LLM** | Modèle de langage : prédit du texte. Ne peut rien exécuter. |
| **Ollama** | Logiciel qui fait tourner des LLM en local et les expose via une API HTTP locale. |
| **Tool calling** | Capacité d'un LLM à répondre par une demande d'appel d'outil structurée au lieu de texte. |
| **Agent** | Programme qui boucle : question → LLM → exécution des outils demandés → LLM → réponse. |
| **MCP** | Standard ouvert pour exposer des outils (et ressources/prompts) à des LLM. |
| **Serveur MCP** | Programme qui expose des outils au format MCP. |
| **Client MCP** | Programme qui se connecte à un serveur MCP (ici : intégré à l'agent). |
| **JSON-RPC** | Format de messages « appelle telle méthode avec tels params » en JSON. |
| **stdio** | Transport : le client lance le serveur en sous-processus et lui parle via stdin/stdout. |
| **JSON Schema** | Description formelle des paramètres d'un outil (types, champs obligatoires...). |
| **initialize / tools/list / tools/call** | Les trois méthodes MCP utilisées ici : poignée de main, découverte, exécution. |
| **Docstring** | Le texte sous `def ...` en Python — devient la description de l'outil que lit le LLM. |
| **Stateless** | Sans mémoire : le LLM ne voit que ce qu'on lui renvoie à chaque appel. |

---

## 7. Exercices pour ancrer tout ça (dans l'ordre)

**Niveau 1 — Observer**
1. Lance `python agent/agent.py`, pose plusieurs questions, et relie chaque
   ligne `[agent] ...` affichée aux étapes ⑤, ⑥, ⑦ du film ci-dessus.
2. Pose une question SANS rapport avec la météo (« raconte-moi une blague »).
   Observe : pas de tool_call, le LLM répond directement. C'est lui qui
   décide, selon la description de l'outil.

**Niveau 2 — Modifier le serveur**
3. Dans `server.py`, change la docstring de `get_weather` en quelque chose de
   trompeur (« Retourne le prix d'une action en bourse »). Relance et demande
   la météo : le LLM utilise-t-il encore l'outil ? Tu viens de vérifier que
   **le LLM choisit ses outils uniquement d'après leur description**.
   (Remets la vraie docstring après !)
4. Ajoute un **deuxième outil** au serveur, par exemple :
   ```python
   @mcp.tool()
   def get_heure() -> str:
       """Retourne l'heure actuelle."""
       from datetime import datetime
       return datetime.now().strftime("%H:%M")
   ```
   Relance l'agent **sans le modifier** : il découvre le nouvel outil tout
   seul (regarde la ligne « Outils découverts »). C'est la découverte
   dynamique via `tools/list`.

**Niveau 3 — Voir le protocole à nu**
5. Dans `server.py`, ajoute en haut `import sys` puis dans `get_weather` :
   `print(f"[serveur] appelé avec ville={ville}", file=sys.stderr)`.
   Tu verras exactement quand le serveur est sollicité. (Essaie ensuite avec
   `print()` tout court pour voir le dialogue se casser — puis remets stderr.)
6. Question à te poser devant le code : pourquoi `messages.append(...)` deux
   fois dans `boucle_agent` (la demande du LLM PUIS le résultat de l'outil) ?
   Réponse dans la section 2.a : le LLM est stateless — au second appel, il
   doit revoir tout le film pour savoir où il en est.

**Niveau 4 — Aller plus loin**
7. Remplace la météo factice par un outil `read_file(chemin)` qui lit un
   vrai fichier texte. Tu toucheras du doigt pourquoi les permissions et la
   sécurité deviennent vite un sujet avec les agents.
8. Fais pointer un autre client MCP (par ex. Claude Desktop, section
   « Developer » de ses réglages) vers ton `server.py` : le même serveur,
   sans modification, servira un autre LLM. C'est toute la promesse de MCP.

---

## 8. Pour continuer

- La spécification et les guides officiels : <https://modelcontextprotocol.io>
- Le SDK Python utilisé ici : <https://github.com/modelcontextprotocol/python-sdk>
- Le tool calling côté Ollama : <https://ollama.com/blog/tool-support>
- Des centaines de serveurs MCP existants à explorer :
  <https://github.com/modelcontextprotocol/servers>
