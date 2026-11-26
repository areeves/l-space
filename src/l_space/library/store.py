"""Persistence. Exact questions (counts, filters, series status) are answered here,
with plain code, never by a language model."""

from abc import ABC, abstractmethod
from collections.abc import Iterable

from l_space.core.settings import Settings
from l_space.library import queries
from l_space.library.filters import BookFilter
from l_space.library.models import Book


class LibraryStore(ABC):
    @classmethod
    def from_settings(cls, settings: Settings) -> "LibraryStore":
        from l_space.library.engine import get_engine
        from l_space.library.store_sql import SqlLibraryStore

        return SqlLibraryStore(get_engine(settings))

    @abstractmethod
    def replace_all(self, books: Iterable[Book]) -> int:
        """Replace the whole library with `books` (re-import is idempotent) and return the count."""

    @abstractmethod
    def all_books(self) -> list[Book]:
        """Return every book, ordered by title."""

    # Concrete methods below are the reference behaviour, built on all_books(). Subclasses may
    # override them with something faster, but must return the same books in the same order.

    def filter_books(self, f: BookFilter | None = None) -> list[Book]:
        """Audiobooks matching every given filter, in all_books() order."""
        return queries.filter_books(self.all_books(), **(f or BookFilter()).as_kwargs())

    def count_books(self, f: BookFilter | None = None) -> int:
        return len(self.filter_books(f))
