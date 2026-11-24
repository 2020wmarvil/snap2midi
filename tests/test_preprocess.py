import numpy as np
import pytest

from conftest import BASELINE, PIECES
from snap2midi.preprocess import TARGET_INTERLINE, remove_overlays


@pytest.mark.parametrize("piece", PIECES)
def test_systems_per_page(analyzed, piece):
    assert [len(p.systems) for p in analyzed[piece]] == BASELINE[piece]["systems_per_page"]


@pytest.mark.parametrize("piece", PIECES)
def test_no_warnings_on_clean_pages(analyzed, piece):
    assert [w for p in analyzed[piece] for w in p.warnings] == []


@pytest.mark.parametrize("piece", PIECES)
def test_systems_are_grand_staves_at_target_scale(analyzed, piece):
    for page in analyzed[piece]:
        for s in page.systems:
            assert len(s.staves) == 2
            assert s.staves[0].bottom < s.staves[1].top, "treble staff must be above bass staff"
            assert not s.partial
            # After normalization the staff-line spacing should be close to the target.
            scale = s.image.height / (s.box[3] - s.box[1])
            assert abs(s.staves[0].interline * scale - TARGET_INTERLINE) < 1.5


@pytest.mark.parametrize("piece", PIECES)
def test_systems_span_the_staff_width(analyzed, piece):
    """Boxes must include the clef at the left and the final barline at the right."""
    for page in analyzed[piece]:
        widths = [s.staves[0].x1 - s.staves[0].x0 for s in page.systems]
        page_w = page.clean.shape[1]
        # First systems can be indented (instrument name); everything spans most of the page.
        assert min(widths) > 0.6 * page_w
        assert max(widths) > 0.85 * page_w


@pytest.mark.parametrize("piece", PIECES)
def test_ui_is_outside_every_system(testdata, analyzed, piece):
    """Status bar and player bar must never end up inside a system crop."""
    from PIL import Image
    for page in analyzed[piece]:
        rgb = np.asarray(Image.open(testdata / piece / page.source).convert("RGB")).astype(int)
        colored_rows = np.where(((rgb.max(2) - rgb.min(2)) > 40).mean(1) > 0.3)[0]  # player bar
        h = rgb.shape[0]
        for s in page.systems:
            assert s.box[1] > 0.05 * h  # below the status bar
            assert not ((colored_rows >= s.box[1]) & (colored_rows < s.box[3])).any()


def test_overlay_keeps_ink_under_translucent_color():
    rgb = np.full((10, 30, 3), 255, np.uint8)
    rgb[:, 0:10] = (160, 200, 250)   # light blue overlay on paper -> white
    rgb[:, 10:20] = (30, 50, 100)    # blue overlay over black ink -> black
    rgb[:, 20:30] = (20, 20, 20)     # plain ink stays dark
    gray = remove_overlays(rgb)
    assert (gray[:, 0:10] == 255).all()
    assert (gray[:, 10:20] == 0).all()
    assert (gray[:, 20:30] < 50).all()


def test_cursor_page_keeps_its_first_note(testdata, analyzed):
    """Gymnopedie p1 has the translucent blue playback cursor across both staves.
    Where it crosses a staff line, the line must still be ink after cleanup."""
    from PIL import Image
    page = analyzed["gymnopedie_no1"][0]
    rgb = np.asarray(Image.open(testdata / "gymnopedie_no1" / page.source).convert("RGB")).astype(int)
    colored = (rgb.max(2) - rgb.min(2)) > 40
    checked = 0
    for st in page.systems[0].staves:
        cols = np.where(colored[st.top:st.bottom + 1].any(axis=0))[0]
        cols = cols[(cols > st.x0) & (cols < st.x1)]
        for y in st.lines:
            for c in cols:
                if colored[y - 1:y + 2, c].any():
                    assert page.clean[y - 1:y + 2, c].min() < 140, f"staff line erased at x={c}, y={y}"
                    checked += 1
    assert checked > 20, "cursor not found; did the test image change?"


@pytest.mark.parametrize("piece", PIECES)
def test_staff_extent_reaches_the_ends_of_the_lines(analyzed, piece):
    """The detected staff must run to where its lines end: no staff-line ink just
    outside it (else the clef or final barline gets cut off)."""
    for page in analyzed[piece]:
        dark = page.clean < 140
        for s in page.systems:
            for st in s.staves:
                gap = max(2, int(0.25 * st.interline))
                for c in (st.x0 - gap, st.x1 + gap):
                    if 0 <= c < dark.shape[1]:
                        inked = sum(dark[y - 1:y + 2, c].any() for y in st.lines)
                        assert inked < 4, f"{page.source}: staff lines continue at x={c} (staff {st.x0}-{st.x1})"


def _synthetic_grand_staff(width=900, gap_cols=()):
    """White page with two 5-line staves; optional white columns cut through the lines."""
    img = np.full((600, width), 255, np.uint8)
    for top in (150, 330):
        for k in range(5):
            img[top + 18 * k:top + 18 * k + 2, 40:width - 40] = 0
    for c0, c1 in gap_cols:
        img[:, c0:c1] = 255
    return img


def test_staff_extent_survives_broken_lines():
    """A break in the staff lines (removed overlay, wide gap) must not shorten the staff."""
    from snap2midi.preprocess import find_staves
    staves = find_staves(_synthetic_grand_staff(gap_cols=[(600, 620)]))
    assert len(staves) == 2
    for st in staves:
        assert st.x0 <= 41 and st.x1 >= 858
