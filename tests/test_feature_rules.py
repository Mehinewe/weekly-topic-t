import unittest
import participation as P
import send_weekly_awards as awards


class FeatureRuleTests(unittest.TestCase):
    def test_award_ties_and_exclusions_are_deterministic(self):
        counts = {"2": 3, "1": 3}
        self.assertEqual(awards.pick_winner(counts), ("1", 3))
        self.assertEqual(awards.pick_winner(counts, exclude={"1"}), ("2", 3))
        self.assertIsNone(awards.pick_winner(counts, exclude={"1", "2"}))

    def test_social_award_falls_back_only_without_replies(self):
        rows = [{"user_id": "1", "is_reply": "0"}, {"user_id": "2", "is_reply": "0"}]
        self.assertEqual(dict(awards.tally(rows, "social")), {"1": 1, "2": 1})
        rows[1]["is_reply"] = "1"
        self.assertEqual(dict(awards.tally(rows, "social")), {"2": 1})

    def test_first_failure_warns_and_second_respects_removal_setting(self):
        for enabled, expected in ((False, "strike2_flagged"), (True, "remove")):
            member = {"strikes": "0", "status": "active"}
            cfg = {"auto_removal": enabled}
            self.assertEqual(P.apply_week_result(member, False, "2026-09-07", cfg), "strike1")
            self.assertEqual(P.apply_week_result(member, False, "2026-09-14", cfg), expected)

    def test_success_streak_clears_strike_at_configured_boundary(self):
        member = {"strikes": "1", "success_streak": "2"}
        self.assertEqual(P.apply_week_result(member, True, "2026-09-07", {"clear_weeks": 4}), "streak")
        self.assertEqual(P.apply_week_result(member, True, "2026-09-14", {"clear_weeks": 4}), "cleared")
        self.assertEqual(member["strikes"], "0")

    def test_reminder_selection_excludes_completed_and_previously_reminded(self):
        from datetime import date
        from automation.reminders.rules import select_targets
        members = {uid: {} for uid in (1, 2, 3, 4)}
        result = select_targets(members, {1: 0, 2: 3, 3: 1, 4: 0},
                                {("2026-09-14", "3", "wed")}, date(2026, 9, 14),
                                "wed", 3, 1, False, lambda member, week: True)
        self.assertEqual(result, [(1, 0), (4, 0)])
