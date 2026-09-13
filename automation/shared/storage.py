"""Atomic single-file replacement. Writers still require workflow concurrency."""
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def atomic_text_writer(path):
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="",
                                         dir=path.parent, prefix=path.name + ".",
                                         suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            yield stream
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def atomic_write_text(path, text, encoding="utf-8"):
    if encoding != "utf-8":
        raise ValueError("State must use UTF-8")
    with atomic_text_writer(path) as stream:
        stream.write(text)
