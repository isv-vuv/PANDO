import unittest
from unittest.mock import patch

from core.app.app_core.update_checker import (
    UpdateCheckResult,
    check_for_updates,
    get_git_status,
    get_local_commit_info,
    perform_git_pull,
)


class UpdateCheckerTests(unittest.TestCase):
    def test_get_local_commit_info_returns_tuple(self):
        commit_hash, commit_date = get_local_commit_info()
        self.assertIsInstance(commit_hash, str)
        self.assertIsInstance(commit_date, str)

    @patch("core.app.app_core.update_checker.get_git_status")
    def test_check_for_updates_up_to_date(self, mock_status):
        mock_status.return_value = {
            "is_git": True,
            "branch": "main",
            "upstream": "origin/main",
            "behind_count": 0,
            "ahead_count": 0,
            "deleted_files": [],
            "modified_files": [],
            "fetch_ok": True,
            "local_commit": "abc123456789",
            "remote_commit": "abc123456789",
        }

        res = check_for_updates()
        self.assertFalse(res.has_update)
        self.assertEqual(res.status_code, "UP_TO_DATE")

    @patch("core.app.app_core.update_checker.get_git_status")
    def test_check_for_updates_newer_commit(self, mock_status):
        mock_status.return_value = {
            "is_git": True,
            "branch": "main",
            "upstream": "origin/main",
            "behind_count": 3,
            "ahead_count": 0,
            "deleted_files": [],
            "modified_files": [],
            "fetch_ok": True,
            "local_commit": "abc123456789",
            "remote_commit": "def987654321",
        }

        res = check_for_updates()
        self.assertTrue(res.has_update)
        self.assertEqual(res.status_code, "UPDATE_AVAILABLE")
        self.assertEqual(res.behind_count, 3)

    @patch("core.app.app_core.update_checker.get_git_status")
    def test_check_for_updates_missing_files(self, mock_status):
        mock_status.return_value = {
            "is_git": True,
            "branch": "main",
            "upstream": "origin/main",
            "behind_count": 0,
            "ahead_count": 0,
            "deleted_files": ["core/some_file.py"],
            "modified_files": [],
            "fetch_ok": True,
            "local_commit": "abc123456789",
            "remote_commit": "abc123456789",
        }

        res = check_for_updates()
        self.assertTrue(res.has_update)
        self.assertEqual(res.status_code, "LOCAL_FILES_MISSING")
        self.assertEqual(res.missing_files_count, 1)

    @patch("core.app.app_core.update_checker.get_git_status")
    def test_check_for_updates_offline(self, mock_status):
        mock_status.return_value = {
            "is_git": True,
            "branch": "main",
            "upstream": "origin/main",
            "behind_count": 0,
            "ahead_count": 0,
            "deleted_files": [],
            "modified_files": [],
            "fetch_ok": False,
            "local_commit": "abc123456789",
            "remote_commit": "",
        }

        res = check_for_updates()
        self.assertFalse(res.has_update)
        self.assertEqual(res.status_code, "OFFLINE")


if __name__ == "__main__":
    unittest.main()
