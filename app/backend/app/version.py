"""Build version: set by the container build (NETLAB_VERSION / NETLAB_COMMIT), 'dev' otherwise."""
import os
VERSION = os.environ.get("NETLAB_VERSION", "dev")
COMMIT = os.environ.get("NETLAB_COMMIT", "")
