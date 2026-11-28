from dataclasses import asdict
from pathlib import Path

import httpx2
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from l_space.ask.answer import Answer, answer_question
from l_space.core.settings import Settings, get_settings
from l_space.ingestion.loaders import load_ale_csv
from l_space.library.filters import BookFilter
from l_space.library.models import Book
from l_space.library.store import LibraryStore, UserLibrary
from l_space.models.provider import ModelProvider, from_settings, router_from_settings

router = APIRouter()


def get_store(settings: Settings = Depends(get_settings)) -> LibraryStore:
    return LibraryStore.from_settings(settings)


def get_library(store: LibraryStore = Depends(get_store)) -> UserLibrary:
    """The library the request acts on. Until login exists (phase 2) that is the default owner's."""
    return store.for_user(store.default_owner().id)


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


@router.get("/library/books", response_model=list[Book], response_model_exclude={"raw"})
def list_books(library: UserLibrary = Depends(get_library)) -> list[Book]:
    return library.all_books()


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
