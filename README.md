# Ruche — messagerie pair-à-pair

Application de discussion **décentralisée** : chat texte, partage d'images et de
vidéos, et appels de groupe avec partage d'écran. Pas de serveur central : les
participants se relient directement entre eux, et le rôle d'**hôte migre
automatiquement** vers un autre participant si l'hôte se déconnecte.

> Interface graphique native **Python 3.13** (PySide6) — pas de navigateur.
> Langue de l'interface : **français**.

---

## État actuel

| Fonctionnalité | État |
|---|---|
| Interface graphique (pseudo, salons, membres, chat) | ✅ |
| Maillage WebRTC pair-à-pair (canal de données) | ✅ |
| Chat texte temps réel | ✅ |
| Historique local + réplication entre pairs | ✅ |
| Élection d'hôte et **bascule automatique** | ✅ |
| Serveur de rendez-vous sans état | ✅ |
| Envoi d'images / vidéos (pair-à-pair, vérifié SHA-256) | ✅ |
| Appels audio/vidéo de groupe | ✅ |
| Partage d'écran | ✅ |
| Découverte locale mDNS (sans serveur) | ✅ |
| Relais TURN configurable | ✅ |
| Empaquetage en application autonome | ✅ |

Toutes les phases de la feuille de route sont réalisées (voir plus bas).

---

## Installation

### Windows — installateur ou winget

Téléchargez **`Ruche-Setup-0.1.0.exe`** depuis la page des versions :

<https://github.com/hugo94z/ruche/releases/latest>

L'installation se fait **par utilisateur** (aucun droit administrateur requis),
dans `%LOCALAPPDATA%\Programs\Ruche`.

Une fois le paquet accepté dans le catalogue winget :

```powershell
winget install Ruche.Ruche
```

### Depuis les sources

Python **3.13** est requis.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Lancer l'application

**1. Démarrer un serveur de rendez-vous** (une seule fois, sur n'importe quelle
machine joignable par les participants — pas forcément la vôtre) :

```powershell
.\.venv\Scripts\python.exe run_rendezvous.py --host 0.0.0.0 --port 8765
```

**2. Lancer l'application**, sur chaque poste :

```powershell
.\.venv\Scripts\python.exe run.py
```

Dans la fenêtre : saisissez un **pseudo**, un **code de salon** (bouton
« Nouveau salon » pour en générer un), puis **Rejoindre**. L'adresse du serveur
de rendez-vous se règle dans le champ prévu à cet effet.

Pour tester **deux instances sur une même machine**, donnez-leur des dossiers de
données distincts (sinon elles partagent la même identité) :

```powershell
$env:RUCHE_DATA_DIR="$env:TEMP\ruche-alice"; .\.venv\Scripts\python.exe run.py
$env:RUCHE_DATA_DIR="$env:TEMP\ruche-bob";   .\.venv\Scripts\python.exe run.py
```

---

## Comment ça marche

```
   Alice (hôte)  ⇄  Bob  ⇄  Chloé      ← maillage WebRTC : chat, fichiers, appels
         │            │         │
         └──── serveur de rendez-vous (sans état) ────┘  sert seulement à se trouver
```

- **Maillage pair-à-pair** : chaque paire de participants ouvre une connexion
  WebRTC directe (chiffrée, avec un canal de données fiable). Toute la
  communication passe par là.
- **Serveur de rendez-vous** : sur Internet, il permet aux participants
  d'écouter un même salon et de négocier la connexion WebRTC. Il ne stocke rien
  et ne fait pas tourner le chat ; s'il s'arrête, les participants déjà connectés
  continuent de discuter. **Sur un réseau local, il est inutile** : mDNS et une
  signalisation HTTP directe prennent le relais.
- **Élection d'hôte** : l'hôte est le participant connecté dont l'identifiant est
  le plus petit. Tous calculent la même règle, donc ils désignent le même hôte
  sans se concerter. Quand l'hôte part, le suivant prend le relais **tout seul**.
- **Historique** : chaque message est horodaté (horloge de Lamport) et conservé
  par tous les participants. À la reconnexion, les journaux fusionnent sans
  doublon ni perte.
- **Identité** : ni compte ni mot de passe. Une paire de clés est générée au
  premier lancement, et l'identifiant en dérive.

### Fichiers

- Un fichier est identifié par le **SHA-256 de son contenu** : n'importe quel
  pair qui le possède peut le servir, et l'intégrité est vérifiée à l'arrivée.
- Le transfert passe par un **canal dédié** (avec contre-pression), pour que le
  chat reste fluide pendant un gros envoi.
