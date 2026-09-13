"""Offline failure tests; all state lives in temporary directories."""
import csv
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import log_activity
import participation
import send_weekly_topic as topic
from automation.shared.storage import atomic_write_text


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "state.json"
        self.journal = Path(self.temp.name) / "delivery.json"
        self.journal.write_text("{}")
        self.image = Path(self.temp.name) / "test.png"
        self.image.write_bytes(b"image")

    def test_failed_replace_preserves_original_and_cleans_temporary(self):
        self.path.write_text("original", encoding="utf-8")
        with patch("automation.shared.storage.os.replace", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                atomic_write_text(self.path, "replacement")
        self.assertEqual(self.path.read_text(), "original")
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_failed_csv_generation_preserves_original(self):
        self.path.write_text("original", encoding="utf-8")
        def rows():
            yield {"id": 1}
            raise ValueError("bad row")
        with self.assertRaises(ValueError):
            participation.write_rows(self.path, ["id"], rows())
        self.assertEqual(self.path.read_text(), "original")

    def test_unicode_csv_round_trip(self):
        participation.write_rows(self.path, ["id"], [{"id": "hello, ??"}])
        self.assertEqual(participation.read_rows(self.path), [{"id": "hello, ??"}])

    def test_corrupt_cursor_stops_instead_of_replaying_updates(self):
        with patch.object(log_activity, "STATE_FILE", self.path):
            self.assertIsNone(log_activity.read_offset())
            for text in ('{', '[]', '{}', '{"last_update_id": true}'):
                self.path.write_text(text)
                with self.assertRaises(ValueError):
                    log_activity.read_offset()
            log_activity.write_offset(123)
            self.assertEqual(log_activity.read_offset(), 124)

    def test_pin_failure_does_not_repost_delivered_topic(self):
        row = {"date": date(2026, 6, 22), "image": "test.png", "message": "hello"}
        with patch.object(topic, "DELIVERY_FILE", self.journal), \
             patch.object(topic, "SENT_LOG_FILE", self.path), \
             patch.object(topic, "load_dotenv"), \
             patch.object(topic, "load_schedule", return_value=[row]), \
             patch.object(topic, "resolve_image", return_value=self.image), \
             patch.object(topic, "send_photo", return_value=123) as send, \
             patch.object(topic, "pin_message", side_effect=SystemExit(1)), \
             patch("participation.record_topic_post"), \
             patch.dict("os.environ", TELEGRAM_BOT_TOKEN="test", TELEGRAM_CHAT_ID="test"), \
             patch("sys.argv", ["send_weekly_topic.py", "2026-06-22"]):
            with self.assertRaises(SystemExit):
                topic.main()
            topic.main()
            send.assert_called_once()

    def test_monday_dry_run_does_not_send_or_write(self):
        row = {"date": date(2026, 6, 22), "image": "test.png", "message": "hello"}
        with patch.object(topic, "DELIVERY_FILE", self.journal), \
             patch.object(topic, "SENT_LOG_FILE", self.path), \
             patch.object(topic, "load_dotenv"), \
             patch.object(topic, "load_schedule", return_value=[row]), \
             patch.object(topic, "resolve_image", return_value=self.image), \
             patch.object(topic, "send_photo") as send, \
             patch("sys.argv", ["send_weekly_topic.py", "2026-06-22", "--dry-run"]):
            topic.main()
            send.assert_not_called()
            self.assertFalse(self.path.exists())
