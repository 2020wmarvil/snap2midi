# Roadmap

Status as of 2026-11-25 (updated after the repeat-structure work). Four phases, each ending at a gate the next one depends on.
No dates are set.

| Phase | Gate at the end | Status |
| --- | --- | --- |
| 1. Harden the CLI | The "done means" criteria are met (output good enough to play along to without editing; suspect measures reported) | Done; real scrolled captures deferred |
| 2. Installer | Runs on a clean PC, GPU or not | Built and tested on a clean CI runner; a second real PC is still untried |
| 3. Desktop app | Produces the same files as the CLI | Done |
| 4. Play-along handoff | The play-along app's needs are written down | Play order done (repeats, endings, D.C., D.S., Fine, Coda); agreeing the contract not started |

## 1. Harden the CLI

| Item | Done when | Status |
| --- | --- | --- |
| Regression tests | Pass on a clean checkout and fail on a deliberate regression | Done: fast suite (no engines, about 20 s) and end-to-end suite (`pytest -m e2e`, about 4 min on GPU). Two injected bugs were caught. |
| Real scrolled captures of 2 or 3 pieces | Each stitches to the same systems as its paged capture | Deferred. Synthetic scrolled captures of every test piece are covered by the tests. |

There is no hand-checked ground truth and none is planned. Accuracy rests on two engines
cross-checking each other, with every disagreement reported for review (by a person or an
agent following SKILL.md).

## 2. Installer

| Item | Done when | Status |
| --- | --- | --- |
| Downloading installer | Runs on a PC with no Python, Java or CUDA installed; falls back to CPU without an NVIDIA GPU | Done: `install.cmd` downloads pinned, SHA-256-checked components; CUDA only with an NVIDIA GPU. Tested here (GPU 2.48 GB, CPU 0.92 GB before the app; CPU 1.14 GB with it) and on a clean GitHub runner. Not yet tried on a second real PC. |
| Agent skill ships with the tool | An agent transcribes a folder using only the skill | Done: SKILL.md sits next to the launcher. An agent given only SKILL.md judged all 5 suspect measures of a test piece correctly; its feedback is folded in. |
| CI | Every push shows a pass/fail check; a failing check blocks merging | Done: CI runs on every push and pull request; branch protection on `main` requires it for pull requests (admins can still push directly). |
| Releases | A fresh install from a release zip passes | Done: a `v*` tag builds the installer package, installs it on a clean runner, checks the CLI, then publishes. A manual run does all but publishing. No release tagged yet. |

## 3. Desktop app

| Item | Done when | Status |
| --- | --- | --- |
| Window: input and run | Same files as the CLI for every test piece | Done: drop screenshots or a folder, reorder, name, output folder, cross-check toggle, Transcribe / Stop. Runs the CLI's pipeline. |
| Baking view | Every stage shows as it happens and stays browsable | Done: cleaned screenshots with staves, systems and barlines; stitched systems with dropped overlaps greyed; packed pages; a lane per engine with page progress; fixes, warnings and suspect measures; output. |
| Score viewer | Opens every test output with suspect measures marked | Done: Verovio engraving, suspect measures outlined and clickable for both readings, MIDI playback, opens any .musicxml. |
| Ship the app in the installer | Installed app starts from the Start menu | Done: `snap2midi-gui.cmd`, Start menu entry (skippable with `-NoShortcut`), `uninstall.cmd`; the installer's self-check loads the app. |

## 4. Play-along handoff

The play-along app itself is out of scope; this phase only settles what it needs from the files.

| Item | Status |
| --- | --- |
| Two staves, one MIDI track per hand | Done |
| Measure numbers matching the printed score | Done (`printed_last_measure` checks it) |
| Repeats played out in the MIDI, kept as printed in the MusicXML | Done |
| 1st/2nd endings, D.C., D.S., Fine, Coda | Done: endings where both engines agree; D.C./D.S./Fine/To Coda read by OCR and segno/coda signs matched on the page (only searched where the words call for them). The MIDI follows the computed play order; the MusicXML shows the markings; `structure.play_order` in the JSON. Tested on generated pieces and a public-domain Mutopia engraving (tests/fixtures/structure). |
| Agree the contract with the play-along app | Not started |

## Open decisions and next steps

- [x] Handle 1st/2nd endings, D.C., D.S., Fine and Coda.
- [x] Branch protection for `main` (CI required for pull requests).
- [ ] Find a real (not generated) score with D.S. al Coda for the test set; Mutopia has none for solo piano.
- [ ] Tag the first release (`v0.1.0`).
- [ ] Try the installer on a second Windows PC, with and without an NVIDIA GPU.
- [ ] Add 2 or 3 real scrolled captures to the test set.
- [ ] Optionally rewrite the existing commits to the GitHub no-reply email.

## Known limitations

- Piano scores only (two staves), printed music only.
- After a D.C. or D.S. the usual convention is assumed (repeats not taken again, last ending played); unusual instructions such as "D.C. con repetizione" are not understood.
- Windows only (the installer, MIDI playback in the app and the GPU path).
