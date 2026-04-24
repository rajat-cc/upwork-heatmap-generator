import json
import secrets
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import requests

from config import (
    ACCESS_TOKEN, AUTH_URL, CLIENT_ID, CLIENT_SECRET,
    REDIRECT_URI, TOKEN_CACHE_FILE, TOKEN_URL,
)


def get_access_token() -> str:
    if ACCESS_TOKEN:
        return ACCESS_TOKEN

    cache = Path(TOKEN_CACHE_FILE)
    if cache.exists():
        cached = json.loads(cache.read_text())
        if cached.get("expires_at", 0) > time.time() + 60:
            return cached["access_token"]
        if cached.get("refresh_token"):
            return _refresh(cached["refresh_token"])

    if not CLIENT_ID or not CLIENT_SECRET:
        raise RuntimeError(
            "\n[!] No Upwork credentials found.\n"
            "Set UPWORK_CLIENT_ID + UPWORK_CLIENT_SECRET in .env then run:\n"
            "    make auth\n"
        )

    raise RuntimeError(
        "\n[!] No valid token found. Run:  make auth\n"
        "    This opens your browser to authorize the app with Upwork.\n"
    )


def run_auth_flow() -> str:
    """Full OAuth2 authorization code flow — opens browser, captures callback."""
    if not CLIENT_ID or not CLIENT_SECRET:
        raise RuntimeError("Set UPWORK_CLIENT_ID and UPWORK_CLIENT_SECRET in .env first.")

    state = secrets.token_urlsafe(16)
    auth_params = urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "state": state,
    })
    full_auth_url = f"{AUTH_URL}?{auth_params}"

    # Determine if we can use a local server or need manual code entry
    is_localhost = REDIRECT_URI.startswith("http://localhost") or REDIRECT_URI.startswith("http://127.0.0.1")

    print(f"\n  Opening browser for Upwork authorization...")
    print(f"  If browser doesn't open, visit:\n  {full_auth_url}\n")
    webbrowser.open(full_auth_url)

    if is_localhost:
        code = _capture_code_via_server(state)
    else:
        # Custom scheme (e.g. upwork-analytics://callback) — user pastes the code manually
        print(f"\n  Your redirect URI is: {REDIRECT_URI}")
        print("  After authorizing, Upwork will redirect you to that URL.")
        print("  Copy the 'code' value from the URL and paste it here.")
        code = input("\n  Paste the authorization code: ").strip()

    if not code:
        raise RuntimeError("No authorization code received.")

    return _exchange_code(code)


def _capture_code_via_server(expected_state: str) -> str:
    captured = {}

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            captured["code"] = (params.get("code") or [None])[0]
            captured["state"] = (params.get("state") or [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<h2>Authorization successful!</h2>"
                b"<p>You can close this tab and return to your terminal.</p>"
            )

        def log_message(self, *args):
            pass

    port = int(REDIRECT_URI.split(":")[-1].split("/")[0])
    server = HTTPServer(("localhost", port), _Handler)
    print(f"  Waiting for Upwork to redirect to localhost:{port}...")
    server.handle_request()
    server.server_close()

    if captured.get("state") != expected_state:
        raise RuntimeError("State mismatch — possible CSRF. Try again.")
    return captured.get("code", "")


def _exchange_code(code: str) -> str:
    resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    data["expires_at"] = time.time() + data.get("expires_in", 3600)
    Path(TOKEN_CACHE_FILE).write_text(json.dumps(data, indent=2))
    print(f"\n  Token saved to {TOKEN_CACHE_FILE}")
    return data["access_token"]


def _refresh(refresh_token: str) -> str:
    resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=15,
    )
    if resp.status_code != 200:
        Path(TOKEN_CACHE_FILE).unlink(missing_ok=True)
        raise RuntimeError("Refresh token expired. Run:  make auth")
    data = resp.json()
    data["expires_at"] = time.time() + data.get("expires_in", 3600)
    Path(TOKEN_CACHE_FILE).write_text(json.dumps(data, indent=2))
    return data["access_token"]
