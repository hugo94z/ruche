"""Chaînes de l'interface, en français."""

from __future__ import annotations

STRINGS: dict[str, str] = {
    # Fenêtre
    "app.title": "Ruche — messagerie pair-à-pair",
    "app.subtitle": "Discutez, partagez, appelez — sans serveur central.",

    # Connexion
    "connect.pseudo": "Pseudo",
    "connect.pseudo.placeholder": "Votre pseudo",
    "connect.room": "Salon",
    "connect.room.placeholder": "code du salon",
    "connect.rendezvous": "Serveur de rendez-vous",
    "connect.join": "Rejoindre",
    "connect.leave": "Quitter",
    "connect.new_room": "Nouveau salon",
    "connect.status_idle": "Hors ligne",
    "connect.status_connecting": "Connexion…",
    "connect.status_online": "Connecté au salon « {room} »",
    "connect.pseudo_required": "Choisissez d'abord un pseudo.",

    # Membres
    "members.title": "Membres ({count})",
    "members.you": "vous",
    "members.host": "hôte",

    # Chat
    "chat.placeholder": "Écrivez un message…  (Entrée pour envoyer)",
    "chat.send": "Envoyer",
    "chat.attach": "Joindre un fichier",
    "chat.attach_filter": "Tous les fichiers (*.*)",
    "chat.empty": "Aucun message pour l'instant. Dites bonjour !",
    "chat.host_changed": "L'hôte est maintenant {pseudo}.",

    # Appels
    "call.title": "Appel",
    "call.start": "Appeler",
    "call.hangup": "Raccrocher",
    "call.you": "Vous ({pseudo})",
    "call.mic_off": "Couper le micro",
    "call.mic_on": "Réactiver le micro",
    "call.cam_off": "Couper la caméra",
    "call.cam_on": "Réactiver la caméra",
    "call.screen_share": "Partager l'écran",
    "call.screen_stop": "Arrêter le partage",
    "call.incoming": "{pseudo} lance un appel de groupe. Rejoindre ?",
    "call.settings": "Périphériques",
    "call.camera": "Caméra",
    "call.microphone": "Microphone",
    "call.speaker": "Haut-parleur",
    "call.no_camera": "Mire de test (aucune caméra)",
    "call.system_default": "Par défaut du système",

    # Divers
    "misc.copy": "Copier",
    "misc.copied": "Copié",
    "misc.error": "Erreur",
}


def t(key: str, **kwargs: object) -> str:
    text = STRINGS.get(key, key)
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError):
            return text
    return text
