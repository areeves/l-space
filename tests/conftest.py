from pathlib import Path

import pytest

from l_space.ingestion.loaders import load_ale_csv

SAMPLE = Path(__file__).parent.parent / "data" / "sample" / "library.csv"


@pytest.fixture(scope="session")
def books():
    return list(load_ale_csv(SAMPLE))
