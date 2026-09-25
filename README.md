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
| **Plusieurs salons simultanés** (barre latérale) | ✅ |
| **Messages privés persistants** (1‑à‑1, historisés) | ✅ |
| **Édition / suppression / réactions** (opérations signées) | ✅ |
| **Vignettes** des images reçues | ✅ |
| **Reprise des transferts** interrompus | ✅ |
| **Boîte aux lettres chiffrée** (livraison différée des MP) | ✅ |
| Appels audio/vidéo de groupe | ✅ |
| Partage d'écran | ✅ |
| Découverte locale mDNS (sans serveur) | ✅ |
| Hébergement d'un rendez-vous en un clic | ✅ |
| Relais TURN configurable | ✅ |
| Empaquetage en application autonome | ✅ |

Toutes les phases de la feuille de route sont réalisées (voir plus bas).

---

## Installation

### Windows — installateur ou winget

Téléchargez **`Ruche-Setup-0.2.0.exe`** depuis la page des versions :

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

## Sécurité

- **Signature des messages** — chaque message est signé (Ed25519). Comme
  l'identifiant d'un pair est dérivé de sa clé publique, **personne ne peut se
  faire passer pour un autre** : un message falsifié ou attribué à une autre
  identité est **rejeté**.
- **Empreintes vérifiables** — clic droit sur un membre → *Voir l'empreinte*.
  Comparez-la de vive voix, puis marquez le pair comme **vérifié**.
- **Détection d'usurpation** — si un pair se présente avec une **nouvelle clé**,
  il est ignoré et une alerte s'affiche.
- **Blocage / sourdine** — clic droit sur un membre.
- **Limitation de débit** — au-delà de 40 messages par minute, un pair est
  temporairement ignoré.

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
- Les images s'affichent en **vignette** (générée localement) ; un clic ouvre
  l'original.
- Un transfert interrompu **reprend au dernier octet reçu** : rien n'est
  retéléchargé du début.

### Salons et messages privés

- **Plusieurs salons à la fois** : la barre latérale liste les salons ouverts ;
  cliquez pour passer de l'un à l'autre, sans quitter les autres.
- Chaque salon garde son propre historique, ses membres, son hôte et son appel.
- Clic droit sur un membre → **Message privé** : une conversation 1‑à‑1
  persistante, historisée localement et répliquée avec la même signature que
  le reste du maillage.
- Clic droit sur un message (le lien **⋯**) → **modifier**, **supprimer** ou
  **réagir**. L'édition et la suppression ne concernent que vos propres
  messages, et restent des **opérations signées** : rien n'est réécrit en
  douce, l'entrée d'origine est conservée dans le journal.
- **Hors ligne** : si le correspondant n'est pas connecté, le message privé est
  **chiffré** (X25519 + ChaCha20-Poly1305) et mis en attente. Il lui est remis
  automatiquement dès qu'un lien vers lui s'ouvre — même s'il n'a pas encore
  rouvert la conversation — puis effacé sur **accusé de réception**.

### Appels

- Bouton **Appeler** : un appel de groupe démarre dans le salon ; les autres
  participants reçoivent une invitation et le rejoignent s'ils le souhaitent.
- Audio et vidéo circulent en **maillage** (chaque pair envoie aux autres).
- **Annulation d'écho et réduction de bruit** : le micro retire ce que le
  haut-parleur joue (mesuré à ~25 dB d'atténuation), donc plus de larsen.
- **30 images/seconde** pour la vidéo et le partage d'écran, en option
  (profils : basse, moyenne, haute, HD).
- La fenêtre d'appel affiche une mosaïque et permet de **couper le micro** ou
  la **caméra**, puis de raccrocher.
- Le bouton **Périphériques** choisit la caméra, le micro, le haut-parleur, la
  **qualité vidéo**, l'**écran à partager** et sa cadence.
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

### Héberger le point de rencontre en un clic

Le bouton **Héberger un rendez-vous** de l'application démarre un serveur de
rendez-vous directement depuis ta machine, **sans terminal ni Python** :

- l'adresse à transmettre est affichée (réseau local, plus l'adresse publique
  si elle est détectable) ;
- un bouton **Copier** la place dans le presse-papiers ;
- **Me connecter via cet hébergement** remplit le champ automatiquement.

