"""Offline regression checks for the two-phase Wednesday posting flow."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import requests

import send_wednesday_idiom as idiom


class IdiomPostingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.schedule = root / "schedule.json"
        self.state = root / "state.json"
        self.images = root / "images"
        self.images.mkdir()
        (self.images / "ice.png").write_bytes(b"mock image; photo API is stubbed")
        self.row = {
            "date": "2026-09-16", "image": "ice.png", "idiom": "Break the ice",
            "hint": "Help new people feel comfortable.",
            "meaning": "Make people feel relaxed in a social situation.",
            "examples": ["I told a joke to break the ice.",
                         "A game can help break the ice."],
            "speaking_prompt": "How do you help someone feel welcome?",
        }
        self.write_schedule([self.row])
        self.state.write_text("{}\n", encoding="utf-8")
        for name, value in (("SCHEDULE_FILE", self.schedule),
                            ("STATE_FILE", self.state), ("IMAGES_DIR", self.images)):
            p = patch.object(idiom, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.env = patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "test-token",
                                           "TELEGRAM_CHAT_ID": "-123"})
        self.env.start()
        self.addCleanup(self.env.stop)
        for target, name in (("send_wednesday_idiom.topic.load_dotenv", "dotenv"),
                             ("send_wednesday_idiom.topic.send_photo", "photo"),
                             ("send_wednesday_idiom.send_reveal", "reveal"),
                             ("participation.record_topic_post", "register")):
            p = patch(target)
            setattr(self, name, p.start())
            self.addCleanup(p.stop)
        self.photo.return_value = 101
        self.reveal.return_value = 102

    def write_schedule(self, rows):
        self.schedule.write_text(json.dumps(rows), encoding="utf-8")

    def run_phase(self, phase, *extra):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            idiom.main(["--phase", phase, "--date", "2026-09-16", *extra])
        return output.getvalue()

    def test_dry_runs_never_send_load_secrets_or_write(self):
        before = self.state.read_bytes()
        challenge = self.run_phase("challenge", "--dry-run")
        reveal = self.run_phase("reveal", "--dry-run")
        self.assertNotIn("Break the ice", challenge)
        self.assertIn("Break the ice", reveal)
        self.assertEqual(self.state.read_bytes(), before)
        self.dotenv.assert_not_called()
        self.photo.assert_not_called()
        self.reveal.assert_not_called()
        self.register.assert_not_called()

    def test_phrasal_verbs_use_custom_posts_and_preserve_answer_snapshot(self):
        row = {"date": "2026-09-16", "image": "ice.png", "kind": "phrasal_verbs",
               "title": "Phrasal verbs with give", "challenge": "Complete with give: I won't ____.",
               "reveal": "Give up: stop trying. Use two expressions in a story."}
        self.write_schedule([row])
        before = self.state.read_bytes()
        preview = self.run_phase("challenge", "--dry-run")
        self.assertIn(row["challenge"], preview)
        self.assertNotIn("give up", preview.lower())
        self.assertNotIn("IDIOM CHALLENGE", preview)
        self.assertEqual(self.state.read_bytes(), before)
        self.run_phase("challenge")
        self.assertEqual(self.photo.call_args.args[3], row["challenge"])
        saved = idiom.load_state()[row["date"]]
        self.assertEqual(saved["idiom"], row["title"])
        original_answer = row["reveal"]
        row["reveal"] = "Edited after the morning post"
        self.write_schedule([row])
        self.run_phase("reveal")
        self.assertEqual(self.reveal.call_args.args[2], original_answer)

    def test_phrasal_verbs_validate_custom_content(self):
        row = {"date": "2026-09-16", "image": "ice.png", "kind": "phrasal_verbs",
               "title": "Give", "challenge": "A puzzle", "reveal": "An answer"}
        for change in ({"kind": "unknown"}, {"title": ""}, {"challenge": ""},
                       {"reveal": None}, {"challenge": "x" * 1025},
                       {"reveal": "x" * 4097}):
            with self.subTest(change=next(iter(change))):
                self.write_schedule([dict(row, **change)])
                with self.assertRaises(ValueError):
                    self.run_phase("challenge")
        self.photo.assert_not_called()

    def test_each_phase_sends_once_and_reveal_replies_to_picture(self):
        self.run_phase("challenge")
        self.run_phase("challenge")
        self.run_phase("reveal")
        self.run_phase("reveal")
        self.photo.assert_called_once()
        self.reveal.assert_called_once()
        self.assertEqual(self.reveal.call_args.args[3], 101)
        saved = idiom.load_state()["2026-09-16"]
        self.assertEqual(saved["challenge_message_id"], 101)
        self.assertEqual(saved["reveal_message_id"], 102)

    def test_reveal_uses_saved_answer_even_if_schedule_disappears(self):
        self.run_phase("challenge")
        original = idiom.messages(self.row)[1]
        self.schedule.unlink()
        self.run_phase("reveal")
        self.assertEqual(self.reveal.call_args.args[2], original)

    def test_reveal_requires_morning_post(self):
        with self.assertRaisesRegex(ValueError, "orphan reveal"):
            self.run_phase("reveal")
        self.reveal.assert_not_called()
        self.dotenv.assert_not_called()
        self.assertEqual(idiom.load_state(), {})

    def test_failed_challenge_does_not_mark_sent(self):
        self.photo.side_effect = requests.ConnectionError("mock connection failure")
        with self.assertRaises(requests.ConnectionError):
            self.run_phase("challenge")
        self.assertEqual(idiom.load_state(), {})

    def test_failed_reveal_can_retry_without_reposting_challenge(self):
        self.run_phase("challenge")
        self.reveal.side_effect = requests.ConnectionError("mock connection failure")
        with self.assertRaises(requests.ConnectionError):
            self.run_phase("reveal")
        self.assertNotIn("reveal_message_id", idiom.load_state()["2026-09-16"])
        self.reveal.side_effect = None
        self.run_phase("reveal")
        self.photo.assert_called_once()
        self.assertEqual(idiom.load_state()["2026-09-16"]["reveal_message_id"], 102)

    def test_participation_registration_failure_does_not_duplicate_post(self):
        self.register.side_effect = OSError("mock registration failure")
        self.run_phase("challenge")
        self.run_phase("challenge")
        self.photo.assert_called_once()

    def test_missing_week_never_falls_back_to_old_content(self):
        self.row["date"] = "2026-09-09"
        self.write_schedule([self.row])
        with self.assertRaisesRegex(ValueError, "No idiom scheduled"):
            self.run_phase("challenge")
        self.photo.assert_not_called()

    def test_invalid_schedule_fails_before_send(self):
        cases = [
            ("duplicate", [self.row, self.row]),
            ("wrong weekday", [dict(self.row, date="2026-09-17")]),
            ("missing image", [dict(self.row, image="missing.png")]),
            ("escaped image", [dict(self.row, image="../outside.png")]),
            ("too few examples", [dict(self.row, examples=["Only one"])]),
            ("oversized caption", [dict(self.row, hint="x" * 1024)]),
            ("oversized answer", [dict(self.row, meaning="x" * 4096)]),
        ]
        for label, rows in cases:
            with self.subTest(label=label):
                self.write_schedule(rows)
                with self.assertRaises(ValueError):
                    self.run_phase("challenge")
        self.photo.assert_not_called()

    def test_corrupt_or_missing_state_is_not_treated_as_empty(self):
        self.state.write_text('{"2026-09-16": {}}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Invalid idiom state"):
            self.run_phase("challenge")
        self.state.unlink()
        with self.assertRaises(FileNotFoundError):
            self.run_phase("challenge")
        self.photo.assert_not_called()

    def test_scheduled_windows_prevent_late_challenges_and_early_answers(self):
        for timestamp, challenge, reveal in [
            ("2026-09-16T10:31:59+00:00", False, False),
            ("2026-09-16T10:32:00+00:00", True, False),
            ("2026-09-16T18:31:59+00:00", True, False),
            ("2026-09-16T18:32:00+00:00", False, True),
            ("2026-09-16T23:59:59+00:00", False, True),
            ("2026-09-17T00:00:00+00:00", False, False),
        ]:
            now = datetime.fromisoformat(timestamp)
            with self.subTest(timestamp=timestamp):
                self.assertEqual(idiom.in_scheduled_window("challenge", now), challenge)
                self.assertEqual(idiom.in_scheduled_window("reveal", now), reveal)

    def test_delayed_scheduled_job_never_sends(self):
        with patch.object(idiom, "datetime") as clock:
            clock.now.return_value = datetime(2026, 9, 17, 1, tzinfo=timezone.utc)
            with contextlib.redirect_stdout(io.StringIO()):
                idiom.main(["--phase", "challenge", "--scheduled"])
        self.photo.assert_not_called()
        self.assertEqual(idiom.load_state(), {})


class TelegramReplyTests(unittest.TestCase):
    def test_reveal_uses_telegram_reply_parameters(self):
        with patch.object(idiom.requests, "post") as post:
            post.return_value.json.return_value = {"ok": True, "result": {"message_id": 77}}
            with contextlib.redirect_stdout(io.StringIO()):
                result = idiom.send_reveal("fake-token", "-123", "Answer", 42)
        self.assertEqual(result, 77)
        self.assertEqual(post.call_args.kwargs["json"]["reply_parameters"], {"message_id": 42})


if __name__ == "__main__":
    unittest.main()
