import socket

import pytest

from cutzo.core import Cutzo


@pytest.fixture()
def library(tmp_path):
    return tmp_path / "Cutzo"


@pytest.fixture()
def cutzo(library):
    app = Cutzo(library, speed=0.0)
    yield app
    app.jobs.shutdown()


@pytest.fixture()
def client(cutzo):
    from fastapi.testclient import TestClient

    from cutzo.engine import create_app

    with TestClient(create_app(cutzo, extra_hosts=("testserver",))) as c:
        yield c


@pytest.fixture()
def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
