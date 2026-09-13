"""Environment loading shared by command entry points."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]

def load_dotenv():
    """
    Load KEY=VALUE pairs from a local .env file into the environment, if present.

    Used for local runs. On GitHub Actions there is no .env; the secrets come
    from the environment instead. Existing environment variables are not
    overwritten.
    """
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


