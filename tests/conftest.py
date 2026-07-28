import os
import tempfile
import pytest


def pytest_configure(config):
    os.environ["DATABASE_URL"] = "sqlite://"


@pytest.fixture
def temp_file():
    f = tempfile.NamedTemporaryFile(delete=False)
    f.write(b"dummy content for mime detection")
    f.close()
    yield f.name
    if os.path.exists(f.name):
        os.remove(f.name)
