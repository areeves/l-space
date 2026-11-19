"""Tiny CLI: ``l-space import library.csv`` then ``l-space stalled``."""

import argparse
from pathlib import Path

from l_space.core.settings import get_settings
from l_space.ingestion.loaders import load_csv
from l_space.library import queries
from l_space.library.store import LibraryStore


def main() -> None:
    parser = argparse.ArgumentParser(prog="l-space")
    sub = parser.add_subparsers(dest="command", required=True)
    imp = sub.add_parser("import", help="Import a library CSV (replaces the current library)")
    imp.add_argument("csv", type=Path)
    sub.add_parser("stalled", help="Series you started and haven't continued")
    sub.add_parser("narrators", help="Narrators ranked by number of books")
    args = parser.parse_args()

    store = LibraryStore(get_settings().db_path)
    if args.command == "import":
        print(f"Imported {store.replace_all(load_csv(args.csv))} books")
    elif args.command == "stalled":
        for row in queries.stalled_series(store.all_books()):
            print(f"{row['series']}: next up '{row['next_up']}' ({row['unstarted']} unstarted)")
    elif args.command == "narrators":
        for name, count in queries.narrator_counts(store.all_books()):
            print(f"{count:3d}  {name}")
