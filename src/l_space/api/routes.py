from pathlib import Path

import httpx2
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from l_space.ask.answer import Answer, answer_question
from l_space.core.settings import Settings, get_settings
from l_space.ingestion.loaders import load_ale_csv
from l_space.library import queries
from l_space.library.models import Book
from l_space.library.store import LibraryStore
from l_space.models.provider import ModelProvider, from_settings, router_from_settings

router = APIRouter()


def get_store(settings: Settings = Depends(get_settings)) -> LibraryStore:
    return LibraryStore.from_settings(settings)


def get_provider(settings: Settings = Depends(get_settings)) -> ModelProvider:
    return from_settings(settings)


def get_router_provider(
    provider: ModelProvider = Depends(get_provider), settings: Settings = Depends(get_settings)
) -> ModelProvider:
    return router_from_settings(settings, provider)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/library/import")
def import_library(path: str, store: LibraryStore = Depends(get_store)) -> dict[str, int]:
    csv_path = Path(path)
    if not csv_path.is_file():
        raise HTTPException(status_code=404, detail=f"No such file: {path}")
    return {"imported": store.replace_all(load_ale_csv(csv_path))}


@router.get("/library/books", response_model=list[Book], response_model_exclude={"raw"})
def list_books(store: LibraryStore = Depends(get_store)) -> list[Book]:
    return store.all_books()


@router.get("/library/narrators")
def narrators(min_my_rating: float | None = None, store: LibraryStore = Depends(get_store)):
    return queries.narrator_counts(store.all_books(), min_my_rating)


@router.get("/library/series-gaps")
def series_gaps(store: LibraryStore = Depends(get_store)):
    return queries.series_gaps(store.all_books())


@router.post("/ask", response_model=Answer)
def ask(
    body: AskRequest,
    store: LibraryStore = Depends(get_store),
    provider: ModelProvider = Depends(get_provider),
    router_provider: ModelProvider = Depends(get_router_provider),
) -> Answer:
    books = store.all_books()
    if not books:
        raise HTTPException(status_code=409, detail="The library is empty. Import a CSV first.")
    try:
        return answer_question(body.question, books, provider, router_provider)
    except httpx2.HTTPError as exc:
        raise HTTPException(
            status_code=503, detail=f"Language model backend unavailable ({type(exc).__name__})."
        ) from exc