> ⚠️ Limite honnête : depuis Internet, il faut que le port (8765 par défaut)
> soit joignable depuis l'extérieur — donc une **redirection de port** sur la
> box. Sans cela, l'hébergement fonctionne sur le **réseau local**. Pour deux
> lieux différents sans redirection, héberge le rendez-vous ailleurs (`deploy/docker-compose.yml`).

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
.\.venv\Scripts\python.exe tools\host_test.py    # hébergement d'un rendez-vous en un clic
.\.venv\Scripts\python.exe tools\crypto_test.py  # signature et chiffrement de bout en bout
.\.venv\Scripts\python.exe tools\signed_history_test.py  # journal signé (authentification)
.\.venv\Scripts\python.exe tools\messaging_test.py  # multi-salons, édition, réactions, reprise, MP
.\.venv\Scripts\python.exe tools\offline_test.py  # boîte aux lettres chiffrée, livraison différée
.\.venv\Scripts\python.exe tools\media_test.py   # cadence 30 fps, écran, annulation d'écho
.\.venv\Scripts\python.exe tools\call_test.py    # appels + partage d'écran
.\.venv\Scripts\python.exe tools\gui_test.py     # interface, en mode hors écran
```

Les deux prototypes de dérisquage sont conservés pour référence :
`tools/aec_poc.py` (annulation d'écho) et `tools/fps_poc.py` (tenue des 30 fps).

## Structure du projet

```
app/
  config.py                 paramètres et chemins
  i18n.py                   chaînes de l'interface (français)
  core/
    identity.py             identité locale (clé de signature + clé de chiffrement)
    storage.py              stockage SQLite (messages, fichiers, salons, boîte aux lettres)
    crypto.py               signature Ed25519 + scellement X25519 (boîte aux lettres)
    history.py              journal répliqué (Lamport) + édition/réactions
    files.py                magasin de fichiers (SHA-256), vignettes, reprise
    media.py                caméra, micro, haut-parleur
    hosting.py              hébergement d'un rendez-vous depuis l'application
    room.py                 hub multi-salons : RoomSession (salon) + RoomManager
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
9. ✅ v0.2.0 — hébergement d'un rendez-vous **en un clic** depuis l'application

---

## État du développement (v1.0.0 en cours)

Objectif : **une seule version 1.0.0** regroupant 22 fonctionnalités, puis publication.
Avancement : **5 chantiers sur 6 terminés**.

| Chantier | Contenu | État |
|---|---|---|
| **1 · Sécurité** | Signature Ed25519 des messages · empreintes vérifiables · détection de changement de clé · blocage/sourdine · limitation de débit | ✅ **terminé** |
| **2 · Présence** | Notifications système · zone de notification · démarrage auto Windows · reconnexion automatique · thème clair/sombre · relais TURN en interface · écran d'accueil | ✅ **7/8** (reste l'indicateur de frappe et les accusés de réception) |
| **3 · Appels** | 30 fps réels · profils de qualité · choix de l'écran · annulation d'écho + réduction de bruit | ✅ **terminé** |
| **4 · Messagerie** | Multi-salons (barre latérale) · messages privés persistants · édition/suppression/réactions · vignettes · reprise des transferts | ✅ **terminé** |
| **5 · Hors ligne** | Boîte aux lettres chiffrée, livraison différée | ✅ **terminé** |
| **6 · Distribution** | Purge du cache avec sélection · mise à jour par bouton · signature SignPath | ⏳ **à faire** |

### Notes de reprise

- Le **chiffrement de bout en bout a été retiré** à la demande ; la
  **signature des messages est conservée**.
- Le chantier 4 a extrait un `RoomSession` par salon : `RoomManager` est
  désormais un **hub** (identité, confiance, fichiers partagés, réglages média)
  qui possède plusieurs salons, avec une API historique déléguée au salon actif.
- L'édition, la suppression et les réactions sont des **opérations signées
  append-only** qui référencent un message (`extra.target`) : l'entrée d'origine
  n'est jamais réécrite, la vue est recalculée en repliant le journal.
- Un **message privé** est un salon à deux dont le code dérive des deux
  identités (`dm:<id>:<id>`) : il réutilise toute la mécanique (historique
  signé, réplication, reconnexion) et persiste localement.
- La **boîte aux lettres** scelle les MP destinés à un pair hors ligne avec une
  clé X25519 (échange éphémère + ChaCha20-Poly1305) ; ils sont remis dès qu'un
  lien vers ce pair s'ouvre, puis supprimés sur accusé de réception. Le chat en
  direct, lui, n'est pas chiffré de bout en bout (choix conservé).
- Les **prototypes de dérisquage** sont conservés : `tools/aec_poc.py`
  (annulation d'écho) et `tools/fps_poc.py` (tenue des 30 fps).
- La suite de tests compte **114 vérifications**, toutes vertes.

## Empaqueter l'application

L'application peut être livrée en dossier autonome (aucun Python à installer) :

```powershell
powershell -ExecutionPolicy Bypass -File build.ps1
```

Le résultat se trouve dans `dist\Ruche\Ruche.exe`.

### Publication automatique

Pousser un tag `vX.Y.Z` déclenche `.github/workflows/release.yml` : construction
de l'application, compilation de l'installateur, génération et validation des
manifestes winget, puis publication de la release GitHub avec l'installateur en
pièce jointe.

```powershell
git tag v0.2.0
git push origin v0.2.0
```

Le workflow `CI` (`ci.yml`) rejoue les quatre suites de tests à chaque
modification.
