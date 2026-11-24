# snap2midi

Turn phone screenshots of piano sheet music into MusicXML and MIDI on Windows.

```
snap2midi.cmd transcribe page1.png page2.png page3.png -o out --json
```

- Screenshots may be page by page or scrolled with overlap; duplicates and overlap are removed.
- Two optical music recognition engines read every page: [homr](https://github.com/liebharc/homr)
  (primary, GPU when available) and [Audiveris](https://github.com/Audiveris/audiveris) (cross-check).
  Measures where they disagree are reported as suspect, with each engine's reading and an
  image of the system, so a person or an agent can check them.
- The MIDI has one track per hand and plays repeated sections out; the MusicXML keeps the
  score as printed.
- An agent skill ([SKILL.md](skill/snap2midi/SKILL.md)) ships with the tool and explains how
  to drive the CLI and review its output.

Piano scores only (two staves), printed music only.

## Install

Download `snap2midi-installer-<version>.zip` from the releases, unzip it and run `install.cmd`.
It downloads everything from the official sources, pinned to tested versions and checked
against SHA-256 hashes, into `%LOCALAPPDATA%\Programs\snap2midi`:

| Component | Source | Size |
| --- | --- | --- |
| Python 3.11 runtime | python.org | ~10 MB |
| Python packages and snap2midi | PyPI, the release | ~0.4 GB |
| homr and OCR models | their release downloads | ~0.2 GB |
| Audiveris (unpacked, not system-installed) | Audiveris GitHub release | ~0.2 GB |
| NVIDIA CUDA and cuDNN, only with an NVIDIA GPU | PyPI | ~1.5 GB |

About 0.9 GB without a GPU, 2.5 GB with one. Options: `install.cmd -Dir D:\snap2midi -Gpu no -Force`.
Internet is needed to install, not to run.

## Development

```
py -3.11 -m venv .venv
.venv\Scripts\python -m pip install -e ".[cuda,test]"
.venv\Scripts\python -m pytest              # fast tests, ~20 s
.venv\Scripts\python -m pytest -m e2e       # real engines, ~5 min on GPU
.venv\Scripts\python packaging\make_installer.py
```

Audiveris must be installed (or `SNAP2MIDI_AUDIVERIS` set) for the cross-check. The
screenshot-based tests need test images in `testdata/`, which are not in the repository;
without them those tests are skipped.

## Third-party software

The installer downloads, and snap2midi runs, third-party software under its own licenses,
including Audiveris (AGPL-3.0), homr, music21 and NVIDIA's CUDA and cuDNN libraries. An
install keeps their license texts in its `licenses` folder.
