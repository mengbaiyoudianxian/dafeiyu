"""Forward a temporary remote login desktop to a private local browser link."""
import argparse
import json
import secrets
import shlex
import socket
import subprocess
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlencode


def serve(site, host, sessions):
    state = f'"$HOME/.local/share/dafeiyu-comic-sessions/{site}/login.json"'
    if sessions:
        if not sessions.startswith("/"):
            raise ValueError("remote sessions directory must be an absolute path")
        state = shlex.quote(f"{sessions.rstrip('/')}/{site}/login.json")
    data = json.loads(subprocess.check_output(
        ["ssh", "-o", "BatchMode=yes", "--", host, "cat -- " + state],
        timeout=15,
    ))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        local = sock.getsockname()[1]
    remote = int(data["port"])
    opts = urlencode({"autoconnect": "1", "resize": "scale", "password": data["password"], "reconnect": "0"})
    nonce = secrets.token_urlsafe(18)
    class Redirect(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != f"/{nonce}":
                self.send_error(404)
                return
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{local}/vnc.html#{opts}")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
        def log_message(self, *args):
            pass

    with HTTPServer(("127.0.0.1", 0), Redirect) as server:
        tunnel = subprocess.Popen(
            ["ssh", "-N", "-T", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=30", "-L", f"127.0.0.1:{local}:127.0.0.1:{remote}", "--", host],
            stdin=subprocess.DEVNULL,
        )
        try:
            from urllib.request import urlopen
            for _ in range(30):
                if tunnel.poll() is not None:
                    raise RuntimeError("Forwarding failed")
                try:
                    with urlopen(f"http://127.0.0.1:{local}/vnc.html", timeout=1) as response:
                        if response.status == 200:
                            break
                except OSError:
                    time.sleep(0.25)
            else:
                raise RuntimeError("noVNC desktop did not answer")
            print(f"http://127.0.0.1:{server.server_port}/{nonce}", flush=True)
            server.timeout = 2
            end = time.monotonic() + 1200
            while time.monotonic() < end and tunnel.poll() is None:
                server.handle_request()
        finally:
            tunnel.terminate()
            tunnel.wait(timeout=10)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("site", choices=["pica", "jm"])
    parser.add_argument("host", help="your SSH destination for the bot's service user")
    parser.add_argument("--sessions", help="absolute remote sessions directory, if customized")
    args = parser.parse_args()
    serve(args.site, args.host, args.sessions)
