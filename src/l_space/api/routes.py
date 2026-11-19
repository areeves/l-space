from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from l_space.core.settings import Settings, get_settings
from l_space.ingestion.loaders import load_csv
from l_space.library import queries
from l_space.library.models import Book
from l_space.library.store import LibraryStore

router = APIRouter()


def get_store(settings: Settings = Depends(get_settings)) -> LibraryStore:
    return LibraryStore(settings.db_path)


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/library/import")
def import_library(path: str, store: LibraryStore = Depends(get_store)) -> dict[str, int]:
    csv_path = Path(path)
    if not csv_path.is_file():
        raise HTTPException(status_code=404, detail=f"No such file: {path}")
    return {"imported": store.replace_all(load_csv(csv_path))}


@router.get("/library/books", response_model=list[Book])
def list_books(store: LibraryStore = Depends(get_store)) -> list[Book]:
    return store.all_books()


@router.get("/library/narrators")
def narrators(min_rating: float | None = None, store: LibraryStore = Depends(get_store)):
    return queries.narrator_counts(store.all_books(), min_rating)


@router.get("/library/stalled-series")
def stalled_series(store: LibraryStore = Depends(get_store)):
    return queries.stalled_series(store.all_books())
