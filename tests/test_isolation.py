"""One user's library never shows, changes or removes another's (decisions D2 and D5)."""

import inspect

import pytest

from l_space.library import queries
from l_space.library.filters import BookFilter
from l_space.library.store import LibraryStore, UnknownUser, UserLibrary, UsernameTaken
from stores import new_store
from test_store_schema import assert_index_matches_blob, sql


def titles(library) -> list[str]:
    return [b.title for b in library.all_books()]


def test_each_user_sees_only_their_own_books(two_users):
    _, alice, bob = two_users
    assert titles(alice) == ["Alice's Shared 0", "Alice's Shared 1", "Alice's Shared 2", "Only Alice"]
    assert titles(bob) == [
        "Bob's Podcast", "Bob's Shared 0", "Bob's Shared 1", "Bob's Shared 2", "Only Bob",
    ]


def test_the_same_asin_is_a_separate_copy_per_user(two_users):
    _, alice, _bob = two_users
    rows = sql(alice, "SELECT user_id, source_id, public_id FROM books WHERE source_id = 'SHARED0'")
    assert len(rows) == 2 and rows[0][0] != rows[1][0]
    assert rows[0][2] != rows[1][2]  # each copy has its own opaque identifier
    assert {b.title for b in alice.filter_books() if b.source_id == "SHARED0"} == {"Alice's Shared 0"}


# Every public read method of a library, discovered from the interface: a new method that reads
# books must be scoped, and this test finds it without anyone remembering to list it.
READERS = sorted(
    name
    for name, member in inspect.getmembers(UserLibrary, inspect.isfunction)
    if not name.startswith("_") and name != "import_books"
)


def test_the_readers_are_found():
    assert {"all_books", "filter_books", "count_books", "narrator_counts", "author_counts",
            "in_progress", "series_gaps"} <= set(READERS)


# Readers that take arguments are given bob's identifiers, asked for from alice's library.
def reader_arguments(name, bob):
    return {"get_book": (bob.all_books()[0].public_id,)}.get(name, ())


@pytest.mark.parametrize("name", READERS)
def test_no_reader_returns_another_users_data(two_users, name):
    _, alice, bob = two_users
    result = getattr(alice, name)(*reader_arguments(name, bob))
    flat = repr(result)
    for other in ("Bob", "B-ONLY", "Pod"):
        assert other not in flat, f"{name} leaked: {other}"
    if name != "get_book":  # looking up bob's book finds nothing; every other reader finds alice's
        assert flat


def test_a_public_id_only_opens_the_book_for_its_owner(two_users):
    _, alice, bob = two_users
    mine, theirs = alice.all_books()[0], bob.all_books()[0]
    assert alice.get_book(mine.public_id) == mine
    assert bob.get_book(theirs.public_id) == theirs
    assert alice.get_book(theirs.public_id) is None
    assert bob.get_book(mine.public_id) is None
    assert alice.get_book("no-such-id") is None


def test_aggregates_cover_only_the_users_own_books(two_users):
    _, alice, bob = two_users
    assert alice.narrator_counts() == [("Common Narrator", 3), ("Alice Narrator", 1)]
    assert alice.narrator_counts() == queries.narrator_counts(alice.all_books())
    assert bob.author_counts() == queries.author_counts(bob.all_books())
    assert alice.series_gaps() == queries.series_gaps(alice.all_books())
    assert {g["series"] for g in alice.series_gaps()} == {"Alice Saga", "Shared Saga"}
    assert {g["series"] for g in bob.series_gaps()} == {"Bob Saga", "Shared Saga"}
    assert alice.count_books() == 4 and bob.count_books() == 4  # bob's podcast is not an audiobook
    assert len(alice.in_progress()) == 4 and len(bob.in_progress()) == 4