- Les **images** reçues (jusqu'à 8 Mo) sont téléchargées automatiquement et
  affichées dans le fil ; les autres fichiers montrent un lien de
  téléchargement, puis s'ouvrent d'un clic une fois reçus.

### Appels

- Bouton **Appeler** : un appel de groupe démarre dans le salon ; les autres
  participants reçoivent une invitation et le rejoignent s'ils le souhaitent.
- Audio et vidéo circulent en **maillage** (chaque pair envoie aux autres).
- La fenêtre d'appel affiche une mosaïque et permet de **couper le micro** ou
  la **caméra**, puis de raccrocher.
- Le bouton **Périphériques** choisit la caméra, le micro et le haut-parleur.
  Sans caméra, une **mire de test** animée prend le relais.

### Partage d'écran

- Pendant un appel, le bouton **Partager l'écran** remplace la caméra par la
  capture de l'écran, sans renégociation (le flux continue, le contenu change).
- La capture est redimensionnée automatiquement pour rester fluide.

### Fonctionner sans serveur (réseau local)

- Sur un même réseau, les participants se trouvent **tout seuls** via mDNS et
  échangent leur signalisation par HTTP direct : **aucun serveur n'est requis**.
- Laissez simplement le champ « Serveur de rendez-vous » vide.
- Le rendez-vous ne sert qu'à se retrouver **entre réseaux différents**.

### Relais TURN (réseaux récalcitrants)

Quand le NAT bloque le pair-à-pair, un relais TURN prend le relais. Trois façons
de le configurer :

1. fichier `ice_servers.json` dans le dossier de données (modèle :
   `deploy/ice_servers.example.json`) ;
2. variables d'environnement `RUCHE_TURN_URL`, `RUCHE_TURN_USER`,
   `RUCHE_TURN_PASS` ;
3. sinon, STUN publics uniquement.

Un serveur de rendez-vous + TURN « clé en main » est fourni :

```powershell
docker compose -f deploy/docker-compose.yml up -d
```

### Limites connues

- Derrière un **NAT symétrique**, une connexion directe peut échouer : configurez
  alors un **relais TURN** (voir plus haut).
- Le maillage est confortable jusqu'à **~5-6 participants** en vidéo (coût en
  O(n²)). Au-delà, un serveur média (SFU) serait nécessaire.
- L'historique n'est disponible que tant qu'au moins un membre de la session l'a
  répliqué.

---

## Tests

```powershell
.\.venv\Scripts\python.exe tools\smoke_test.py   # socle : maillage, chat, fichiers, bascule d'hôte
.\.venv\Scripts\python.exe tools\lan_test.py     # découverte mDNS et connexion SANS serveur
.\.venv\Scripts\python.exe tools\call_test.py    # appels + partage d'écran
.\.venv\Scripts\python.exe tools\gui_test.py     # interface, en mode hors écran
```

## Structure du projet

```
app/
  config.py                 paramètres et chemins
  i18n.py                   chaînes de l'interface (français)
  core/
    identity.py             identité locale (clé + pseudo)
    storage.py              stockage SQLite (messages, fichiers, salons)
    history.py              journal répliqué (Lamport)
    files.py                magasin de fichiers (adressage par SHA-256)
    media.py                caméra, micro, haut-parleur
    room.py                 salon : membres, élection d'hôte, chat, fichiers, appels
    network/
      rendezvous.py         client du serveur de rendez-vous
      lan.py                découverte mDNS + signalisation locale
      transport.py          maillage WebRTC (aiortc)
  rendezvous/server.py      serveur de rendez-vous sans état
  ui/main_window.py         fenêtre principale (PySide6)
  ui/call_window.py         fenêtre d'appel (mosaïque vidéo)
  main.py                   point d'entrée
tools/                      scripts de vérification
deploy/                     docker-compose (rendez-vous + TURN), modèle ICE
ruche.spec / build.ps1      empaquetage PyInstaller
```

## Feuille de route

1. ✅ Fondations — projet, fenêtre, stockage, identité
2. ✅ Découverte & maillage — rendez-vous, première connexion WebRTC
3. ✅ Chat — messages texte, salons, élection d'hôte
4. ✅ Historique & bascule — réplication, bascule testée
5. ✅ Fichiers — images/vidéos, transfert par morceaux, adressage par contenu
6. ✅ Appels — audio et vidéo, de groupe en maillage
7. ✅ Partage d'écran
8. ✅ Durcissement — relais TURN, découverte mDNS locale, empaquetage PyInstaller

## Empaqueter l'application

L'application peut être livrée en dossier autonome (aucun Python à installer) :

```powershell
powershell -ExecutionPolicy Bypass -File build.ps1
```

Le résultat se trouve dans `dist\Ruche\Ruche.exe`.
