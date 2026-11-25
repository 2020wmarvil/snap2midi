"""snap2midi desktop GUI (PySide6). Start with `snap2midi gui` or `python -m snap2midi.gui`."""


def main(argv=None) -> int:
    from .app import main as _main
    return _main(argv)
