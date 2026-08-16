import socket
import sys
from urllib.parse import urlparse

from app import create_app, socketio

PORT = 10001
app = create_app()

if __name__ == "__main__":
    # Fail loudly instead of silently serving nothing: if another process
    # already holds the port (e.g. a leftover server), tell the user exactly
    # which PID to kill. Otherwise it looks like the app is running, but the
    # browser keeps hitting the OLD process with its OLD database/config.
    with socket.socket() as s:
        try:
            s.bind(("0.0.0.0", PORT))
        except OSError as e:
            print(f"\nCannot bind port {PORT}: {e}")
            print("Another server is already running. Find and kill it, then retry:")
            print("  netstat -ano | grep :10001")
            print("  taskkill //PID <pid> //F //T")
            sys.exit(1)
    print(f"Serving on http://127.0.0.1:{PORT}  (DB: {app.config['DB_PATH']})")
    # use_reloader=False: the debug reloader spawns child processes that inherit
    # the socket and re-run this pre-check, causing a bind race on restart (and,
    # historically, zombie servers holding the port). One process, one bind.
    socketio.run(app, host="0.0.0.0", port=PORT, debug=True, use_reloader=False)
