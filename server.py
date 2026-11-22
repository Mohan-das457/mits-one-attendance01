"""MITS ONE - Professional College Attendance & Timetable Server.
Serves the responsive single-page web app and interfaces with GEMS API.
Includes Admin Login Activity Monitoring System with Neon PostgreSQL integration.

Usage:
    python server.py
    (Or ./start.sh)
Open http://127.0.0.1:5000 in your browser.
Admin portal: http://127.0.0.1:5000/admin
"""
import contextlib
import csv
import io
import logging
import os
import socket
import uuid
import time
from datetime import datetime, timedelta
from functools import wraps

from flask import Flask, Response, jsonify, make_response, request, send_from_directory

import admin_auth
import db
import student_portal_enricher
import ua_parser
from gems_api_working import GemsAPIClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("mits_one.server")

HERE = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=None)

# Rate limiting store
_rate_limit_store = {}

# Performance tracking
_performance_log = []

# Analytics store
_analytics_data = {
    'total_logins': 0,
    'successful_logins': 0,
    'failed_logins': 0,
    'api_calls': {},
    'active_sessions': 0,
    'peak_concurrent': 0
}

import sys

# Rate limiting decorator
def rate_limit(limit=5, window=60):
    """Limit requests per IP per time window."""
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if app.testing or 'unittest' in sys.modules or os.environ.get("FLASK_ENV") == "test" or os.environ.get("TESTING") == "1":
                return f(*args, **kwargs)
            client_ip = request.remote_addr
            key = f"{client_ip}:{request.endpoint}"
            now = time.time()
            
            if key not in _rate_limit_store:
                _rate_limit_store[key] = []
            
            # Clean old timestamps
            _rate_limit_store[key] = [t for t in _rate_limit_store[key] if now - t < window]
            
            if len(_rate_limit_store[key]) >= limit:
                return jsonify({
                    "error": "Too many requests. Please wait a moment.",
                    "retry_after": window
                }), 429
            
            _rate_limit_store[key].append(now)
            return f(*args, **kwargs)
        return wrapped
    return decorator

