# Engine benchmark (milestone 2), 2026-11-23

Test set: 8 piano pieces, 34 phone screenshots (one page each, no overlap), in `testdata/`.
Each engine ran on single, unstitched screenshots. No hand-checked ground truth yet, so
accuracy is measured with page-level proxies plus engine-vs-engine note agreement,
and disputed pages were checked by eye.

| Engine | Version | Pages output | Measure count exact | Key right | Time sig right (p1) | Bars filled exactly | Speed (CPU) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| homr | 0.7.1 | 34/34 | 29/30 | 100% | 8/8 | 99% | ~30 s/page |
| Audiveris | 5.11.0 | 34/34 | 29/30 | 100%* | 8/8 | 86% | ~7 s/page |
| oemer | 0.1.8 | 4/5 (stopped) | 4/4 | 100% | n/a | 21% | ~140 s/page |

\* Audiveris omits the key element for C major / A minor; counted as right.

Note-level agreement (F1 on measure, onset, pitch): Audiveris vs homr 0.923 mean over
34 pages, 18 pages at 0.98 or above. oemer vs either: 0.45 (4 pages).

Disputed pages, checked against the image:

| Page | Winner | What went wrong |
| --- | --- | --- |
| lovely p2-p7 | homr | Audiveris drops held whole-note bass triads, loses a measure on p6 |
| Je Te Laisserai p2, p5 | Audiveris | homr inserts an empty measure at a start-repeat barline, shifting the rest |
| Hollow Knight p1 | Audiveris | homr ignores the 8va treble clef, so the right hand is an octave low |

Other findings:
- homr reads the phone clock ("7:32") as the title; preprocessing must crop the status bar.
- oemer needs `onnxruntime<1.19` (its model fails on 1.32), so it lives in `.venv-oemer`.
- homr fell back to CPU: onnxruntime-gpu wants CUDA 13 DLLs. The machine has an RTX 3080.

Recommendation: homr as the primary engine, Audiveris as a second opinion. Measures where
the two disagree become the "suspect measure" warnings the CLI must print.

Reproduce: `python bench/bench.py run --engines audiveris,homr`, then `score` and `agree`.
