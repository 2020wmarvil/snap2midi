"""Progress events from the pipeline, for the GUI's baking view (and any other
observer). The CLI does not subscribe.

Every event is (stage, kind, data). Stages arrive in this order:

  preprocess  page      one per screenshot: index, total, source, original (PIL RGB),
                        clean (PIL L, overlays removed), staves [(lines, x0, x1)],
                        systems [{box, barlines, partial}], warnings
  stitch      done      systems [{source, index, number, kept, measures}], dropped,
                        warnings
  name        done      name
  pack        page      one per packed page: index, total, image (PIL L), systems
  recognize   start     engine, pages, gpu (homr)
  recognize   page      engine, index, total, ok        (engines run in parallel)
  recognize   done      engine, ok_pages, total
  merge       done      measures, fixes, suspects [dict], warnings, system_counts
  export      done      musicxml, midi, review_dir
  done        result    the CLI's JSON result as a dict

Events may come from more than one thread (Audiveris runs beside homr). A
listener that raises is ignored, so a broken view cannot stop a transcription.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Event:
    stage: str
    kind: str
    data: dict[str, Any] = field(default_factory=dict)


Listener = Callable[[Event], None]


class Emitter:
    def __init__(self, listener: Listener | None):
        self._listener = listener
        self._lock = threading.Lock()

    @property
    def active(self) -> bool:
        return self._listener is not None

    def __call__(self, stage: str, kind: str, **data: Any) -> None:
        if self._listener is None:
            return
        with self._lock:
            try:
                self._listener(Event(stage, kind, data))
            except Exception:  # noqa: BLE001  a view must never break the pipeline
                pass
