"""Durable send checkpoints without persisting captions or member names."""
import hashlib
import json
from pathlib import Path

from automation.shared.storage import atomic_write_text


class TelegramRejected(RuntimeError):
    """Telegram explicitly confirmed that a request was rejected."""


class Delivery:
    def __init__(self, path, key, plan, force=False):
        self.path = Path(path)
        self.key = key
        # A missing/corrupt tracked journal is a recovery error, never an empty run.
        self.state = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(self.state, dict):
            raise ValueError("Invalid delivery journal; restore it from Git.")
        for entry in self.state.values():
            if (not isinstance(entry, dict) or set(entry) != {"plan", "steps"}
                    or not isinstance(entry["plan"], str)
                    or not isinstance(entry["steps"], dict)):
                raise ValueError("Invalid delivery journal entry; restore it from Git.")
            for step in entry["steps"].values():
                if (not isinstance(step, dict) or step.get("status") not in ("pending", "sent")
                        or (step["status"] == "sent" and
                            (type(step.get("message_id")) is not int or step["message_id"] <= 0))):
                    raise ValueError("Invalid delivery step; restore it from Git.")
        digest = hashlib.sha256(json.dumps(plan, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        previous = self.state.get(key)
        if previous and any(s["status"] == "pending" for s in previous["steps"].values()):
            raise ValueError(f"Uncertain delivery in {self.path.name} for {key}; inspect the chat and reconcile the pending step before retrying.")
        if previous and not force and previous["plan"] != digest:
            raise ValueError("Delivery inputs changed during a run; restore the original inputs before resuming.")
        if not previous or force:
            self.state[key] = {"plan": digest, "steps": {}}
        self.entry = self.state[key]

    def save(self):
        atomic_write_text(self.path, json.dumps(self.state, indent=2) + "\n")

    def done(self, step):
        return self.entry["steps"].get(step, {}).get("status") == "sent"

    def send(self, step, action):
        if self.done(step):
            return self.entry["steps"][step]["message_id"]
        if step in self.entry["steps"]:
            raise ValueError("Uncertain delivery; reconcile the pending step before retrying.")
        self.entry["steps"][step] = {"status": "pending"}
        self.save()  # If this fails, do not contact Telegram.
        try:
            message_id = action()
        except TelegramRejected:
            del self.entry["steps"][step]
            self.save()  # A confirmed rejection is safe to retry.
            raise
        if type(message_id) is not int or message_id <= 0:
            raise ValueError("Telegram returned no message id; inspect the chat before retrying.")
        self.entry["steps"][step] = {"status": "sent", "message_id": message_id}
        self.save()  # Failure leaves the durable pending marker for reconciliation.
        return message_id
