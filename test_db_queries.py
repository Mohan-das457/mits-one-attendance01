"""Test suite for database logic, SQL parameterization, and multi-user tracking.
"""
import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

import db


class TestDatabaseLogic(unittest.TestCase):

    def setUp(self):
        # Configure test DB state
        db.PSYCOPG2_AVAILABLE = True

    @patch("db.get_db_connection")
    def test_record_multiple_students_login(self, mock_get_conn):
        """Test recording multiple logins from different students."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_get_conn.return_value.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_cur.fetchone.return_value = [101]

        with patch("db.is_db_configured", return_value=True):
            # Student 1
            id1 = db.record_login_event("21691A04G5", "K. Sai Tharun", "SUCCESS", None, "Chrome", "Desktop")
            self.assertEqual(id1, 101)

            # Student 2
            id2 = db.record_login_event("21691A0501", "A. Rahul", "SUCCESS", None, "Safari", "Mobile")
            self.assertEqual(id2, 101)

            # Check parameterized queries were executed
            self.assertEqual(mock_cur.execute.call_count, 2)
            for call in mock_cur.execute.call_args_list:
                sql, params = call[0]
                self.assertIn("INSERT INTO login_events", sql)
                self.assertIn("%s", sql)  # Parameterized!

    @patch("db.get_db_connection")
    def test_same_student_multiple_logins(self, mock_get_conn):
        """Test recording multiple logins from the same student at different times."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_get_conn.return_value.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_cur.fetchone.return_value = [102]

        with patch("db.is_db_configured", return_value=True):
            # Same student logging in twice
            db.record_login_event("21691A04G5", "K. Sai Tharun", "SUCCESS", None, "Chrome", "macOS (Desktop)")
            db.record_login_event("21691A04G5", "K. Sai Tharun", "SUCCESS", None, "Safari", "iPhone (Mobile)")

            self.assertEqual(mock_cur.execute.call_count, 2)

    @patch("db.get_db_connection")
    def test_timezone_conversion_ist(self, mock_get_conn):
        """Test that UTC timestamps are accurately converted to IST (UTC+05:30)."""
        utc_time = datetime(2026, 11, 20, 10, 30, 0, tzinfo=timezone.utc)
        ist_str = db.utc_to_ist_display(utc_time)
        # 10:30 UTC + 5:30 = 16:00 (4:00 PM) IST
        self.assertIn("04:00:00 PM", ist_str)
        self.assertIn("20 Nov 2026", ist_str)

    @patch("db.get_db_connection")
    def test_login_activity_filters(self, mock_get_conn):
        """Test SQL query generation with search, status, and date filters."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_get_conn.return_value.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_cur.fetchone.return_value = {"count": 1}
        mock_cur.fetchall.return_value = [{
            "id": 1,
            "roll_number": "21691A04G5",
            "student_name": "K. Sai Tharun",
            "login_status": "SUCCESS",
            "failure_reason": None,
            "browser": "Chrome",
            "device": "Desktop",
            "created_at": datetime(2026, 11, 20, 10, 30, 0, tzinfo=timezone.utc),
            "last_active": None
        }]

        with patch("db.is_db_configured", return_value=True):
            result = db.get_login_activity(
                page=1,
                limit=10,
                search="Sai",
                status="SUCCESS",
                date_filter="2026-11-20"
            )

            self.assertEqual(len(result["events"]), 1)
            self.assertEqual(result["events"][0]["roll_number"], "21691A04G5")

            # Verify parameterized query was executed
            count_call = mock_cur.execute.call_args_list[0]
            count_sql, count_params = count_call[0]
            self.assertIn("roll_number ILIKE %s OR le.student_name ILIKE %s", count_sql)
            self.assertIn("login_status = %s", count_sql)
            self.assertIn("%Sai%", count_params)
            self.assertIn("SUCCESS", count_params)


if __name__ == "__main__":
    unittest.main()
