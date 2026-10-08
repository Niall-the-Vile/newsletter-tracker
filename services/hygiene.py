"""Startup warnings about the data file's surroundings."""
import glob
import os
from pathlib import Path


def conflict_copies(db_path):
    """OneDrive conflict copies look like newsletter_tracker-<machine>.db next to the data file."""
    p = Path(db_path)
    pattern = os.path.join(glob.escape(str(p.parent)), glob.escape(p.stem) + "-*" + glob.escape(p.suffix))
    return sorted(os.path.basename(f) for f in glob.glob(pattern))


def git_root_of(db_path):
    """The git working tree holding the data file, or None."""
    folder = Path(db_path).resolve().parent
    for d in (folder, *folder.parents):
        if (d / ".git").exists():
            return str(d)
    return None


def startup_warnings(db_path):
    warnings = []
    copies = conflict_copies(db_path)
    if copies:
        warnings.append(
            f"Found {len(copies)} extra copy of the data file next to it ({', '.join(copies[:3])}). "
            "OneDrive makes these when two people save at once. Ask Niall before using either one."
        )
    root = git_root_of(db_path)
    if root:
        warnings.append(
            f"The data file is inside a code folder ({root}). Real names and addresses must never be "
            "uploaded to GitHub. Move the data file out of it."
        )
    return warnings
