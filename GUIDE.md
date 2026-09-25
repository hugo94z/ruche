# Guide d'utilisation de Ruche

Application de messagerie **pair-à-pair** : discussion texte, partage d'images
et de vidéos, appels audio/vidéo de groupe et partage d'écran — **sans serveur
central**, avec bascule automatique de l'hôte.

- Interface graphique native **Python 3.13** (PySide6), en **français**
- Version : `0.1.0`

---

## 1. Lancer l'application (le plus simple)

Sur le **Bureau**, double-cliquez sur le raccourci **Ruche**.

> Le raccourci pointe vers l'application autonome
> `dist\Ruche\Ruche.exe` (aucun Python requis pour l'utiliser).
> S'il n'existe pas encore, il pointe vers `run.py` de l'environnement local.

### Installation (Windows)

| Méthode | Commande |
|---|---|
| Installateur | `Ruche-Setup-0.2.0.exe` (page des versions GitHub) |
| winget | `winget install Ruche.Ruche` |

L'installation se fait **par utilisateur**, dans `%LOCALAPPDATA%\Programs\Ruche`
(aucun droit administrateur requis).

### Autres façons de lancer

| Méthode | Commande |
|---|---|
| Application autonome | `dist\Ruche\Ruche.exe` |
| Depuis les sources | `.\.venv\Scripts\python.exe run.py` |
| Sans fenêtre de console | `.\.venv\Scripts\pythonw.exe run.py` |

---

## 2. Installation depuis les sources

Prérequis : **Python 3.13**.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

---

## 3. Utilisation

### 3.1 Rejoindre un salon

1. Saisissez un **pseudo**.
2. Saisissez un **code de salon** (ou cliquez **Nouveau salon** pour en générer un).
3. Cliquez **Rejoindre**.

Le même code doit être utilisé par tous les participants. Il n'y a **ni compte
ni mot de passe** : votre identité est une paire de clés créée au premier
lancement.

### 3.2 Discuter

- Écrivez dans le champ en bas et appuyez sur **Entrée**.
- Les messages circulent **directement entre les participants**.
- L'historique est conservé localement et **répliqué** chez les autres membres.

### 3.3 Envoyer des images et des vidéos

- Cliquez **Joindre un fichier** et choisissez un fichier.
- Les **images** reçues (jusqu'à 8 Mo) s'affichent automatiquement dans le fil.
- Les autres fichiers affichent un lien **⬇ nom** : cliquez pour les recevoir,
  puis cliquez à nouveau pour les ouvrir.
- Un fichier est identifié par son **empreinte SHA-256** : son intégrité est
  vérifiée à l'arrivée et n'importe quel pair qui le possède peut le partager.

### 3.4 Appels audio/vidéo de groupe

1. Cliquez **Appeler**.
2. Les autres membres reçoivent une invitation et cliquent **Oui** pour rejoindre.
3. La fenêtre d'appel affiche une **mosaïque** des participants.

Dans la fenêtre d'appel :

| Bouton | Effet |
|---|---|
| Couper le micro | N'envoie plus le son (silence) |
| Couper la caméra | N'envoie plus qu'une image noire |
| Partager l'écran | Envoie votre écran à la place de la caméra |
| Raccrocher | Termine l'appel pour vous |

### 3.5 Choisir ses périphériques

Bouton **Périphériques** (fenêtre principale) : caméra, micro, haut-parleur.

- Sans caméra, une **mire de test** animée est envoyée.
- Le son est repris via `sounddevice` ; les noms de périphériques sont listés
  avec leur numéro.

### 3.6 Plusieurs salons et messages privés

- La **barre latérale** (à gauche) liste les salons ouverts. Cliquez sur un
  salon pour le rendre actif ; **clic droit → Quitter ce salon** pour le fermer.
- Le bouton **Rejoindre** ouvre un salon **sans fermer les autres** : saisissez
  un autre code et cliquez. Le bouton **Quitter** ferme le salon actif.
- Clic droit sur un membre → **Message privé** : une conversation 1‑à‑1
  persistante. Elle apparaît dans la barre latérale sous le pseudo du
  correspondant.

### 3.7 Modifier, supprimer, réagir

- Sur un message, cliquez le lien **⋯** (à droite de l'horodatage) pour ouvrir
  le menu : **Modifier**, **Supprimer**, **Réagir** (👍 ❤️ 😂 ✅).
- Vous ne pouvez modifier ou supprimer que **vos** messages.
- Une modification affiche **« (modifié) »** ; une suppression masque le message
  partout, mais l'entrée d'origine reste dans le journal signé (rien n'est
  réécrit en douce).
- Les images reçues s'affichent en **vignette** ; un clic ouvre l'original.
- Un transfert interrompu **reprend au dernier octet reçu** à la reconnexion.

### 3.8 Écrire à un pair hors ligne

- Vous pouvez envoyer un **message privé** même si le correspondant n'est pas
  connecté : il est **chiffré** et mis en attente sur votre machine.
- Dès que le correspondant réapparaît (dans un salon commun ou dans la
  conversation), le message lui est **remis automatiquement**, puis effacé de
  votre boîte après **accusé de réception**.
- Si le correspondant n'est jamais revenu, le message reste en attente : il n'y
  a **pas de serveur** pour le garder à votre place.

### 3.9 Cache et mises à jour

- Bouton **Cache…** : cochez les fichiers à supprimer (images, vidéos reçues)
  pour libérer de l'espace ; **Nettoyer les transferts inachevés** efface les
  réceptions interrompues.
- **Vérifier les mises à jour** (clic droit sur l'icône de la zone de
  notification) : Ruche compare sa version à la dernière publiée et propose
  d'ouvrir la page de téléchargement le cas échéant.

---

## 4. Les trois façons de se connecter

Ruche adapte automatiquement la méthode de connexion :

| Situation | Ce qu'il faut faire |
|---|---|
| **Même réseau local** (Wi‑Fi/filaire) | Laissez la case **Serveur de rendez-vous vide**. Les pairs se trouvent tout seuls via mDNS. |
| **Réseaux différents (Internet)** | Indiquez l'adresse d'un **serveur de rendez-vous** joignable par tous. |
| **NAT bloquant** | Ajoutez un **relais TURN** (voir §6). |

> Le serveur de rendez-vous ne stocke **aucun** message et ne fait pas tourner
> la conversation : il sert uniquement à présenter les participants les uns aux
> autres. S'il tombe, les conversations en cours continuent.

### Comment savoir si ça marche ?

La **barre de statut** (en bas) affiche l'état : « découverte locale active
(mDNS) », « connecté au rendez-vous », « hôte du salon : … », etc.

---

## 5. Héberger un serveur de rendez-vous

### Option A — en un clic, depuis l'application (le plus simple)

Dans Ruche, clique **Héberger un rendez-vous**. L'application démarre le
serveur et affiche l'adresse à transmettre :

- **Copier** place l'adresse dans le presse-papiers ;
- **Me connecter via cet hébergement** remplit le champ pour toi ;
- garde Ruche ouvert : si tu fermes, les autres ne pourront plus te rejoindre
  (les conversations déjà établies continuent).

Depuis Internet, il faut une **redirection du port 8765** sur ta box.

### Option B — sur une machine dédiée

Sur n'importe quelle machine joignable par les participants (pas forcément la
vôtre) :

```powershell
.\.venv\Scripts\python.exe run_rendezvous.py --host 0.0.0.0 --port 8765
```

Les autres saisissent alors : `ws://ADRESSE_IP:8765/ws`

Vérification rapide du service (dans un navigateur ou avec `curl`) :

```
http://ADRESSE_IP:8765/
```

Réponse attendue : `{"service": "ruche-rendezvous", "rooms": 0, "peers": 0}`

---

## 6. Configurer un relais TURN

Un relais TURN prend le secours quand le NAT empêche la connexion directe.
Trois possibilités, par ordre de priorité :

1. **Fichier** `ice_servers.json` dans le dossier de données :
   ```json
   [
     { "urls": "stun:stun.l.google.com:19302" },
     { "urls": "turn:MON_IP:3478", "username": "ruche", "credential": "motdepasse" }
   ]
   ```
   Modèle fourni : `deploy/ice_servers.example.json`.

2. **Variables d'environnement** :
   ```powershell
   $env:RUCHE_TURN_URL="turn:MON_IP:3478"
   $env:RUCHE_TURN_USER="ruche"
   $env:RUCHE_TURN_PASS="motdepasse"
   ```

3. Sinon, seuls les **STUN publics** sont utilisés.

Emplacement du dossier de données :

- Windows : `%APPDATA%\Ruche`
- macOS : `~/Library/Application Support/Ruche`
- Linux : `~/.config/Ruche`

---

## 7. Déploiement « clé en main » (Docker)

Rendez-vous **et** relais TURN en une commande :

```powershell
docker compose -f deploy/docker-compose.yml up -d
```

Pensez à personnaliser `TURN_PASSWORD` (variable d'environnement) et l'adresse
publique dans `deploy/ice_servers.example.json`.

---

## 8. Empaqueter l'application

Pour produire un dossier autonome (aucun Python à installer) :

```powershell
powershell -ExecutionPolicy Bypass -File build.ps1
```

Résultat : `dist\Ruche\Ruche.exe`

---

## 9. Tester le projet

```powershell
.\.venv\Scripts\python.exe tools\smoke_test.py   # maillage, chat, fichiers, bascule d'hôte
.\.venv\Scripts\python.exe tools\lan_test.py     # découverte mDNS sans serveur
.\.venv\Scripts\python.exe tools\call_test.py    # appels + partage d'écran
.\.venv\Scripts\python.exe tools\messaging_test.py  # multi-salons, édition, réactions, reprise, MP
.\.venv\Scripts\python.exe tools\offline_test.py  # boîte aux lettres chiffrée, livraison différée
.\.venv\Scripts\python.exe tools\distribution_test.py  # purge du cache, mises à jour
.\.venv\Scripts\python.exe tools\gui_test.py     # interface hors écran
```

---

## 10. Dépannage

| Symptôme | Piste |
|---|---|
| Personne n'apparaît | Vérifiez que le **code de salon** est identique ; en local, le pare-feu peut bloquer mDNS. |
| Pas de connexion entre réseaux | Renseignez un **serveur de rendez-vous** ; vérifiez qu'il est joignable. |
| La connexion échoue malgré tout | Ajoutez un **relais TURN** (NAT symétrique). |
| Pas d'image en appel | Choisissez la caméra dans **Périphériques** ; sinon la mire de test est envoyée. |
| Pas de son | Vérifiez micro et haut-parleur dans **Périphériques**. |
| Rien ne se passe au démarrage | Lancez depuis un terminal pour voir les journaux : `.\.venv\Scripts\python.exe run.py` |

---

## 11. Notes techniques

- **Transport** : maillage WebRTC (aiortc), chiffré de bout en bout ; un canal
  pour le contrôle (chat, historique, fichiers — métadonnées) et un canal dédié
  aux octets des fichiers.
- **Hôte** : le participant connecté ayant le **plus petit identifiant**. Tous
  calculent la même règle, donc désignent le même hôte sans se concerter ; quand
  il part, le suivant prend le relais automatiquement.
- **Historique** : journal append-only horodaté (horloge de Lamport), répliqué
  chez tous les membres ; la fusion à la reconnexion est sans doublon.
- **Identité** : paire de clés Ed25519 générée localement ; l'identifiant en est
  dérivé. Aucun serveur d'authentification.
- **Fichiers** : adressés par contenu (SHA-256), servis par n'importe quel pair.

### Structure du projet

```
app/
  config.py                 paramètres et chemins
  i18n.py                   chaînes de l'interface (français)
  core/
    identity.py             identité locale (signature + chiffrement)
    storage.py              stockage SQLite (+ boîte aux lettres)
    crypto.py               signature Ed25519 + scellement X25519
    history.py              journal répliqué (+ édition/réactions)
    files.py                magasin de fichiers, vignettes, reprise
    media.py                caméra, micro, haut-parleur, écran
    update.py               vérification des mises à jour
    room.py                 hub multi-salons (RoomSession + RoomManager)
    network/
      rendezvous.py         client du serveur de rendez-vous
      lan.py                découverte mDNS + signalisation locale
      transport.py          maillage WebRTC
  rendezvous/server.py      serveur de rendez-vous sans état
  ui/                       fenêtres PySide6
  main.py                   point d'entrée
deploy/                     Docker + modèle ICE
tools/                      scripts de vérification
```

---

## 12. Limites connues

- Confortable jusqu'à **~5-6 participants** en vidéo (maillage en O(n²)).
- Un **NAT symétrique** peut empêcher la connexion directe sans relais TURN.
- L'historique n'est disponible que tant qu'au moins un membre de la session
  l'a répliqué.
- La découverte locale dépend du réseau (certains Wi‑Fi d'entreprise la bloquent).
