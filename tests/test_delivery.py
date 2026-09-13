"""Delivery recovery tests with no real API or repository state writes."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from automation.shared.delivery import Delivery, TelegramRejected


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "delivery.json"
        self.path.write_text("{}")

    def run_state(self, plan=None):
        return Delivery(self.path, "week", plan or {"content": "test"})

    def test_completed_steps_skipped_after_restart(self):
        first = self.run_state()
        first.send("photo", lambda: 11)
        with self.assertRaises(TelegramRejected):
            first.send("text", Mock(side_effect=TelegramRejected("rejected")))
        second = self.run_state()
        photo = Mock()
        self.assertEqual(second.send("photo", photo), 11)
        photo.assert_not_called()
        self.assertEqual(second.send("text", lambda: 12), 12)

    def test_uncertain_send_blocks_even_force(self):
        with self.assertRaises(TimeoutError):
            self.run_state().send("photo", Mock(side_effect=TimeoutError()))
        for force in (False, True):
            with self.assertRaisesRegex(ValueError, "Uncertain"):
                Delivery(self.path, "week", {}, force=force)

    def test_failed_pending_save_never_sends(self):
        send = Mock()
        with patch("automation.shared.delivery.atomic_write_text", side_effect=OSError()):
            with self.assertRaises(OSError):
                self.run_state().send("photo", send)
        send.assert_not_called()

    def test_failed_success_save_leaves_pending_on_disk(self):
        delivery = self.run_state()
        original = delivery.save
        calls = 0
        def save():
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("disk full")
            original()
        with patch.object(delivery, "save", side_effect=save):
            with self.assertRaises(OSError):
                delivery.send("photo", lambda: 11)
        with self.assertRaisesRegex(ValueError, "Uncertain"):
            self.run_state()

    def test_changed_inputs_fail_before_resuming(self):
        self.run_state().send("photo", lambda: 11)
        with self.assertRaisesRegex(ValueError, "inputs changed"):
            self.run_state({"content": "changed"})

    def test_missing_and_corrupt_journal_fail_closed(self):
        for text in ('{', '[]', '{"week": {}}'):
            self.path.write_text(text)
            with self.assertRaises(ValueError):
                self.run_state()
        self.path.unlink()
        with self.assertRaises(FileNotFoundError):
            self.run_state()

    def test_journal_does_not_store_plan_content(self):
        self.run_state({"content": "private caption"}).send("photo", lambda: 11)
        self.assertNotIn("private caption", self.path.read_text())

    def test_only_explicit_api_rejection_is_retryable(self):
        from automation.shared.telegram import _check
        for body in ({"ok": False, "error_code": 429}, {"error": "broken"}):
            self.path.write_text("{}")
            response = Mock()
            response.json.return_value = body
            error = TelegramRejected if body.get("ok") is False else ValueError
            with self.assertRaises(error):
                self.run_state().send("photo", lambda: _check(response, "sendPhoto"))
            if error is TelegramRejected:
                self.assertEqual(self.run_state().entry["steps"], {})
            else:
                with self.assertRaisesRegex(ValueError, "Uncertain"):
                    self.run_state()
