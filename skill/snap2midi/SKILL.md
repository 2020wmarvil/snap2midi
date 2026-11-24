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
- `--no-review`: do not write review images (see "Review the work").
- `--keep-work DIR`: keep intermediate pages and raw engine output (for debugging).

One run = one piece. If the user hands you screenshots of several pieces, group them
first (titles, page numbers, measure numbers) and run once per piece.

Runtime: roughly 10 to 30 s per piece on an NVIDIA GPU, several times longer on CPU.

## Output (`--json`)

Success:

```json
{
  "ok": true,
  "name": "Je Te Laisserai Des Mots",
  "musicxml": "C:\\...\\Je Te Laisserai Des Mots.musicxml",
  "midi": "C:\\...\\Je Te Laisserai Des Mots.mid",
  "measures": 92,
  "systems": 23,
  "screenshots": 5,
  "duplicates_skipped": 0,
  "suspect_measures": [
    {
      "measure": 31,
      "reason": "engines disagree on 15% of notes",
      "system": 9, "position": 1, "of": 4, "printed_measure": 31,
      "homr": {"treble": "beat 1: A#4(eighth) A#3(eighth); ...", "bass": "beat 1: F#2(quarter); beat 2: F#3(quarter) C#3(quarter) A#2(quarter); ..."},
      "audiveris": {"treble": "...", "bass": "beat 1: F#2(quarter); beat 2: A2(quarter) C#3(quarter) F#3(quarter) ..."},
      "only_homr": ["beat 2 A#2", "beat 3 A#2"],
      "only_audiveris": ["beat 2 A2 x2", "beat 2 C#3", "beat 3 A2"],
      "image": "C:\\...\\Je Te Laisserai Des Mots.review\\system09.png"
    }
  ],
  "review_dir": "C:\\...\\Je Te Laisserai Des Mots.review",
  "midi_repeats_played": true,
  "warnings": [],
  "fixes": ["dropped 2 empty measure(s) only homr produced (repeat barlines)"],
  "engines": {"homr": {"gpu": true}, "audiveris": {"used": true}},
  "seconds": 55.3
}
```

Beats count quarter notes from 1 (`beat 2.5` = the eighth after beat 2). `homr` is the reading
written to the output files; `audiveris` is the second engine's reading of the same measure.

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

## Review the work (always do this)

Two independent engines read the score. Where they agree the result is very likely
right; where they disagree, one of them is wrong, and only a look at the score settles it.
There is no other ground truth. So after every successful run:

1. **Glance over the whole result.** Does `measures` match the last printed measure
   number on the score? Are there `warnings`? Many suspects packed into one system often
   means that system was misread as a whole (wrong clef, missed key signature) rather
   than note by note.
2. **Check every suspect measure against its image.** Open `image` (one per system,
   captioned with the measure's position in that system, e.g. "1 of 4"). Find the measure,
   then compare `only_homr` and `only_audiveris` with what is printed: count ledger lines,
   look for accidentals (including ones carried from earlier in the measure), and check
   which notes are held across beats. Decide for each: **homr right**, **Audiveris
   right**, or **unclear**.
3. **Report to the user** in a short list: measure (use `printed_measure` when present,
   so it matches their score), the verdict, and the notes involved. Example:
   "m. 31: homr right (the A in the bass chord is sharp, Audiveris missed it)."
   Say plainly when the output file holds a wrong reading (the "Audiveris right" cases),
   since the output uses homr. Do not edit the output files unless the user asks.
4. If you cannot view images, say so and list the suspect measures with both readings
   for the user to check by eye or by ear.

Known tendencies, useful as a prior but never a substitute for looking:
- Audiveris drops long held chords and sometimes misses accidentals or ledger-line notes.
- homr's known failures (an empty extra measure at start-repeat barlines, ignoring octave
  clefs) are fixed automatically and listed in `fixes`.
- Grace notes are ignored by the comparison.

## Reading the rest of the result

- `suspect_measures` also covers measures that do not add up to the time signature and
  notes outside the piano range; those have a `homr` reading but no `audiveris` one.
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
- Repeats: the MusicXML keeps the repeat signs as printed; the MIDI plays repeated
  sections out (`midi_repeats_played: true`), so it is longer than the printed score.
  1st/2nd endings, D.C. and D.S. are not handled; a warning says so when they may matter.
