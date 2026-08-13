"""Configuration from environment variables with sane defaults.

No config file, no secrets. Every value has a default so the tool runs
immediately after clone (Constitution Principle VII).
"""

import os
from pathlib import Path

ENV_OUTPUT_DIR = "XVD_OUTPUT_DIR"


def output_dir(override: Path | None = None) -> Path:
    """Resolve where finished files are written.

    Precedence: override (the --output-dir flag) > $XVD_OUTPUT_DIR > cwd.
    The flag wins so a scripted override never has to mutate the environment.

    The directory is created if absent. Raises OSError if it cannot be created
    or is not a directory.
    """
    if override is not None:
        chosen = Path(override)
    else:
        from_env = os.environ.get(ENV_OUTPUT_DIR)
        chosen = Path(from_env) if from_env else Path.cwd()

    chosen = chosen.expanduser()
    chosen.mkdir(parents=True, exist_ok=True)
    if not chosen.is_dir():
        raise OSError(f"output directory is not a directory: {chosen}")
    return chosen.resolve()
