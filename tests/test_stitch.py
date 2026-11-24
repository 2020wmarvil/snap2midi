
import pytest

from conftest import BASELINE, PIECES, make_scrolled, piece_images
from snap2midi.preprocess import analyze
from snap2midi.stitch import measure_number, stitch


@pytest.fixture(scope="session")
def paged(analyzed):
    return {piece: stitch(pages) for piece, pages in analyzed.items()}


@pytest.mark.parametrize("piece", PIECES)
def test_measure_numbers_read(paged, piece):
    assert paged[piece].numbers == BASELINE[piece]["measure_numbers"]


@pytest.mark.parametrize("piece", PIECES)
def test_paged_capture_drops_nothing(paged, piece):
    assert paged[piece].dropped == 0
    assert paged[piece].warnings == []


def test_measure_number_fixes_letter_digit_confusion(analyzed):
    # lovely p2, second system is printed "11"; raw OCR reads it as "II".
    page = analyzed["lovely"][1]
    assert measure_number(page, page.systems[1]) == 11


def test_duplicate_screenshot_is_skipped(testdata, analyzed, paged):
    pages = list(analyzed["gymnopedie_no1"])
    pages.insert(4, pages[3])  # same screenshot twice in a row
    st = stitch(pages)
    assert st.numbers == paged["gymnopedie_no1"].numbers
    assert st.dropped == len(pages[3].systems)
    assert any("duplicate" in w for w in st.warnings)


@pytest.mark.parametrize("piece", PIECES)
def test_scrolled_capture_matches_paged(tmp_path_factory, testdata, paged, piece):
    shots = make_scrolled(piece_images(testdata, piece), tmp_path_factory.mktemp(piece))
    st = stitch([analyze(p) for p in shots])
    expected = paged[piece].numbers
    assert len(st.systems) == len(expected)
    # A window edge can cut off a printed number; then that one is unknown (None).
    for got, want in zip(st.numbers, expected):
        assert got in (want, None)
    assert sum(n is None for n in st.numbers) <= 2


def test_out_of_order_screenshots_warn(analyzed):
    pages = list(analyzed["game_of_thrones"])
    pages[1], pages[2] = pages[2], pages[1]
    st = stitch(pages)
    assert any("out of order" in w for w in st.warnings)
