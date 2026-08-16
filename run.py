import os
import socket
import sys

from app import create_app, socketio

PORT = 10001
PID_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "server.pid")
app = create_app()


def _write_pid():
    try:
        with open(PID_FILE, "w") as f:
            f.write(str(os.getpid()))
    except OSError:
        pass


def _remove_pid():
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except OSError:
        pass


if __name__ == "__main__":
    # Fail loudly instead of silently serving nothing: if another process is
    # actually LISTENING on the port, tell the user exactly which PID to kill.
    # Otherwise it looks like the app is running, but the browser keeps hitting
    # the OLD process with its OLD database/config.
    #
    # SO_REUSEADDR is critical here: after the old server is killed, its
    # connections (e.g. the managers' /manager/state polling) leave TIME_WAIT
    # tuples on the port for ~60s. A fresh LISTEN bind on a port with TIME_WAIT
    # tuples fails with EADDRINUSE unless SO_REUSEADDR is set — which is exactly
    # the "Cannot bind port 10001: [Errno 98]" the deploy kept hitting even
    # after the old process was killed. Werkzeug's own listen socket sets
    # SO_REUSEADDR; only this pre-check was missing it.
    probe = socket.socket()
    try:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("0.0.0.0", PORT))
    except OSError as e:
        print(f"\nCannot bind port {PORT}: {e}")
        print("Another server is already running. Find and kill it, then retry:")
        if os.name == "nt":
            print("  netstat -ano | grep :10001")
            print("  taskkill //PID <pid> //F //T")
        else:
            print("  ss -ltnp 'sport = :10001'   # or: netstat -nlp | grep :10001")
            print("  kill <pid>")
        sys.exit(1)
    finally:
        probe.close()

    _write_pid()
    print(f"Serving on http://127.0.0.1:{PORT}  (DB: {app.config['DB_PATH']})", flush=True)
    try:
        # use_reloader=False: the debug reloader spawns child processes that
        # inherit the socket and re-run this pre-check, causing a bind race on
        # restart (and, historically, zombie servers holding the port). One
        # process, one bind.
        socketio.run(app, host="0.0.0.0", port=PORT, debug=True, use_reloader=False)
    finally:
        _remove_pid()
