"""MITS ONE - Professional College Attendance & Timetable Server.
Serves the responsive single-page web app and interfaces with GEMS API.

Usage:
    python server.py
    (Or ./start.sh)
Open http://127.0.0.1:5000 in your browser.
To access from phone on same Wi-Fi: set MITS_HOST=0.0.0.0, open http://<your-ip>:5000
"""
import contextlib
import io
import os
import socket
from datetime import datetime

from flask import Flask, jsonify, request, send_from_directory

from gems_api_working import GemsAPIClient

HERE = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=None)


def get_local_ip():
    """Find the local IP address for sharing over Wi-Fi."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


@app.after_request
def add_headers(response):
    """Enable CORS and ensure fresh assets."""
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    # Prevent caching of index.html so frontend code edits apply immediately
    if request.path == "/" or request.path.endswith(".html"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


@app.route("/", methods=["GET"])
def index():
    return send_from_directory(HERE, "index.html")


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({
        "status": "healthy",
        "service": "MITS ONE Attendance Portal",
        "serverTime": datetime.now().isoformat()
    })


@app.route("/api/demo", methods=["GET", "POST"])
def demo_data():
    """Return realistic demo student data for testing, offline preview, and showcase."""
    mock = GemsAPIClient.get_mock_data()
    return jsonify(mock)


@app.route("/api/data", methods=["POST", "OPTIONS"])
def data():
    if request.method == "OPTIONS":
        return "", 204

    body = request.get_json(silent=True) or {}
    username = str(body.get("username", "")).strip()
    password = str(body.get("password", "")).strip()

    if not username or not password:
        return jsonify(error="Please enter both username and password."), 400

    # Quick demo bypass for testing without college credentials
    if username.lower() == "demo" and password.lower() == "demo":
        return jsonify(GemsAPIClient.get_mock_data())

    client = GemsAPIClient()
    result = None
    captured_logs = io.StringIO()

    # Suppress verbose stdout from scraper to keep server console clean
    with contextlib.redirect_stdout(captured_logs):
        try:
            result = client.scrape_full_data(username, password)
        except Exception as e:
            result = None
            if not getattr(client, "last_error", None):
                client.last_error = f"Scraping exception: {str(e)}"

    if not result:
        err_msg = getattr(client, "last_error", None) or "Could not fetch your data. Please check your credentials and try again."
        status_code = 401 if ("login" in err_msg.lower() or "credentials" in err_msg.lower() or "password" in err_msg.lower()) else 502
        return jsonify(error=err_msg), status_code

    return jsonify(result)


def find_available_port(default_port=5000):
    """Find an open port, handling macOS AirPlay Receiver on 5000 smoothly."""
    requested = os.environ.get("MITS_PORT")
    if requested:
        return int(requested)
    for p in [default_port, 5050, 5001, 8080, 8000]:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("0.0.0.0", p))
                return p
            except OSError:
                continue
    return 5050


if __name__ == "__main__":
    host = os.environ.get("MITS_HOST", "0.0.0.0")
    port = find_available_port(5000)
    local_ip = get_local_ip()

    print("=" * 64)
    print("🎓 MITS ONE - College Attendance Portal Server")
    print("=" * 64)
    print(f"👉 Local Access:   http://127.0.0.1:{port}")
    if local_ip != "127.0.0.1":
        print(f"📱 Phone (Wi-Fi):  http://{local_ip}:{port}")
    print("=" * 64)
    print("Press Ctrl+C to stop the server.\n")

    app.run(host=host, port=port, threaded=True)
