"""Environment variables. PATCHYARD_* is the name; NETLAB_* (the project's old name) still
works so installs from before the rename keep their settings."""
import os

DEFAULT_HOME = "/opt/patchyard"


def env(name: str, default=None):
    for prefix in ("PATCHYARD_", "NETLAB_"):
        v = os.environ.get(prefix + name)
        if v is not None:
            return v
    return default


def home() -> str:
    return env("HOME", DEFAULT_HOME)
