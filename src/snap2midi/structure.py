"""Repeat structure: which order the measures are played in.

The input is one Marks per measure (in score order); the output is the list of
measure indices as a performer plays them. Covered:
  - repeat signs |: :| (an end repeat without a start goes back to the previous
    section boundary: the last end repeat or double bar passed, or the start)
  - 1st/2nd (and more) endings
  - D.C. / D.S. (to the segno), each optionally "al Fine" or "al Coda"
  - Fine, To Coda, Coda

Conventions (the common ones; scores that say otherwise are rare):
  - after a D.C. or D.S. jump, repeats are not taken again, and where there are
    endings the last one is played
  - "al Fine": stop after the measure marked Fine; plain D.C./D.S. with a Fine
    in the score also stops there; otherwise play to the end
  - "al Coda": on reaching To Coda, jump to the Coda
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Marks:
    repeat_start: bool = False        # |: at the start of this measure
    repeat_end: bool = False          # :| at the end of this measure
    endings: tuple[int, ...] = ()     # this measure belongs to these ending numbers
    section_end: bool = False         # a double bar at the end (a section boundary)
    segno: bool = False               # segno sign at the start of this measure
    coda: bool = False                # the Coda section starts at this measure
    to_coda: bool = False             # "To Coda": jump from the end of this measure
    fine: bool = False                # "Fine": the piece ends after this measure
    jump: str | None = None           # after this measure: "dc", "dc_al_fine", "dc_al_coda",
                                      # "ds", "ds_al_fine", "ds_al_coda"
    notes: list[str] = field(default_factory=list)  # what was detected, for reporting


def play_order(marks: list[Marks], max_len: int = 10_000) -> tuple[list[int], list[str]]:
    """Measure indices in performance order, plus warnings about anything that
    could not be followed."""
    n = len(marks)
    warns: list[str] = []
    segno = next((i for i, m in enumerate(marks) if m.segno), None)
    coda = next((i for i, m in enumerate(marks) if m.coda), None)

    order: list[int] = []
    i = 0
    section_start = 0
    pass_no = 1
    taken: set[int] = set()       # end repeats already used
    jumped = False                # after a D.C./D.S.
    stop_at_fine = False
    to_coda_active = False

    def highest_ending(start: int) -> int:
        nums = [x for m in marks[start:] for x in m.endings]
        return max(nums) if nums else 1

    while i < n and len(order) < max_len:
        m = marks[i]
        if m.repeat_start and i != section_start:
            section_start, pass_no = i, 1
        if m.endings:
            wanted = highest_ending(section_start) if jumped else pass_no
            if wanted not in m.endings:
                i += 1
                continue
        order.append(i)

        if jumped and m.fine and stop_at_fine:
            break
        if jumped and m.to_coda and to_coda_active:
            if coda is None:
                warns.append("To Coda found but no Coda to jump to; played on")
            else:
                to_coda_active = False
                i = coda
                continue
        if m.repeat_end and not jumped and i not in taken:
            taken.add(i)
            pass_no += 1
            i = section_start
            continue
        if m.jump and not jumped:
            jumped = True
            kind = m.jump
            stop_at_fine = kind.endswith("al_fine") or (not kind.endswith("al_coda")
                                                         and any(x.fine for x in marks))
            to_coda_active = kind.endswith("al_coda")
            if kind.startswith("ds"):
                if segno is None:
                    warns.append("D.S. found but no segno sign; jumped to the start instead")
                    i = 0
                else:
                    i = segno
            else:
                i = 0
            section_start, pass_no = i, 1
            continue
        # Leaving a finished repeat section: later end repeats go back only to here.
        if (m.repeat_end and i in taken) or m.section_end or (
                m.endings and (i + 1 >= n or not marks[i + 1].endings)):
            section_start, pass_no = i + 1, 1
        i += 1
    if len(order) >= max_len:
        warns.append("repeat structure did not terminate; played as written")
        return list(range(n)), warns
    return order, warns
