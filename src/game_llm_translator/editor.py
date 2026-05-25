from __future__ import annotations

from pathlib import Path
import subprocess
import sys


def open_file_editor(file: Path) -> None:
    """Open a file in the user's default editor, or a folder in Explorer/Finder."""
    if sys.platform.startswith("win"):
        if file.is_dir():
            subprocess.Popen(["explorer", str(file)])
        else:
            subprocess.Popen(["cmd", "/c", "start", "", str(file)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(file)])
    else:
        subprocess.Popen(["xdg-open", str(file)])
