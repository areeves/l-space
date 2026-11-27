import httpx2
from fastapi.testclient import TestClient

from conftest import SAMPLE
from fakes import FakeProvider
from l_space.api.routes import get_provider, get_store
from l_space.ingestion.loaders import load_ale_csv
from l_space.main import create_app
from stores import new_store


def client_for(provider, with_books=True) -> TestClient:
    store = new_store(":memory:")
    if with_books:
        store.import_books(load_ale_csv(SAMPLE))
    app = create_app()
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_provider] = lambda: provider
    return TestClient(app)


def test_ask_returns_answer_and_plan():
    provider = FakeProvider('{"op": "count_books", "author": "Maren Oakley"}', "You own 4 books by Maren Oakley.")
    resp = client_for(provider).post("/ask", json={"question": "How many by Maren Oakley?"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["text"] == "You own 4 books by Maren Oakley."
    assert body["plan"]["op"] == "count_books"
    assert body["grounded"] is True


def test_ask_rejects_empty_question():
    assert client_for(FakeProvider("{}")).post("/ask", json={"question": ""}).status_code == 422


def test_ask_on_empty_library_is_a_conflict():
    resp = client_for(FakeProvider("{}"), with_books=False).post("/ask", json={"question": "hi"})
    assert resp.status_code == 409


def test_ask_reports_unreachable_model_as_503():
    class Down:
        def generate(self, prompt, system=None, schema=None, max_tokens=None):
            raise httpx2.ConnectError("refused")

    resp = client_for(Down()).post("/ask", json={"question": "anything"})
    assert resp.status_code == 503
    assert "unavailable" in resp.json()["detail"]
