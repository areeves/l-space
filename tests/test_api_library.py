from pathlib import Path

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


def test_import_route_reports_what_changed(books, tmp_path):
    from stores import new_store

    store = new_store()
    client = client_for(store)
    sample = str(Path(__file__).parent.parent / "data" / "sample" / "library.csv")

    first = client.post("/library/import", params={"path": sample}).json()
    assert first == {"added": len(books), "updated": 0, "unchanged": 0, "removed": 0, "total": len(books)}
    again = client.post("/library/import", params={"path": sample}).json()
    assert again == {"added": 0, "updated": 0, "unchanged": len(books), "removed": 0, "total": len(books)}


def test_cli_import_prints_a_summary(tmp_path, monkeypatch, capsys):
    from l_space import cli

    sample = Path(__file__).parent.parent / "data" / "sample" / "library.csv"
    monkeypatch.setenv("L_SPACE_DB_PATH", str(tmp_path / "cli.db"))
    for _ in range(2):
        monkeypatch.setattr("sys.argv", ["l-space", "import", str(sample)])
        cli.main()
    lines = capsys.readouterr().out.splitlines()
    assert "12 added, 0 updated, 0 unchanged, 0 removed" in lines[0]
    assert "0 added, 0 updated, 12 unchanged, 0 removed" in lines[1]
