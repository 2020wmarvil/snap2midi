---
name: snap2midi
description: Turn screenshots of piano sheet music into MusicXML and MIDI files with the snap2midi CLI. Use when the user has phone screenshots or images of a piano score and wants a .mid or .musicxml, wants to play along to it, or asks to transcribe sheet music images. Not for audio-to-MIDI or for non-piano scores.
---

# snap2midi

Converts screenshots of ONE piano piece into `<name>.musicxml` and `<name>.mid`.
Fully automatic: no prompts, no manual correction step.

## Command

```
snap2midi transcribe <image> [<image> ...] [-n NAME] [-o OUT_DIR] --json
```

- `<image>`: PNG or JPEG screenshots of a single piece, **in reading order**. Screenshots
  may be page by page or scrolled with overlap; overlap and exact duplicates are removed.
- `-n, --name`: output file name. Omit it to use the title read from page 1
  (falls back to the first image's file name).
- `-o, --out-dir`: output folder, default the current folder. Created if missing.
- `--json`: always pass this when you drive the tool. Prints one JSON object on stdout.
- `--no-crosscheck`: skip the second engine. Faster, but no suspect-measure check.
- `--cpu`: do not use the GPU.
- `--keep-work DIR`: keep intermediate pages and raw engine output (for debugging).

One run = one piece. If the user hands you screenshots of several pieces, group them
first (titles, page numbers, measure numbers) and run once per piece.

Runtime: roughly 10 to 30 s per piece on an NVIDIA GPU, several times longer on CPU.

## Output (`--json`)

Success:

```json
{
  "ok": true,
  "name": "Hollow Knight Main Theme",
  "musicxml": "C:\\...\\Hollow Knight Main Theme.musicxml",
  "midi": "C:\\...\\Hollow Knight Main Theme.mid",
  "measures": 26,
  "systems": 8,
  "screenshots": 2,
  "duplicates_skipped": 0,
  "suspect_measures": [{"measure": 23, "reason": "engines disagree on 22% of notes"}],
  "warnings": [],
  "fixes": ["system 1, treble staff shifted up an octave (octave clef)"],
  "engines": {"homr": {"gpu": true}, "audiveris": {"used": true}},
  "seconds": 11.1
}
```

Failure: `{"ok": false, "exit_code": 3, "error": "no piano staves found in the screenshots"}`

## Exit codes

| Code | Meaning | What to do |
| --- | --- | --- |
| 0 | Files written (may still have warnings) | Read `suspect_measures` and `warnings` |
| 1 | Internal error (a bug) | Report the `error` text; retrying will not help |
| 2 | Bad arguments or unreadable image | Fix the paths or file types |
| 3 | No piano staves found | Images are not sheet music, or too small/blurry |
| 4 | Recognition failed | Retry with `--cpu`; if it persists, report it |
| 5 | Engine missing | The install is broken; reinstall the bundle |

## Reading the result

- `suspect_measures`: measure numbers (1-based, as in the output file) where the two OMR
  engines read different notes, a bar does not add up to the time signature, or a note
  is outside the piano range. The file is still written; the primary engine's reading
  is used. Tell the user which measures to check by ear or against the score.
- `warnings` worth surfacing to the user:
  - `measures X-Y missing: a screenshot may be missing ...`: a gap in the printed measure
    numbers. Ask the user for the missing screenshot and rerun with it in order.
  - `measure numbers go backwards ...`: screenshots are probably out of order.
  - `... duplicate of previous screenshot, skipped`: harmless.
  - `Audiveris not found ...`: no cross-check was done, so `suspect_measures` only covers
    bar-length and range problems.
- `fixes`: automatic corrections already applied. Informational.
- `measures` vs. the last printed measure number on the score is a quick sanity check.

## Limits

- Piano scores only (two staves). Lead sheets and other instruments are not supported.
- Printed, engraved music. Handwritten scores will fail or be poor.
- Accidentals are sometimes spelled enharmonically (B-flat for A-sharp). Pitch and MIDI
  are correct.
- Repeats are kept as repeat signs in MusicXML; the MIDI plays the music as written
  once, without expanding repeats.
