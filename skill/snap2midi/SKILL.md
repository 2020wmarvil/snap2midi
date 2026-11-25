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

Where the command lives: in a Windows install this file sits next to the launcher, so run
`snap2midi.cmd` from the same folder as this SKILL.md (use its full path) in place of
`snap2midi`. If `snap2midi` is on PATH, that works too. If neither exists, ask the user where
snap2midi is installed; do not guess.

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

Runtime: roughly 10 to 60 s per piece on an NVIDIA GPU, a few times longer on CPU.

## Output (`--json`)

Success (example from a short piece; your values will differ):

```json
{
  "ok": true,
  "name": "Hollow Knight Main Theme",
  "musicxml": "C:\\...\\Hollow Knight Main Theme.musicxml",
  "midi": "C:\\...\\Hollow Knight Main Theme.mid",
  "measures": 26,
  "printed_last_measure": 26,
  "systems": 8,
  "screenshots": 2,
  "duplicates_skipped": 0,
  "suspect_measures": [
    {
      "measure": 23,
      "reason": "engines disagree on 22% of notes",
      "system": 7, "position": 3, "of": 3, "printed_measure": 23,
      "homr": {"treble": "beat 1: C5(half); beat 3.25: D5(eighth); beat 3.75: E-5(eighth)", "bass": "beat 1: C3(eighth); ..."},
      "audiveris": {"treble": "beat 1: C5(half); beat 3.5: D5(eighth); beat 4: E-5(eighth)", "bass": "beat 1: C3(eighth); ..."},
      "only_homr": ["beat 3.25 D5", "beat 3.75 E-5"],
      "only_audiveris": ["beat 3.5 D5", "beat 4 E-5"],
      "image": "C:\\...\\Hollow Knight Main Theme.review\\system07.png",
      "measure_image": "C:\\...\\Hollow Knight Main Theme.review\\system07_measure023.png"
    }
  ],
  "review_dir": "C:\\...\\Hollow Knight Main Theme.review",
  "midi_repeats_played": false,
  "structure": {
    "play_order": "1-26",
    "measures_played": 26,
    "repeats": [],
    "endings": [],
    "markings": []
  },
  "warnings": [],
  "fixes": ["system 1, treble staff shifted up an octave (octave clef)"],
  "engines": {"homr": {"gpu": true}, "audiveris": {"used": true}},
  "seconds": 11.1
}
```

How to read a suspect measure:
- `homr` is the reading **written to the output files**. `audiveris` is the second engine's
  reading of the same measure, used only for checking.
- Beats count quarter notes from 1 (`beat 2.5` = the eighth after beat 2). Durations are
  spelled out with dots and ties: `half`, `dotted half`, `quarter, tied` (tied into the
  next measure).
- `only_homr` / `only_audiveris`: notes one engine has and the other lacks, by beat.
  `x2` means that engine has the note twice at that beat (for example in two voices).
- `measure`: position in the output file. `printed_measure`: the number on the user's
  score. `position` / `of`: where the measure sits in its line of music.
- `measure_image`: an enlarged crop of just that measure. `image`: the whole line of music
  around it. `measure_image` is absent when the barlines could not be matched; then use
  `image` and count measures from the left.

`structure` says in which order the MIDI plays the measures. For a score with repeats and
a D.S. it looks like this (measure numbers as in the output file):

```json
"structure": {
  "play_order": "1-5, 1-4, 6-10, 3-4, 6, 11-14",
  "measures_played": 21,
  "repeats": [{"measure": 1, "sign": "start"}, {"measure": 5, "sign": "end"}],
  "endings": [{"measure": 5, "numbers": [1]}, {"measure": 6, "numbers": [2]}],
  "markings": [{"kind": "segno", "measure": 3, "text": "Bravura"},
               {"kind": "to_coda", "measure": 6, "text": "To Coda"},
               {"kind": "ds_al_coda", "measure": 10, "text": "D.S. al Coda"},
               {"kind": "coda", "measure": 11, "text": "Leland"}]
}
```

