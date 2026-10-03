"""Offline checks for the shadowing challenge: rules, DM/vote handling, phases."""

import contextlib
import io
import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

import send_shadowing as send
import shadowing as S

WEEK = "2026-10-05"          # a Monday
TODAY = date(2026, 10, 6)


def dm(uid, **media):
    return {"from": {"id": uid}, "chat": {"id": uid, "type": "private"},
            "date": int(datetime(2026, 10, 6, 9, tzinfo=timezone.utc).timestamp()), **media}


def video(file_id):
    return {"video": {"file_id": file_id, "file_unique_id": "u" + file_id}}


class ShadowingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        for name, value in (("PARTICIPANTS_FILE", root / "p.csv"), ("ENTRIES_FILE", root / "e.csv"),
                            ("VOTES_FILE", root / "v.csv"), ("STATE_FILE", root / "s.json")):
            patcher = patch.object(S, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        S.STATE_FILE.write_text("{}\n", encoding="utf-8")
        self.messages = S.load_messages()
        self.cfg = {"shadowing": {"enabled": True, "ranking_size": 3}}

    def join(self, *uids):
        rows = S.load_participants()
        for uid in uids:
            rows[uid] = {"user_id": str(uid), "joined_at": "x"}
        S.save_participants(rows)

    def open_week(self):
        return {WEEK: {"video_message_id": 5}}

    # --- rules ---------------------------------------------------------
    def test_numbering_is_reproducible_and_complete(self):
        first = S.assign_numbers([10, 20, 30], WEEK)
        self.assertEqual(first, S.assign_numbers([30, 10, 20], WEEK))
        self.assertEqual(sorted(first), ["1", "2", "3"])
        self.assertEqual(sorted(first.values()), [10, 20, 30])

    def test_tally_ignores_self_votes_and_unknown_targets(self):
        numbering = {"1": 10, "2": 20, "3": 30}
        counts = dict(S.tally({10: 10, 20: 10, 30: 10, 40: 999}, numbering))
        self.assertEqual(counts, {10: 2, 20: 0, 30: 0})

    def test_rank_breaks_ties_by_earliest_submission_and_skips_zero_votes(self):
        entries = {1: {"submitted_at": "2026-10-06T09"}, 2: {"submitted_at": "2026-10-05T09"},
                   3: {"submitted_at": "2026-10-04T09"}}
        counts = [(1, 2), (2, 2), (3, 0)]
        ranked = S.rank(counts, entries, 3)
        self.assertEqual(ranked, [(2, 2), (1, 2)])
        self.assertFalse(S.tie_affects_ranking(counts, ranked))
        self.assertTrue(S.tie_affects_ranking(counts, S.rank(counts, entries, 1)))
        self.assertEqual(S.rank([(1, 0), (2, 0)], entries, 1), [])

    def test_ballot_excludes_own_video(self):
        rows = S.ballot_buttons(WEEK, {"1": 10, "2": 20, "3": 30}, 20)
        labels = [b["text"] for row in rows for b in row]
        self.assertEqual(labels, ["Video 1", "Video 3"])
        self.assertEqual(S.parse_callback(rows[0][0]["callback_data"]), (WEEK, "1"))
        self.assertIsNone(S.parse_callback("other:1:2"))

    # --- DMs -----------------------------------------------------------
    def test_submission_requires_opt_in_and_an_open_week(self):
        state = self.open_week()
        text, activity = S.handle_submission(dm(10, **video("a")), self.cfg, self.messages, state, TODAY)
        self.assertIn("/join_challenge", text)
        self.assertEqual(S.load_entries(WEEK), {})
        self.join(10)
        text, _ = S.handle_submission(dm(10, **video("a")), self.cfg, self.messages, {}, TODAY)
        self.assertIn("no shadowing video", text.lower())

    def test_resubmission_replaces_and_counts_practice_once(self):
        self.join(10)
        state = self.open_week()
        _, activity = S.handle_submission(dm(10, **video("a")), self.cfg, self.messages, state, TODAY)
        self.assertEqual((activity["type"], activity["practice"], activity["user_id"]), ("shadowing", 1, 10))
        _, again = S.handle_submission(dm(10, **video("b")), self.cfg, self.messages, state, TODAY)
        self.assertIsNone(again)
        self.assertEqual(S.load_entries(WEEK)[10]["file_id"], "b")

    def test_submissions_close_once_numbering_exists(self):
        self.join(10)
        state = self.open_week()
        state[WEEK]["numbering"] = {"1": 10, "2": 20}
        text, _ = S.handle_submission(dm(10, **video("a")), self.cfg, self.messages, state, TODAY)
        self.assertNotIn(10, S.load_entries(WEEK))

    def test_join_and_leave_commands(self):
        message = {"text": "/join_challenge", "from": {"id": 10},
                   "chat": {"id": -1, "type": "supergroup"}}
        with patch.object(S, "is_group_member", return_value=True):
            first = S.handle_command(message, "t", -1, self.cfg, self.messages, {}, TODAY)
            second = S.handle_command(message, "t", -1, self.cfg, self.messages, {}, TODAY)
        self.assertIn("American Accent Challenge", first)
        self.assertIn("Start", first)                      # group join reminds them to open the bot
        self.assertIn("already", second)
        self.assertEqual(list(S.load_participants()), [10])
        message["text"] = "/leave_challenge"
        S.handle_command(message, "t", -1, self.cfg, self.messages, {}, TODAY)
        self.assertEqual(S.load_participants(), {})

    def test_join_link_start_payload_joins_from_the_private_chat(self):
        message = {"text": "/start join", "from": {"id": 55}, "chat": {"id": 55, "type": "private"}}
        with patch.object(S, "is_group_member", return_value=True):
            text = S.handle_command(message, "t", -1, self.cfg, self.messages, {}, TODAY)
        self.assertIn("American Accent Challenge", text)
        self.assertNotIn("Start", text)                    # already in the private chat
        self.assertEqual(list(S.load_participants()), [55])
        with patch.object(S, "P") as p:
            p.tg.return_value = {"ok": True, "result": {"username": "MyBot"}}
            self.assertEqual(S.join_link("t"), "https://t.me/MyBot?start=join")
            p.tg.return_value = {"ok": False}
            self.assertIsNone(S.join_link("t"))

    def test_dm_join_requires_group_membership(self):
        message = {"text": "/join_challenge", "from": {"id": 99}, "chat": {"id": 99, "type": "private"}}
        with patch.object(S, "is_group_member", return_value=False):
            text = S.handle_command(message, "t", -1, self.cfg, self.messages, {}, TODAY)
        self.assertIn("member of the group", text)
        self.assertEqual(S.load_participants(), {})

    # --- voting --------------------------------------------------------
    def voting_state(self):
        return {WEEK: {"video_message_id": 5, "showcase_sent": True,
                       "numbering": {"1": 10, "2": 20, "3": 30}}}

    def tap(self, voter, number, state):
        return S.handle_callback({"from": {"id": voter}, "data": f"sv:{WEEK}:{number}"},
                                 self.cfg, self.messages, state)

    def test_vote_rules(self):
        self.join(10, 20)
        state = self.voting_state()
        ok, text, _ = self.tap(10, 2, state)
        self.assertTrue(ok)
        self.assertEqual(S.load_votes(WEEK), {10: 20})
        ok, text, _ = self.tap(10, 3, state)                 # changing a vote replaces it
        self.assertEqual(S.load_votes(WEEK), {10: 30})
        ok, text, _ = self.tap(10, 1, state)                 # own video
        self.assertFalse(ok)
        self.assertEqual(S.load_votes(WEEK), {10: 30})
        ok, text, _ = self.tap(10, 9, state)                 # no such video
        self.assertFalse(ok)
        ok, text, _ = self.tap(77, 2, state)                 # group member, not in the challenge: may vote
        self.assertTrue(ok)
        self.assertEqual(S.load_votes(WEEK)[77], 20)
        self.assertNotIn(77, S.load_participants())          # voting does not enrol them
        state[WEEK]["results_sent"] = True                   # voting closed
        ok, text, _ = self.tap(20, 1, state)
        self.assertFalse(ok)
        self.assertNotIn(20, S.load_votes(WEEK))

    def test_vote_link_sends_ballot_to_non_participant_group_member(self):
        state = self.voting_state()
        msg = {"text": "/start vote", "from": {"id": 55}, "chat": {"id": 55, "type": "private"}}
        with patch.object(S, "is_group_member", return_value=True),                 patch.object(S, "send_ballot", return_value=(True, "ok")) as sb:
            reply = S.handle_command(msg, "t", -1, self.cfg, self.messages, state, TODAY)
        self.assertIsNone(reply)
        sb.assert_called_once()
        with patch.object(S, "is_group_member", return_value=False):
            reply = S.handle_command(msg, "t", -1, self.cfg, self.messages, state, TODAY)
        self.assertIn("members of the group", reply)

    def test_typed_vote_is_dm_only(self):
        self.join(10)
        group = {"text": "/vote 2", "from": {"id": 10}, "chat": {"id": -1, "type": "supergroup"}}
        self.assertIn("private", S.handle_command(group, "t", -1, self.cfg, self.messages,
                                                  self.voting_state(), TODAY))
        private = {"text": "/vote 2", "from": {"id": 10}, "chat": {"id": 10, "type": "private"}}
        S.handle_command(private, "t", -1, self.cfg, self.messages, self.voting_state(), TODAY)
        self.assertEqual(S.load_votes(WEEK), {10: 20})


class PhaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        for name, value in (("PARTICIPANTS_FILE", root / "p.csv"), ("ENTRIES_FILE", root / "e.csv"),
                            ("VOTES_FILE", root / "v.csv"), ("STATE_FILE", root / "s.json")):
            patcher = patch.object(S, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        S.STATE_FILE.write_text("{}\n", encoding="utf-8")
        self.messages = S.load_messages()
        self.cfg = {"shadowing": {"enabled": True, "ranking_size": 2}}
        self.sent = []
        self.podium = []
        for name, fake in (("send_text", lambda t, c, text, button=None: self.sent.append(("text", text)) or 1),
                           ("send_results", lambda t, c, cap, names, sub:
                            self.sent.append(("text", cap)) or self.podium.append(names) or 1),
                           ("name_of", lambda t, c, uid: f"Name{uid}"),
                           ("send_entry", lambda t, c, e, n: self.sent.append(("entry", n)) or 2),
                           ("mention", lambda t, c, uid: f"@{uid}"),
                           ("_time", None)):
            if fake is None:
                continue
            patcher = patch.object(send, name, fake)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(send._time, "sleep", lambda s: None)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(S, "send_ballot", lambda t, v, w, n, m, c=None, vote_close="": (v != 30, "x"))
        patcher.start()
        self.addCleanup(patcher.stop)
        S.save_participants({u: {"user_id": str(u), "joined_at": "x"} for u in (10, 20, 30)})
        for uid, stamp in ((10, "2026-10-06T09"), (20, "2026-10-07T09"), (30, "2026-10-05T09")):
            S.save_entry({"week_monday": WEEK, "user_id": uid, "kind": "video",
                          "file_id": f"f{uid}", "file_unique_id": "u", "submitted_at": stamp})

    def run_phase(self, name, state, dry=False):
        return send.PHASES[name](date.fromisoformat(WEEK), state, self.cfg, self.messages,
                                 "tok", "-1", dry, False)

    def test_showcase_posts_each_entry_once_and_reports_undeliverable_ballots(self):
        state = {WEEK: {"video_message_id": 5}}
        self.assertTrue(self.run_phase("showcase", state))
        self.assertEqual([n for kind, n in self.sent if kind == "entry"], ["1", "2", "3"])
        ws = state[WEEK]
        self.assertTrue(ws["showcase_sent"])
        self.assertEqual(ws["ballots_failed"], [30])
        self.assertTrue(any("@30" in text for kind, text in self.sent if kind == "text"))
        before = list(self.sent)
        self.assertFalse(self.run_phase("showcase", state))       # idempotent
        self.assertEqual(self.sent, before)
        self.assertEqual(json.loads(S.STATE_FILE.read_text())[WEEK]["numbering"], ws["numbering"])

    def test_showcase_dry_run_changes_nothing(self):
        state = {WEEK: {"video_message_id": 5}}
        with contextlib.redirect_stdout(io.StringIO()):
            self.run_phase("showcase", state, dry=True)
        self.assertEqual(self.sent, [])
        self.assertNotIn("numbering", state[WEEK])
        self.assertEqual(S.STATE_FILE.read_text(), "{}\n")

    def test_single_entry_week_skips_voting(self):
        rows = [r for r in send.P.read_rows(S.ENTRIES_FILE) if r["user_id"] == "10"]
        send.P.write_rows(S.ENTRIES_FILE, S.ENTRY_FIELDS, rows)
        state = {WEEK: {"video_message_id": 5}}
        self.run_phase("showcase", state)
        self.assertNotIn("numbering", state[WEEK])
        self.assertFalse(S.voting_open(state, WEEK))

    def test_results_rank_announce_and_close(self):
        state = {WEEK: {"video_message_id": 5}}
        self.run_phase("showcase", state)
        numbering = state[WEEK]["numbering"]
        by_user = {uid: n for n, uid in numbering.items()}
        S.save_vote(WEEK, 10, 20)
        S.save_vote(WEEK, 30, 20)
        S.save_vote(WEEK, 20, 10)
        self.sent.clear()
        with patch.object(send, "post_winner_badge") as badge:
            self.assertTrue(self.run_phase("results", state))
        text = next(t for kind, t in self.sent if kind == "text")
        self.assertLess(text.index("@20"), text.index("@10"))     # 2 votes beats 1
        self.assertNotIn("@30", text)                             # ranking_size = 2
        self.assertEqual(self.podium, [[("Name20", 2), ("Name10", 1)]])   # podium gets names + votes
        self.assertIn(("entry", by_user[20]), self.sent)          # winner's video re-shown
        badge.assert_called_once()
        self.assertEqual(badge.call_args.args[2], 20)
        self.assertTrue(state[WEEK]["results_sent"])
        self.assertFalse(S.voting_open(state, WEEK))
        self.assertFalse(self.run_phase("results", state))        # idempotent

    def test_results_with_no_votes(self):
        state = {WEEK: {"video_message_id": 5}}
        self.run_phase("showcase", state)
        self.sent.clear()
        self.run_phase("results", state)
        self.assertIn("no votes", self.sent[0][1])
        self.assertTrue(state[WEEK]["results_sent"])

    def gmt(self, day, hour, minute=0):
        return datetime(2026, 10, day, hour, minute, tzinfo=timezone.utc)

    def test_normal_week_is_due_monday_thursday_sunday(self):
        rows = [{"monday": date(2026, 10, 5), "video_on": date(2026, 10, 5),
                 "showcase_on": date(2026, 10, 8), "results_on": date(2026, 10, 11)}]
        state = {WEEK: {"showcase_on": "2026-10-08", "results_on": "2026-10-11"}}
        due = lambda now, st=state: send.due_actions(st, rows, {}, now)
        self.assertEqual(due(self.gmt(5, 11)), [("video", date(2026, 10, 5))])
        self.assertEqual(due(self.gmt(5, 10, 0)), [])                    # before 10:47 GMT
        self.assertEqual(due(self.gmt(6, 12)), [])
        self.assertEqual(due(self.gmt(8, 16)), [("showcase", date(2026, 10, 5))])
        self.assertEqual(due(self.gmt(11, 16)), [("results", date(2026, 10, 5))])

    def test_first_week_override_uses_its_own_dates(self):
        root = Path(self.tmp.name)
        (root / "clip.mp4").write_bytes(b"x")
        (root / "s.csv").write_text(
            "date,video,message,video_date,showcase_date,results_date\n"
            "2026-09-28,clip.mp4,hello,2026-10-01,2026-10-03,2026-10-04\n", encoding="utf-8")
        with patch.object(send, "SCHEDULE_FILE", root / "s.csv"), patch.object(send, "VIDEOS_DIR", root):
            rows = send.load_schedule()
        self.assertEqual((rows[0]["video_on"], rows[0]["showcase_on"], rows[0]["results_on"]),
                         (date(2026, 10, 1), date(2026, 10, 3), date(2026, 10, 4)))
        # video goes out Thursday; the state then carries the week's own dates and wording
        state = {}
        with patch.object(send, "send_video_file", return_value=7), \
                patch.object(send.S, "join_link", return_value=None), \
                patch.object(send.P, "record_topic_post"), \
                patch.object(send, "load_schedule", return_value=rows):
            send.phase_video(date(2026, 9, 28), state, {}, self.messages, "t", "-1", False, False)
        ws = state["2026-09-28"]
        self.assertEqual((ws["showcase_on"], ws["results_on"]), ("2026-10-03", "2026-10-04"))
        self.assertEqual((ws["deadline"], ws["vote_close"]), ("Saturday 12:00 GMT", "Sunday 15:47 GMT"))
        # the Thursday 15:47 GMT run must NOT showcase; Saturday's does
        self.assertEqual([a for a, _ in send.due_actions(state, rows, {}, self.gmt(1, 16))], ["video"])
        self.assertEqual(send.due_actions(state, rows, {}, self.gmt(3, 16)),
                         [("showcase", date(2026, 9, 28))])
        self.assertIn("Saturday 12:00 GMT", S.deadline_text(state))

    def test_schedule_rejects_out_of_order_dates(self):
        root = Path(self.tmp.name)
        (root / "clip.mp4").write_bytes(b"x")
        (root / "s.csv").write_text(
            "date,video,message,showcase_date,results_date\n"
            "2026-10-05,clip.mp4,hello,2026-10-09,2026-10-08\n", encoding="utf-8")
        with patch.object(send, "SCHEDULE_FILE", root / "s.csv"), patch.object(send, "VIDEOS_DIR", root):
            with self.assertRaises(ValueError):
                send.load_schedule()

    def test_announcement_is_due_on_configured_dates_once(self):
        cfg = {"shadowing": {"announce_on": ["2026-09-30"]}}
        now = datetime(2026, 9, 30, 11, tzinfo=timezone.utc)
        self.assertEqual(send.due_actions({}, [], cfg, now)[0][0], "announce")
        self.assertEqual(send.due_actions({"_announced": ["2026-09-30"]}, [], cfg, now), [])
        self.assertEqual(send.due_actions({}, [], cfg, now.replace(day=29)), [])


if __name__ == "__main__":
    unittest.main()
