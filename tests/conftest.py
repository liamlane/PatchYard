import os, sys, tempfile, pathlib, pytest
_TMP = tempfile.mkdtemp(prefix="netlab-test-")
os.environ.update(NETLAB_DATA=f"{_TMP}/labs", NETLAB_METRICS="0", NETLAB_METRICS_DB=f"{_TMP}/metrics.db", NETLAB_SETTINGS=f"{_TMP}/settings.json")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "app" / "backend"))


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


@pytest.fixture
def tmp():
    return _TMP
