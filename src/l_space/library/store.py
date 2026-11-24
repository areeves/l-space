"""SQLite persistence. Exact questions (counts, filters, series status) are answered here,
with plain code, never by a language model."""

from abc import ABC, abstractmethod
from collections.abc import Iterable

from l_space.core.settings import Settings
from l_space.library.models import Book

class LibraryStore(ABC):
    @classmethod
    def from_settings(cls, settings: Settings) -> "LibraryStore":
        from l_space.library.store_sqlite import SqliteLibraryStore
        return SqliteLibraryStore(settings.db_path)

    @abstractmethod
    def replace_all(self, books: Iterable[Book]) -> int:
        """Replace the whole library with `books` (re-import is idempotent) and return the count."""

    @abstractmethod
    def all_books(self) -> list[Book]:
        """Return every book, ordered by title."""

