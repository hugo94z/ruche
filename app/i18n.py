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
    "members.verified": "vérifié",
    "members.blocked": "bloqué",
    "members.muted": "sourdine",

    # Confiance et modération
    "peer.show_fingerprint": "Voir l'empreinte…",
    "peer.verify": "Marquer comme vérifié",
    "peer.unverify": "Retirer la vérification",
    "peer.block": "Bloquer ce pair",
    "peer.unblock": "Débloquer",
    "peer.mute": "Mettre en sourdine",
    "peer.unmute": "Retirer la sourdine",
    "peer.fingerprint_title": "Empreinte de {pseudo}",
    "peer.fingerprint_body": (
        "Compare cette empreinte avec ton correspondant par un autre canal "
        "(de vive voix, SMS…).\n\n{pseudo}\n{fingerprint}\n\n"
        "Si elle diffère, quelqu'un se fait peut-être passer pour lui."
    ),
    "security.key_changed": (
        "⚠ Attention : {pseudo} se présente avec une NOUVELLE clé.\n\n"
        "Cela peut signifier une usurpation d'identité. Le pair a été ignoré."
    ),

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
    "call.quality": "Qualité vidéo",
    "call.screen_fps": "Partage d'écran (images/s)",
    "call.monitor": "Écran à partager",

    # Hébergement d'un rendez-vous
    "host.button": "Héberger un rendez-vous",
    "host.title": "Héberger un rendez-vous",
    "host.intro": "Vous êtes le point de rencontre. Transmettez une adresse ci-dessous aux autres participants.",
    "host.address": "Adresse à partager",
    "host.copy": "Copier",
    "host.copied": "Adresse copiée dans le presse-papiers",
    "host.public_ok": "Depuis Internet : {url}  (nécessite une redirection du port {port} sur votre box)",
    "host.public_unknown": "Depuis Internet : adresse publique non détectée. Il faudra une redirection de port sur votre box.",
    "host.warning": "Gardez Ruche ouvert : si vous fermez l'application, les autres ne pourront plus vous rejoindre.",
    "host.use": "Me connecter via cet hébergement",
    "host.stop": "Arrêter l'hébergement",
    "host.close": "Fermer",
    "host.started": "Hébergement démarré sur le port {port}",
    "host.stopped": "Hébergement arrêté",
    "host.failed": "Impossible de démarrer l'hébergement : {error}",

    # Divers
    "misc.copy": "Copier",
    "misc.copied": "Copié",
    "misc.error": "Erreur",

    # Zone de notification
    "tray.open": "Ouvrir Ruche",
    "tray.quit": "Quitter",
    "tray.still_running": "Ruche continue de tourner en arrière-plan pour recevoir les messages.",
    "tray.new_message": "Nouveau message de {pseudo}",
    "settings.autostart": "Lancer Ruche au démarrage de Windows",
    "settings.autostart_hint": "Ruche démarre réduit en zone de notification et vous prévient des nouveaux messages.",
}


def t(key: str, **kwargs: object) -> str:
    text = STRINGS.get(key, key)
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError):
            return text
    return text
