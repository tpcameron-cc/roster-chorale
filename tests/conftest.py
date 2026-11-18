import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

LOPSIDED = {"Flute": 9, "Clarinet": 5, "Bass Clarinet": 1, "Alto Sax": 3, "Tenor Sax": 1,
            "Bari Sax": 1, "Trumpet": 4, "Horn": 1, "Trombone": 2, "Tuba": 1}

# A spread of real corpus chorales: major and minor, with pickups, sixteenths and ties.
SAMPLE_CHORALES = ["bwv66.6", "bwv269", "bwv347", "bwv153.1", "bwv86.6", "bwv267", "bwv17.7",
                   "bwv40.8", "bwv38.6", "bwv65.2", "bwv33.6", "bwv184.5"]


@pytest.fixture
def lopsided():
    return dict(LOPSIDED)


@pytest.fixture
def sample_xlsx():
    return ROOT / "samples" / "lopsided-band.xlsx"