def test_filters_only_reach_the_users_own_books(two_users):
    _, alice, bob = two_users
    assert alice.filter_books(BookFilter(author="bob")) == []
    assert [b.title for b in bob.filter_books(BookFilter(author="bob"))] == [
        "Bob's Shared 0", "Bob's Shared 1", "Bob's Shared 2", "Only Bob",
    ]
    assert alice.count_books(BookFilter(narrator="bob")) == 0


def snapshot(library) -> dict:
    """Every row belonging to ``library``'s user: their books and those books' child rows."""
    mine = (library.user_id,)
    books = sql(library, "SELECT * FROM books WHERE user_id = ?", mine)
    children = {
        table: sorted(
            sql(
                library,
                f"SELECT * FROM {table} WHERE book_id IN (SELECT id FROM books WHERE user_id = ?)",
                mine,
            ),
            key=repr,
        )
        for table in ("book_people", "book_series", "book_tags")
    }
    return {"books": sorted(books, key=repr), **children}


def test_one_users_import_leaves_the_others_untouched(two_users):
    _, alice, bob = two_users
    before = snapshot(bob)

    result = alice.import_books([])  # alice removes everything
    assert result.removed == 4 and alice.all_books() == []
    assert snapshot(bob) == before  # rows, ids, public ids and children, all unchanged
    assert bob.count_books() == 4

    alice.import_books([b.model_copy(update={"title": "New"}) for b in bob.all_books()])
    assert snapshot(bob) == before
    assert assert_index_matches_blob(bob) == 10  # every row in the database: bob's 5 and the 5 alice now has


def test_importing_a_users_own_books_again_is_unchanged_even_when_others_share_asins(two_users):
    _, alice, _ = two_users
    result = alice.import_books(alice.all_books())
    assert (result.added, result.updated, result.removed, result.unchanged) == (0, 0, 0, 4)


def test_deleting_a_user_removes_only_their_books_and_child_rows(two_users):
    store, _alice, bob = two_users
    before = snapshot(bob)
    alice_user = store.find_user("alice")

    store.delete_user(alice_user.id)

    assert store.find_user("alice") is None
    assert snapshot(bob) == before
    remaining_owners = {r[0] for r in sql(bob, "SELECT DISTINCT user_id FROM books")}
    assert remaining_owners == {store.find_user("bob").id}
    for table in ("book_people", "book_series", "book_tags"):
        orphans = sql(bob, f"SELECT COUNT(*) FROM {table} WHERE book_id NOT IN (SELECT id FROM books)")
        assert orphans == [(0,)]


# --- The interface and the accounts ----------------------------------------------------------


def test_the_store_has_no_book_methods():
    # Books are only reachable through for_user(): the unscoped class cannot read them.
    book_methods = set(READERS) | {"import_books"}
    assert not book_methods & {n for n in dir(LibraryStore) if not n.startswith("_")}
    assert not book_methods & {n for n in dir(type(new_store())) if not n.startswith("_")}


def test_for_user_needs_an_existing_account(two_users):
    store, *_ = two_users
    with pytest.raises(UnknownUser):
        store.for_user(9999)


def test_usernames_are_unique_ignoring_case_and_never_blank(two_users):
    store, *_ = two_users
    with pytest.raises(UsernameTaken):
        store.create_user(" ALICE ")
    with pytest.raises(ValueError, match="blank"):
        store.create_user("   ")
    assert store.find_user("ALICE").username == "alice"


def test_accounts(two_users):
    store, alice, _ = two_users
    user = store.get_user(alice.user_id)
    assert (user.username, user.is_admin, user.disabled) == ("alice", False, False)
    assert user.created_at is not None
    assert store.get_user(9999) is None
    assert store.default_owner().username == "owner"  # new_store() creates it first
    admin = store.create_user("Admin", is_admin=True)
    assert admin.is_admin and admin.username == "Admin"


def test_deleting_an_unknown_user_is_an_error(two_users):
    store, *_ = two_users
    with pytest.raises(UnknownUser):
        store.delete_user(9999)
