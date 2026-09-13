import contextlib
import copy
import io
import unittest
from datetime import date
from unittest.mock import patch

import participation as P
import evaluate_participation as evaluation
import send_reminders as reminders
from validate_project import validate_participation


class ParticipationSafetyTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
        self.stack.enter_context(patch.dict("os.environ", TELEGRAM_BOT_TOKEN="test", TELEGRAM_CHAT_ID="test"))
        self.cfg = copy.deepcopy(P.DEFAULT_CONFIG)
        self.messages = {key: "test {required_days}" for key in
                         ("reminder_first", "reminder_second", "warning_strike1",
                          "strike_cleared", "removal_notice")}
        self.member = P.blank_member("1", "2026-01-01", "2026-01-01")
        self.member["status"] = "active"
        for name, value in (("load_config", self.cfg), ("load_messages", self.messages),
                            ("today_in_tz", date(2026, 9, 14)),
                            ("load_members", {"1": self.member}), ("read_rows", [])):
            self.stack.enter_context(patch.object(P, name, return_value=value))
        self.stack.enter_context(patch.object(P, "load_dotenv"))
        self.side_effects = [self.stack.enter_context(patch.object(P, name)) for name in
                             ("send_dm", "send_chat", "kick_member", "save_members", "append_row", "resolve_name")]

    def test_reminder_preview_has_no_network_or_writes(self):
        with patch.object(P, "active_day_counts", return_value={}), \
             patch("sys.argv", ["reminders", "--which", "wed", "--dry-run"]):
            reminders.main()
        for call in self.side_effects:
            call.assert_not_called()

    def test_evaluation_preview_has_no_network_or_writes(self):
        with patch.object(P, "active_day_counts", return_value={"1": 1}), \
             patch("sys.argv", ["evaluation", "--dry-run"]):
            evaluation.main()
        for call in self.side_effects:
            call.assert_not_called()

    def test_missing_activity_aborts_before_member_changes(self):
        before = copy.deepcopy(self.member)
        with patch.object(P, "active_day_counts", return_value={}), \
             patch("sys.argv", ["evaluation", "--dry-run"]):
            with self.assertRaises(SystemExit):
                evaluation.main()
        self.assertEqual(before, self.member)
        for call in self.side_effects:
            call.assert_not_called()

    def test_invalid_reminder_and_evaluation_settings_are_rejected(self):
        validate_participation(self.cfg, self.messages)
        for key, value in (("required_days", 8), ("clear_weeks", 0), ("auto_removal", "false")):
            cfg = copy.deepcopy(self.cfg)
            cfg[key] = value
            with self.assertRaises(ValueError):
                validate_participation(cfg, self.messages)
        self.cfg["reminders"]["first"]["at_most_active_days"] = -1
        with self.assertRaises(ValueError):
            validate_participation(self.cfg, self.messages)
