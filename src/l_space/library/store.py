"""Persistence. Exact questions (counts, filters, series status) are answered here,
with plain code, never by a language model.

Two interfaces, so that no book can be read without saying whose it is:

- ``LibraryStore``: accounts, and ``for_user(user_id)``. It has no book methods.
- ``UserLibrary``: one user's books. Everything that reads or writes books lives here, and an
  instance only ever sees the books of the user it was made for.
"""

from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from l_space.core.settings import Settings
from l_space.library import queries
from l_space.library.filters import BookFilter
from l_space.library.models import Book


@dataclass(frozen=True)
class ImportResult:
    """What an import changed."""

    added: int = 0
    updated: int = 0
    unchanged: int = 0
    removed: int = 0

    @property
    def total(self) -> int:
        """Books in the library after the import."""
        return self.added + self.updated + self.unchanged

    def summary(self) -> str:
        return (
            f"{self.total} books: {self.added} added, {self.updated} updated, "
            f"{self.unchanged} unchanged, {self.removed} removed"
        )


@dataclass(frozen=True)
class User:
    id: int
    username: str
    is_admin: bool
    disabled: bool
    created_at: datetime


class UsernameTaken(ValueError):
    """Another account already has this username (usernames are unique ignoring case)."""


class UnknownUser(LookupError):
    """There is no account with this id."""


class LibraryStore(ABC):
    """Accounts, and the way to a user's library."""

    @classmethod
    def from_settings(cls, settings: Settings) -> "LibraryStore":
        from l_space.library.engine import get_engine
        from l_space.library.store_sql import SqlLibraryStore

        return SqlLibraryStore(get_engine(settings))

    @abstractmethod
    def for_user(self, user_id: int) -> "UserLibrary":
        """The library of ``user_id``. Raises ``UnknownUser`` if there is no such account."""

    @abstractmethod
    def create_user(self, username: str, *, is_admin: bool = False) -> User:
        """Add an account (it cannot log in until it has a password). Raises ``UsernameTaken``."""

    @abstractmethod
    def get_user(self, user_id: int) -> User | None: ...

    @abstractmethod
    def find_user(self, username: str) -> User | None:
        """Look an account up by username, ignoring case."""

    @abstractmethod
    def default_owner(self) -> User:
        """The first account: who the app acts as until login exists (phase 2)."""

    @abstractmethod
    def delete_user(self, user_id: int) -> None:
        """Remove an account and, with it, all of that user's books."""


class UserLibrary(ABC):
    """One user's books."""

    user_id: int

    def get_book(self, public_id: str) -> Book | None:
        """One of this user's books by its public id; None if it is not theirs or does not exist."""
        return next((b for b in self.all_books() if b.public_id == public_id), None)

    @abstractmethod
    def import_books(self, books: Iterable[Book]) -> ImportResult:
        """Make the library match ``books``, matching rows by ``source_id``.

        New books are added, changed ones updated in place (they keep their ``id``), identical
        ones left alone, and books missing from ``books`` removed. All or nothing. If a
        ``source_id`` appears twice, the last one wins.
        """

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

    def narrator_counts(self, f: BookFilter | None = None) -> list[tuple[str, int]]:
        """Narrators of the matching audiobooks, most books first (ties by name)."""
        return queries.narrator_counts(self.filter_books(f))

    def author_counts(self, f: BookFilter | None = None) -> list[tuple[str, int]]:
        """Authors of the matching audiobooks, most books first (ties by name)."""
        return queries.author_counts(self.filter_books(f))

    def in_progress(self, f: BookFilter | None = None) -> list[Book]:
        """Matching audiobooks that are started but not finished, in all_books() order."""
        return queries.in_progress(self.filter_books(f))

    def series_gaps(self) -> list[dict]:
        """Numbered series with a missing earlier book. Covers every format, not only audiobooks."""
        return queries.series_gaps(self.all_books())
