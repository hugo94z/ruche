"""Lanceur du serveur de rendez-vous Ruche (sans état).

    python run_rendezvous.py --host 0.0.0.0 --port 8765
"""

from app.rendezvous.server import main

if __name__ == "__main__":
    main()
