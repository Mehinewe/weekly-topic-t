import contextlib
import io
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import send_weekly_awards as awards
import send_weekly_topic as topics
from automation.shared.delivery import TelegramRejected


class PostingRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.journal = root / "delivery.json"
        self.journal.write_text("{}")
        self.sent = root / "sent.csv"
        self.media = root / "badge.png"
        self.media.write_bytes(b"image")
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.stack.enter_context(patch.dict("os.environ", TELEGRAM_BOT_TOKEN="test", TELEGRAM_CHAT_ID="test"))

    def mock(self, module, name, **kwargs):
        return self.stack.enter_context(patch.object(module, name, **kwargs))

    def prepare_awards(self, long=False):
        self.mock(awards, "DELIVERY_FILE", new=self.journal)
        self.mock(awards, "SENT_LOG_FILE", new=self.sent)
        self.mock(awards, "load_dotenv")
        rows = [{"key": key, "metric": "voice", "gif": "badge.png",
                 "badge_type": "voice", "message": "x" * (1100 if long else 10)}
                for key in ("first", "second")]
        self.mock(awards, "load_awards", return_value=rows)
        self.mock(awards, "load_week_rows", return_value=[{"user_id": "1", "type": "voice"},
                                                        {"user_id": "2", "type": "voice"}])
        self.mock(awards, "resolve_media", return_value=self.media)
        self.mock(awards, "resolve_name", return_value="Test member")
        self.mock(awards, "get_profile_photo", return_value=None)
        self.stack.enter_context(patch("sys.argv", ["awards", "2026-09-14"]))
        return rows

    def test_awards_resume_after_second_award_rejected(self):
        self.prepare_awards()
        send = self.mock(awards, "send_badge", side_effect=[11, TelegramRejected("rejected"), 12])
        with self.assertRaises(TelegramRejected):
            awards.main()
        self.assertFalse(self.sent.exists())
        awards.main()
        self.assertEqual(send.call_count, 3)
        awards.main()
        self.assertEqual(send.call_count, 3)

    def test_award_split_text_retries_without_repeating_badge(self):
        self.prepare_awards(long=True)
        send = self.mock(awards, "send_badge", side_effect=[11, 13])
        text = self.mock(awards, "send_message", side_effect=[TelegramRejected("rejected"), 12, 14])
        with self.assertRaises(TelegramRejected):
            awards.main()
        awards.main()
        self.assertEqual(send.call_count, 2)
        self.assertEqual(text.call_count, 3)

    def test_missing_badge_stops_before_any_delivery(self):
        self.prepare_awards()
        self.mock(awards, "resolve_media", side_effect=[self.media, None])
        send = self.mock(awards, "send_badge")
        with self.assertRaises(ValueError):
            awards.main()
        send.assert_not_called()
        self.assertFalse(self.sent.exists())

    def test_awards_preview_is_offline_even_with_credentials(self):
        self.prepare_awards()
        name = self.mock(awards, "resolve_name")
        photo = self.mock(awards, "get_profile_photo")
        send = self.mock(awards, "send_badge")
        with patch("sys.argv", ["awards", "2026-09-14", "--dry-run"]):
            awards.main()
        for call in (name, photo, send):
            call.assert_not_called()
        self.assertEqual(self.journal.read_text(), "{}")
        self.assertFalse(self.sent.exists())

    def test_monday_resumes_text_without_repeating_photo(self):
        self.mock(topics, "DELIVERY_FILE", new=self.journal)
        self.mock(topics, "SENT_LOG_FILE", new=self.sent)
        self.mock(topics, "load_dotenv")
        self.mock(topics, "load_schedule", return_value=[{
            "date": date(2026, 9, 14), "image": "badge.png", "message": "x" * 1100}])
        self.mock(topics, "resolve_image", return_value=self.media)
        photo = self.mock(topics, "send_photo", return_value=11)
        text = self.mock(topics, "send_message", side_effect=[TelegramRejected("rejected"), 12])
        self.mock(topics, "pin_message")
        self.stack.enter_context(patch("participation.record_topic_post"))
        with patch("sys.argv", ["topics", "2026-09-14"]):
            with self.assertRaises(TelegramRejected):
                topics.main()
            self.assertFalse(self.sent.exists())
            topics.main()
            topics.main()
        photo.assert_called_once()
        self.assertEqual(text.call_count, 2)
