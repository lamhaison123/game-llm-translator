from __future__ import annotations

from pathlib import Path
import subprocess
import sys


def open_file_editor(file: Path) -> None:
    """Open a CSV in the user's default editor/spreadsheet app."""
    if sys.platform.startswith("win"):
        subprocess.Popen(["cmd", "/c", "start", "", str(file)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(file)])
    else:
        subprocess.Popen(["xdg-open", str(file)])
