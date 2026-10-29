import os, sys, tempfile, pathlib, pytest
_TMP = tempfile.mkdtemp(prefix="patchyard-test-")
os.environ.update(PATCHYARD_DATA=f"{_TMP}/labs", PATCHYARD_METRICS="0", PATCHYARD_METRICS_DB=f"{_TMP}/metrics.db", NETLAB_SETTINGS=f"{_TMP}/settings.json")   # NETLAB_* still works
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "app" / "backend"))


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


@pytest.fixture
def tmp():
    return _TMP
