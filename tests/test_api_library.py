from fastapi.testclient import TestClient

from l_space.api.routes import get_store
from l_space.library import queries
from l_space.main import create_app


def client_for(store) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_store] = lambda: store
    return TestClient(app)


def test_narrators_route_applies_the_rating_filter(library, books):
    client = client_for(library)
    everyone = client.get("/library/narrators").json()
    rated = client.get("/library/narrators", params={"min_my_rating": 5}).json()
    assert [tuple(r) for r in everyone] == queries.narrator_counts(books)
    assert [tuple(r) for r in rated] == queries.narrator_counts(books, 5)
    assert rated != everyone


def test_series_gaps_route(library, books):
    assert client_for(library).get("/library/series-gaps").json() == queries.series_gaps(books)