The MusicXML keeps the score as printed (repeat signs, endings and markings included); the
MIDI follows `play_order`.

Failure: `{"ok": false, "exit_code": 3, "error": "no piano staves found in the screenshots"}`

## Exit codes

| Code | Meaning | What to do |
| --- | --- | --- |
| 0 | Files written (may still have warnings) | Read `suspect_measures` and `warnings` |
| 1 | Internal error (a bug) | Report the `error` text; retrying will not help |
| 2 | Bad arguments or unreadable image | Fix the paths or file types |
| 3 | No piano staves found | Images are not sheet music, or too small/blurry |
| 4 | Recognition failed | Retry with `--cpu`; if it persists, report it |
| 5 | Engine missing | The install is broken; rerun the installer with -Force |

## Review the work (always do this)

Two independent engines read the score. Where they agree the result is very likely
right; where they disagree, one of them is wrong, and only a look at the score settles it.
There is no other ground truth. So after every successful run:

1. **Glance over the whole result.** Read `warnings` first. `measures` should equal
   `printed_last_measure` (the score's own last measure number, read from the page); the
   tool warns when they differ. Many suspects in one line of music usually mean that line
   was misread as a whole (wrong clef, missed key signature), not note by note.
2. **Check every suspect measure against its image.** Remember: the output files hold the
   `homr` reading. Open `measure_image` (enlarged) and, for context, `image`. Compare
   `only_homr` and `only_audiveris` with what is printed:
   - count ledger lines above and below the staff;
   - look for accidentals, including ones carried from earlier in the same measure, and
     ones carried into this measure by a tie from the previous one;
   - check which notes are held across beats (dots, ties) and where each note starts.
   Decide for each: **homr right** (files are correct), **Audiveris right** (files are
   wrong here), or **unclear**.
3. **Report to the user** in a short list: the measure (use `printed_measure` when present,
   so it matches their score), the verdict, and the notes involved. Example:
   "m. 31: homr right (the A in the bass chord is sharp; Audiveris missed it)."
   Say plainly which measures in the files are wrong (the "Audiveris right" and "unclear"
   cases). If every suspect is "homr right", say that the files need no correction and
   list the checks briefly. Do not edit the output files unless the user asks.
4. **Check the play order.** If the score has repeat signs, 1st/2nd endings, D.C., D.S.,
   Fine or a Coda, compare `structure.play_order` with what the score asks for. Look for
   markings the tool missed (they are read from the page by OCR and sign matching) and
   for any structure warning. If the order is wrong, tell the user which measures the MIDI
   plays in the wrong order; the MusicXML is unaffected.
5. If you cannot view images, say so and list the suspect measures with both readings
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
  - `the score's last printed measure is N but M measures were recognized`: measures were
    lost or added somewhere; the per-system warnings below usually say where.
  - `system K: N measures recognized but the barlines show M`: that line of music needs
    a look as a whole.
  - `measure numbers go backwards ...`: screenshots are probably out of order.
  - `... duplicate of previous screenshot, skipped`: harmless.
  - `Audiveris not found ...`: no cross-check was done, so `suspect_measures` only covers
    bar-length and range problems.
  - `1st/2nd ending seen by only one engine at measure(s) ...; not applied`: check the score;
    if there really is an ending there, the MIDI plays the wrong pass.
  - `the score says D.S. but no segno sign was found`, `... al Coda but no coda sign ...`,
    `no To Coda point was found`: a jump was read but its target was not; the MIDI then jumps
    to the start or plays on. Check `play_order` against the score.
- `fixes`: automatic corrections already applied. Informational.

## Limits

- Piano scores only (two staves). Lead sheets and other instruments are not supported.
- Printed, engraved music. Handwritten scores will fail or be poor.
- Repeats, 1st/2nd endings, D.C., D.S., Fine and Coda are followed in the MIDI
  (`midi_repeats_played: true`, order in `structure.play_order`). After a D.C. or D.S.,
  repeats are not taken again and the last ending is played, the usual convention.
  Unusual instructions ("D.C. con repetizione", multiple codas, written-out text
  directions) are not understood.
