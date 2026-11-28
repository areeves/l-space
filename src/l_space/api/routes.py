from dataclasses import asdict
from pathlib import Path

import httpx2
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from l_space.api.schemas import BookOut
from l_space.ask.answer import Answer, answer_question
from l_space.core.settings import Settings, get_settings
from l_space.ingestion.loaders import load_ale_csv
from l_space.library.filters import BookFilter
from l_space.library.store import LibraryStore, User, UserLibrary
from l_space.models.provider import ModelProvider, from_settings, router_from_settings

router = APIRouter()


def get_store(settings: Settings = Depends(get_settings)) -> LibraryStore:
    return LibraryStore.from_settings(settings)


def current_user(store: LibraryStore = Depends(get_store)) -> User:
    """Who is making the request. Until login exists (phase 2) that is the default owner; phase 2
    replaces this body with token authentication, and nothing else needs to change."""
    return store.default_owner()


def get_library(
    user: User = Depends(current_user), store: LibraryStore = Depends(get_store)
) -> UserLibrary:
    """The caller's own library: the only way a route reaches books."""
    return store.for_user(user.id)


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
def import_library(path: str, library: UserLibrary = Depends(get_library)) -> dict[str, int]:
    csv_path = Path(path)
    if not csv_path.is_file():
        raise HTTPException(status_code=404, detail=f"No such file: {path}")
    result = library.import_books(load_ale_csv(csv_path))
    return {**asdict(result), "total": result.total}


@router.get("/library/books", response_model=list[BookOut])
def list_books(library: UserLibrary = Depends(get_library)) -> list[BookOut]:
    return [BookOut.from_book(b) for b in library.all_books()]


@router.get("/library/books/{public_id}", response_model=BookOut)
def get_book(public_id: str, library: UserLibrary = Depends(get_library)) -> BookOut:
    book = library.get_book(public_id)
    if book is None:  # unknown, or another user's: the same answer, so ids reveal nothing
        raise HTTPException(status_code=404, detail="No such book")
    return BookOut.from_book(book)


@router.get("/library/narrators")
def narrators(min_my_rating: float | None = None, library: UserLibrary = Depends(get_library)
):
    return library.narrator_counts(BookFilter(min_my_rating=min_my_rating))


@router.get("/library/series-gaps")
def series_gaps(library: UserLibrary = Depends(get_library)):
    return library.series_gaps()


@router.post("/ask", response_model=Answer)
def ask(
    body: AskRequest,
    library: UserLibrary = Depends(get_library),
    provider: ModelProvider = Depends(get_provider),
    router_provider: ModelProvider = Depends(get_router_provider),
) -> Answer:
    if library.count_books() == 0:
        raise HTTPException(status_code=409, detail="The library is empty. Import a CSV first.")
    try:
        return answer_question(body.question, library, provider, router_provider)
    except httpx2.HTTPError as exc:
        raise HTTPException(
            status_code=503, detail=f"Language model backend unavailable ({type(exc).__name__})."
        ) from exc
