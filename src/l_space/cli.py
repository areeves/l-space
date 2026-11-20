"""Tiny CLI: ``l-space import library.csv`` then ``l-space series-gaps``."""

import argparse
from pathlib import Path

from l_space.core.settings import get_settings
from l_space.ingestion.loaders import load_ale_csv
from l_space.library import queries
from l_space.library.store import LibraryStore


def main() -> None:
    parser = argparse.ArgumentParser(prog="l-space")
    sub = parser.add_subparsers(dest="command", required=True)
    imp = sub.add_parser("import", help="Import an ALE CSV export (replaces the current library)")
    imp.add_argument("csv", type=Path)
    sub.add_parser("series-gaps", help="Numbered series where you own later books but not earlier ones")
    sub.add_parser("narrators", help="Narrators ranked by number of audiobooks")
    args = parser.parse_args()

    store = LibraryStore(get_settings().db_path)
    if args.command == "import":
        print(f"Imported {store.replace_all(load_ale_csv(args.csv))} books")
    elif args.command == "series-gaps":
        for row in queries.series_gaps(store.all_books()):
            print(f"{row['series']}: missing book(s) {', '.join(map(str, row['missing']))}")
    elif args.command == "narrators":
        for name, count in queries.narrator_counts(store.all_books()):
            print(f"{count:3d}  {name}")
