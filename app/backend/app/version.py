"""Build version: set by the container build (NETLAB_VERSION / NETLAB_COMMIT), 'dev' otherwise."""
from .env import env
VERSION = env("VERSION", "dev")
COMMIT = env("COMMIT", "")
