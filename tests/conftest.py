import os
import tempfile
import pytest


def pytest_configure(config):
    os.environ["DATABASE_URL"] = "sqlite://"
    os.environ["RATE_LIMIT_UPLOAD"] = "100"
    os.environ["RATE_LIMIT_WINDOW"] = "60"
    os.environ["MAX_QUEUE_ITEMS"] = "20"
    os.environ["ITEM_TIMEOUT"] = "600"
    os.environ["MAX_RETRIES"] = "3"


@pytest.fixture
def temp_file():
    f = tempfile.NamedTemporaryFile(delete=False)
    f.write(b"dummy content for mime detection")
    f.close()
    yield f.name
    if os.path.exists(f.name):
        os.remove(f.name)


@pytest.fixture
def app():
    from flask import Flask
    from models import db

    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite://"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["TESTING"] = True
    app.config["UPLOAD_FOLDER"] = tempfile.mkdtemp()
    app.config["TEMP_FOLDER"] = tempfile.mkdtemp()
    app.config["ITEM_TIMEOUT"] = 600
    app.config["MAX_RETRIES"] = 3
    app.config["MAX_QUEUE_ITEMS"] = 20
    app.secret_key = "test-secret"
    db.init_app(app)
    with app.app_context():
        db.create_all()
    yield app


@pytest.fixture
def qm(app):
    from services.queue import QueueManager

    qm = QueueManager(app, app.extensions["sqlalchemy"])
    with app.app_context():
        yield qm
