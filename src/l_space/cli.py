"""Tiny CLI: ``l-space import library.csv``, ``l-space ask "..."``, and a few exact queries."""

import argparse
import sys
from pathlib import Path

import httpx2

from l_space.ask.answer import answer_question
from l_space.core.settings import get_settings
from l_space.ingestion.loaders import load_ale_csv
from l_space.library.engine import get_engine
from l_space.library.migrate import migrate, prepare_database
from l_space.library.store import LibraryStore
from l_space.models.provider import from_settings, router_from_settings


def main() -> None:
    parser = argparse.ArgumentParser(prog="l-space")
    sub = parser.add_subparsers(dest="command", required=True)
    imp = sub.add_parser("import", help="Import an ALE CSV export (adds, updates and removes books to match it)")
    imp.add_argument("csv", type=Path)
    ask = sub.add_parser("ask", help="Ask a question about your library (needs the Ollama model)")
    ask.add_argument("question")
    ask.add_argument("--show-plan", action="store_true", help="Also print the query plan and checks")
    sub.add_parser("migrate", help="Create or upgrade the database schema")
    sub.add_parser("series-gaps", help="Numbered series where you own later books but not earlier ones")
    sub.add_parser("narrators", help="Narrators ranked by number of audiobooks")
    args = parser.parse_args()

    settings = get_settings()
    if args.command == "migrate":
        migrate(get_engine(settings))
        print("Database is up to date")
        return
    try:
        prepare_database(settings)
    except RuntimeError as exc:
        sys.exit(str(exc))
    store = LibraryStore.from_settings(settings)
    if args.command == "import":
        print(f"Imported {store.import_books(load_ale_csv(args.csv)).summary()}")
    elif args.command == "ask":
        if store.count_books() == 0:
            sys.exit("The library is empty. Run: l-space import <csv>")
        try:
            writer = from_settings(settings)
            answer = answer_question(
                args.question, store, writer, router_from_settings(settings, writer)
            )
        except httpx2.HTTPError as exc:
            sys.exit(f"Could not reach the language model at {settings.ollama_url}: {exc}")
        print(answer.text)
        if args.show_plan:
            print("\n--- plan ---")
            print(answer.plan.model_dump_json(exclude_none=True, indent=2))
            print(
                f"grounded={answer.grounded} used_fallback={answer.used_fallback} "
                f"abstained={answer.abstained} timings_ms={answer.timings_ms}"
            )
            if answer.ungrounded_terms:
                print(f"ungrounded terms in the model's draft: {answer.ungrounded_terms}")
            if answer.router_error:
                print(f"router error: {answer.router_error}")
    elif args.command == "series-gaps":
        for row in store.series_gaps():
            print(f"{row['series']}: missing book(s) {', '.join(map(str, row['missing']))}")
    elif args.command == "narrators":
        for name, count in store.narrator_counts():
            print(f"{count:3d}  {name}")