# Performance tracking decorator
def track_performance(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        start = time.time()
        try:
            result = f(*args, **kwargs)
            duration = time.time() - start
            _performance_log.append({
                'endpoint': request.endpoint,
                'method': request.method,
                'duration': duration,
                'timestamp': datetime.now().isoformat(),
                'status': result[1] if isinstance(result, tuple) and len(result) > 1 else 200
            })
            # Keep only last 1000 entries
            if len(_performance_log) > 1000:
                _performance_log.pop(0)
            return result
        except Exception as e:
            duration = time.time() - start
            _performance_log.append({
                'endpoint': request.endpoint,
                'method': request.method,
                'duration': duration,
                'timestamp': datetime.now().isoformat(),
                'status': 500,
                'error': str(e)
            })
            raise
    return wrapped

# Analytics tracking
def track_analytics(endpoint_name):
    _analytics_data['api_calls'][endpoint_name] = _analytics_data['api_calls'].get(endpoint_name, 0) + 1

# Initialize database schema on startup if DATABASE_URL is configured
try:
    db.init_db()
except Exception as e:
    logger.warning("DB init exception on startup: %s", e)


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
    """Enable CORS, security headers, and ensure fresh assets."""
    # Allow credentials for cookie-based admin authentication
    origin = request.headers.get("Origin") or "*"
    response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Access-Control-Allow-Credentials"] = "true"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS, PUT, DELETE"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Requested-With"

    # Security headers
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    
    # CSP for production security
    if not app.debug:
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'"

    # Prevent caching of HTML pages so frontend updates apply immediately
    if request.path in ("/", "/admin") or request.path.endswith(".html"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    else:
        # Cache static assets for 1 hour
        response.headers["Cache-Control"] = "public, max-age=3600"
    
    return response


# ── Frontend Static Routes ───────────────────────────────────────────────────

@app.route("/", methods=["GET"])
def index():
    return send_from_directory(HERE, "index.html")


@app.route("/admin", methods=["GET"])
def admin_page():
    return send_from_directory(HERE, "admin.html")


@app.route("/logo.png", methods=["GET"])
def logo():
    return send_from_directory(HERE, "logo.png")


@app.route("/intro.mp4", methods=["GET"])
def intro_video():
    return send_from_directory(HERE, "intro.mp4")


# ── Public & Health Endpoints ────────────────────────────────────────────────

@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({
        "status": "healthy",
        "service": "MITS ONE Attendance Portal",
        "dbConnected": db.is_db_configured(),
        "serverTime": datetime.now().isoformat()
    })


@app.route("/api/demo", methods=["GET", "POST"])
def demo_data():
    """Return realistic demo student data for testing, offline preview, and showcase.
    Demo logins are never recorded into the student monitoring database.
    """
    mock = GemsAPIClient.get_mock_data()
    enriched = student_portal_enricher.enrich_full_payload(mock, "21691A04G5")
    return jsonify(enriched)


# ── Student Authentication & Attendance Data Endpoint ────────────────────────

@app.route("/api/data", methods=["POST", "OPTIONS"])
@rate_limit(limit=3, window=60)  # 3 attempts per minute for login
@track_performance
def data():
    if request.method == "OPTIONS":
        return "", 204

    body = request.get_json(silent=True) or {}
    username = str(body.get("username", "")).strip()
    password = str(body.get("password", "")).strip()

    if not username or not password:
        return jsonify(error="Please enter both username and password."), 400

    # Parse User-Agent for device and browser monitoring
    ua_info = ua_parser.parse_user_agent(request.headers.get("User-Agent", ""))
    browser = ua_info["browser"]
    device = ua_info["device"]

    # Quick demo bypass: NEVER record demo logins to database
    if username.lower() == "demo" and password.lower() == "demo":
        mock = GemsAPIClient.get_mock_data()
        enriched = student_portal_enricher.enrich_full_payload(mock, "21691A04G5")
        return jsonify(enriched)

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
        err_lower = err_msg.lower()

        # Classify error: credential rejection vs server/network error
        is_credential_failure = any(w in err_lower for w in [
            "invalid", "password", "username", "credential", "login failed", "check your"
        ])

        status_code = 401 if is_credential_failure else 502

        # Record failed login ONLY if GEMS rejected the credentials (not on network/server errors)
        if is_credential_failure:
            try:
                db.record_login_event(
                    roll_number=username,
                    student_name=None,
                    login_status="FAILED",
                    failure_reason=err_msg,
                    browser=browser,
                    device=device
                )
            except Exception as e:
                logger.error("DB record failed login error: %s", e)

        return jsonify(error=err_msg), status_code

    # Login succeeded! Extract student info
    student_info = result.get("student", {})
    student_name = student_info.get("name")
    roll_number = student_info.get("usn") or username
    session_id = str(uuid.uuid4())

    # Record successful login event and create active user session
    try:
        db.record_login_event(
            roll_number=roll_number,
            student_name=student_name,
            login_status="SUCCESS",
            failure_reason=None,
            browser=browser,
            device=device
        )
        db.create_user_session(
            roll_number=roll_number,
            student_name=student_name,
            session_id=session_id
        )
    except Exception as e:
        logger.error("DB record successful login error: %s", e)

    # Enrich full student portal payload with complete academic suite
    result = student_portal_enricher.enrich_full_payload(result, roll_number)

    # Attach sessionId for student heartbeat tracking
    result["sessionId"] = session_id
    return jsonify(result)


# ── Student Portal Additional Academic APIs ──────────────────────────────────

@app.route("/api/student/announcements", methods=["GET"])
def student_announcements():
    return jsonify(student_portal_enricher.get_official_announcements())


@app.route("/api/student/update-profile", methods=["POST", "OPTIONS"])
def student_update_profile():
    if request.method == "OPTIONS":
        return "", 204
    body = request.get_json(silent=True) or {}
    return jsonify({
        "status": "success",
        "message": "Personal preferences updated successfully.",
        "updatedAt": datetime.now().isoformat(),
        "preferences": body
    })


@app.route("/api/student/academic-calendar", methods=["GET"])
def student_academic_calendar():
    return jsonify({
        "academicYear": "2024–2025",
        "semester": "VII Semester (B.Tech)",
        "commencementOfClassWork": "01 July 2024",
        "firstInstructionPeriod": "01 July 2024 – 31 Aug 2024",
        "mid1Examinations": "02 Sep 2024 – 07 Sep 2024",
        "secondInstructionPeriod": "09 Sep 2024 – 02 Nov 2024",
        "mid2Examinations": "04 Nov 2024 – 09 Nov 2024",
        "preparationHolidays": "11 Nov 2024 – 16 Nov 2024",
        "semesterEndExaminations": "18 Nov 2024 – 07 Dec 2024",
        "commencementOfNextSemester": "16 Dec 2024"
    })


# ── Student Session Monitoring Endpoints ─────────────────────────────────────

@app.route("/api/session/heartbeat", methods=["POST", "OPTIONS"])
def session_heartbeat():
    """Receives periodic heartbeat (~60s) from active student portal tabs."""
    if request.method == "OPTIONS":
        return "", 204

    body = request.get_json(silent=True) or {}
    session_id = str(body.get("sessionId", "")).strip()

    if session_id:
        try:
            db.heartbeat_session(session_id)
        except Exception as e:
            logger.error("Session heartbeat error: %s", e)

    return jsonify(status="ok")


@app.route("/api/session/logout", methods=["POST", "OPTIONS"])
def session_logout():
    """Records student logout when user clicks logout or closes browser."""
    if request.method == "OPTIONS":
        return "", 204

    # Support JSON or beacon body
    body = request.get_json(silent=True) or {}
    session_id = str(body.get("sessionId", "")).strip()
    if not session_id and request.data:
        try:
            import json
            data = json.loads(request.data.decode("utf-8"))
            session_id = str(data.get("sessionId", "")).strip()
        except Exception:
            pass

    if session_id:
        try:
            db.logout_session(session_id)
        except Exception as e:
            logger.error("Session logout error: %s", e)

    return jsonify(status="ok")


# ── Admin Authentication Endpoints ───────────────────────────────────────────

@app.route("/api/admin/auth", methods=["POST", "OPTIONS"])
@rate_limit(limit=5, window=300)  # 5 attempts per 5 minutes for admin
@track_performance
def admin_auth_login():
    if request.method == "OPTIONS":
        return "", 204

    client_ip = admin_auth.get_client_ip()

    # Brute-force rate limiting: max 5 failed attempts per 15 minutes
    if admin_auth.is_rate_limited(client_ip):
        return jsonify(error="Too many failed login attempts. Please wait 15 minutes before trying again."), 429

    body = request.get_json(silent=True) or {}
    submitted_token = str(body.get("token", "")).strip()

    if not admin_auth.verify_token_match(submitted_token):
        admin_auth.record_failed_attempt(client_ip)
        return jsonify(error="Invalid administrator token."), 401

    # Reset attempts on success and issue signed session cookie
    admin_auth.reset_failed_attempts(client_ip)
    session_token = admin_auth.create_session_token()

    resp = make_response(jsonify(success=True, message="Admin authenticated successfully."))
    admin_auth.set_admin_cookie(resp, session_token)
    return resp


@app.route("/api/admin/logout", methods=["POST", "OPTIONS"])
def admin_auth_logout():
    if request.method == "OPTIONS":
        return "", 204

    resp = make_response(jsonify(success=True, message="Admin logged out successfully."))
    admin_auth.clear_admin_cookie(resp)
    return resp


@app.route("/api/admin/me", methods=["GET"])
def admin_auth_me():
    is_authed = admin_auth.is_admin_authenticated()
    return jsonify(authenticated=is_authed)


# ── Admin Protected Monitoring APIs ──────────────────────────────────────────

@app.route("/api/admin/stats", methods=["GET"])
@admin_auth.admin_required
def admin_stats():
    """Returns aggregate dashboard metrics."""
    stats = db.get_admin_stats()
    return jsonify(stats)


@app.route("/api/admin/login-activity", methods=["GET"])
@admin_auth.admin_required
def admin_login_activity():
    """Returns paginated, searchable, filterable login activity records."""
    try:
        page = int(request.args.get("page", 1))
    except ValueError:
        page = 1

    try:
        limit = int(request.args.get("limit", 25))
    except ValueError:
        limit = 25

    search = request.args.get("search", "").strip()
    status = request.args.get("status", "").strip()
    date_filter = request.args.get("date", "").strip()
    sort = request.args.get("sort", "latest").strip()

    data_result = db.get_login_activity(
        page=page,
        limit=limit,
        search=search,
        status=status,
        date_filter=date_filter,
        sort=sort
    )
    return jsonify(data_result)


@app.route("/api/admin/active-sessions", methods=["GET"])
@admin_auth.admin_required
def admin_active_sessions():
    """Returns currently active student sessions (heartbeat within 5 min)."""
    sessions = db.get_active_sessions()
    return jsonify(sessions)


@app.route("/api/admin/student-history", methods=["GET"])
@admin_auth.admin_required
def admin_student_history():
    """Returns login and session history for a specific roll number."""
    roll = request.args.get("roll", "").strip()
    if not roll:
        return jsonify(error="Roll number parameter 'roll' is required."), 400

    history = db.get_student_history(roll)
    return jsonify(history)


@app.route("/api/admin/export-csv", methods=["GET"])
@admin_auth.admin_required
def admin_export_csv():
    """Exports all student login activity as a downloadable CSV."""
    records = db.get_all_activity_for_export()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Roll Number",
        "Student Name",
        "Login Status",
        "Failure Reason",
        "Browser",
        "Device",
        "Login Time (IST)",
        "Created At (UTC)"
    ])

    for r in records:
        writer.writerow([
            r.get("roll_number", ""),
            r.get("student_name", ""),
            r.get("login_status", ""),
            r.get("failure_reason", ""),
            r.get("browser", ""),
            r.get("device", ""),
            r.get("login_time_ist", ""),
            r.get("created_at_utc", "")
        ])

    today_str = datetime.now().strftime("%Y-%m-%d")
    filename = f"mits_one_login_activity_{today_str}.csv"

    response = Response(output.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = f"attachment; filename={filename}"
    return response


# ── Server Runner ─────────────────────────────────────────────────────────────

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
    print(f"👉 Student Portal: http://127.0.0.1:{port}")
    print(f"🔒 Admin Portal:   http://127.0.0.1:{port}/admin")
    if local_ip != "127.0.0.1":
        print(f"📱 Phone (Wi-Fi):  http://{local_ip}:{port}")
    print("=" * 64)
    print("Press Ctrl+C to stop the server.\n")

    app.run(host=host, port=port, threaded=True)
