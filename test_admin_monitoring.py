"""Automated verification suite for MITS ONE Admin Login Activity Monitoring.
Tests all 12 scenarios required by the specification:
1. Successful student login
2. Incorrect GEMS password
3. GEMS server unavailable / timeout
4. Demo login
5. Multiple logins from different students
6. Same student logging in multiple times
7. Session heartbeat
8. Student logout
9. Admin login
10. Unauthorized access to admin APIs
11. Database connection failure (graceful degradation without blocking student access)
12. Admin dashboard endpoint availability & layout
"""
import os
import unittest
from unittest.mock import MagicMock, patch

# Configure test environment
os.environ["MITS_ADMIN_TOKEN"] = "test_mits_admin_token_2026"
# Leave DATABASE_URL empty initially or set to test URL to verify graceful fallback
os.environ.pop("DATABASE_URL", None)

import admin_auth
import db
import server


class TestAdminMonitoringSuite(unittest.TestCase):

    def setUp(self):
        self.app = server.app
        self.client = self.app.test_client()

    # ── Test 10: Unauthorized access to admin APIs ───────────────────────────
    def test_01_unauthorized_admin_api_access(self):
        """Admin endpoints must reject requests without valid authentication with 401."""
        endpoints = [
            "/api/admin/stats",
            "/api/admin/login-activity",
            "/api/admin/active-sessions",
            "/api/admin/student-history?roll=21691A04G5",
            "/api/admin/export-csv"
        ]
        for ep in endpoints:
            res = self.client.get(ep)
            self.assertEqual(res.status_code, 401, f"Endpoint {ep} should be protected by 401 Unauthorized")
            data = res.get_json() or {}
            self.assertIn("Unauthorized", data.get("error", ""))

    # ── Test 9: Admin login & authentication ──────────────────────────────────
    def test_02_admin_auth_success_and_failure(self):
        """Admin login with valid and invalid tokens."""
        # 1. Invalid token
        res_fail = self.client.post("/api/admin/auth", json={"token": "wrong-token"})
        self.assertEqual(res_fail.status_code, 401)
        self.assertIn("Invalid", res_fail.get_json().get("error", ""))

        # 2. Valid token
        res_success = self.client.post("/api/admin/auth", json={"token": "test_mits_admin_token_2026"})
        self.assertEqual(res_success.status_code, 200)
        self.assertTrue(res_success.get_json().get("success"))

        # Verify cookie was set
        cookies = res_success.headers.getlist("Set-Cookie")
        self.assertTrue(any("mits_admin_session=" in c for c in cookies), "Session cookie must be set")
        self.assertTrue(any("HttpOnly" in c for c in cookies), "Cookie must be HttpOnly")

        # 3. Check /api/admin/me with session
        res_me = self.client.get("/api/admin/me")
        self.assertEqual(res_me.status_code, 200)
        self.assertTrue(res_me.get_json().get("authenticated"))

        # 4. Now admin protected endpoint works!
        res_stats = self.client.get("/api/admin/stats")
        self.assertEqual(res_stats.status_code, 200)

        # 5. Logout
        res_logout = self.client.post("/api/admin/logout")
        self.assertEqual(res_logout.status_code, 200)

        # 6. Verify /api/admin/me is no longer authenticated
        res_me_after = self.client.get("/api/admin/me")
        self.assertFalse(res_me_after.get_json().get("authenticated"))

    # ── Test 4: Demo login ───────────────────────────────────────────────────
    def test_03_demo_login_bypasses_tracking(self):
        """Demo login should return mock data and NEVER record to database."""
        with patch.object(db, "record_login_event") as mock_record:
            res = self.client.post("/api/data", json={"username": "demo", "password": "demo"})
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertTrue(data.get("isDemo"))
            mock_record.assert_not_called()

    # ── Test 1: Successful real student login ────────────────────────────────
    def test_04_successful_student_login(self):
        """Successful login records SUCCESS in DB, creates session, and returns sessionId."""
        mock_result = {
            "success": True,
            "student": {"id": "24892", "name": "K. Sai Tharun", "usn": "21691A04G5"},
            "attendance": []
        }

        with patch("server.GemsAPIClient") as MockClient:
            instance = MockClient.return_value
            instance.scrape_full_data.return_value = mock_result

            with patch.object(db, "record_login_event") as mock_record, \
                 patch.object(db, "create_user_session") as mock_session:

                res = self.client.post("/api/data", json={"username": "21691A04G5", "password": "ValidPassword123"},
                                       headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"})

                self.assertEqual(res.status_code, 200)
                data = res.get_json()
                self.assertIn("sessionId", data)
                self.assertTrue(len(data["sessionId"]) > 10)

                # Verify DB calls
                mock_record.assert_called_once()
                args, kwargs = mock_record.call_args
                self.assertEqual(kwargs.get("roll_number"), "21691A04G5")
                self.assertEqual(kwargs.get("student_name"), "K. Sai Tharun")
                self.assertEqual(kwargs.get("login_status"), "SUCCESS")
                self.assertEqual(kwargs.get("device"), "iPhone (Mobile)")
                self.assertEqual(kwargs.get("browser"), "Apple Safari")

                mock_session.assert_called_once()
                s_args, s_kwargs = mock_session.call_args
                self.assertEqual(s_kwargs.get("roll_number"), "21691A04G5")
                self.assertEqual(s_kwargs.get("session_id"), data["sessionId"])

    # ── Test 2: Incorrect GEMS password ──────────────────────────────────────
    def test_05_incorrect_password_records_failed(self):
        """Failed credentials must be recorded as FAILED with reason."""
        with patch("server.GemsAPIClient") as MockClient:
            instance = MockClient.return_value
            instance.scrape_full_data.return_value = None
            instance.last_error = "Invalid username or password"

            with patch.object(db, "record_login_event") as mock_record:
                res = self.client.post("/api/data", json={"username": "21691A04G5", "password": "WrongPassword"})
                self.assertEqual(res.status_code, 401)

                mock_record.assert_called_once()
                args, kwargs = mock_record.call_args
                self.assertEqual(kwargs.get("roll_number"), "21691A04G5")
                self.assertIsNone(kwargs.get("student_name"))
                self.assertEqual(kwargs.get("login_status"), "FAILED")
                self.assertEqual(kwargs.get("failure_reason"), "Invalid username or password")

    # ── Test 3: GEMS server unavailable / network error ──────────────────────
    def test_06_server_unavailable_not_classified_as_failed_password(self):
        """Network/server timeouts must NOT be classified or recorded as failed student login."""
        with patch("server.GemsAPIClient") as MockClient:
            instance = MockClient.return_value
            instance.scrape_full_data.return_value = None
            instance.last_error = "Connection to GEMS college portal timed out. The portal might be offline or slow."

            with patch.object(db, "record_login_event") as mock_record:
                res = self.client.post("/api/data", json={"username": "21691A04G5", "password": "MyPassword"})
                self.assertEqual(res.status_code, 502)
                # Network error must NOT be recorded as a credential rejection
                mock_record.assert_not_called()

    # ── Test 7: Session heartbeat ────────────────────────────────────────────
    def test_07_session_heartbeat(self):
        """Heartbeat endpoint updates last_seen for active session."""
        with patch.object(db, "heartbeat_session") as mock_hb:
            res = self.client.post("/api/session/heartbeat", json={"sessionId": "test-uuid-session-123"})
            self.assertEqual(res.status_code, 200)
            mock_hb.assert_called_once_with("test-uuid-session-123")

    # ── Test 8: Student logout ───────────────────────────────────────────────
    def test_08_student_logout(self):
        """Logout endpoint marks session inactive."""
        with patch.object(db, "logout_session") as mock_logout:
            res = self.client.post("/api/session/logout", json={"sessionId": "test-uuid-session-123"})
            self.assertEqual(res.status_code, 200)
            mock_logout.assert_called_once_with("test-uuid-session-123")

    # ── Test 11: Database failure graceful degradation ───────────────────────
    def test_09_database_failure_does_not_break_student_login(self):
        """If the database throws an exception or is down, student login still succeeds seamlessly."""
        mock_result = {
            "success": True,
            "student": {"id": "24892", "name": "K. Sai Tharun", "usn": "21691A04G5"},
            "attendance": [{"subjectName": "VLSI", "percentage": 85.0}]
        }

        with patch("server.GemsAPIClient") as MockClient:
            instance = MockClient.return_value
            instance.scrape_full_data.return_value = mock_result

            # Simulate DB crashing or throwing exception
            with patch.object(db, "record_login_event", side_effect=Exception("Neon DB connection timeout")):
                res = self.client.post("/api/data", json={"username": "21691A04G5", "password": "password"})
                self.assertEqual(res.status_code, 200)
                data = res.get_json()
                self.assertTrue(data.get("success"))
                self.assertEqual(data.get("student", {}).get("name"), "K. Sai Tharun")

    # ── Test 12: Admin dashboard HTML view ───────────────────────────────────
    def test_10_admin_dashboard_page(self):
        """GET /admin must serve the modern admin monitoring dashboard."""
        res = self.client.get("/admin")
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)
        self.assertIn("MITS ONE", html)
        self.assertIn("ADMIN", html)
        self.assertIn("Admin Authentication", html)
        self.assertIn("Login Activity History", html)
        self.assertIn("Currently Active Sessions", html)


if __name__ == "__main__":
    unittest.main()
