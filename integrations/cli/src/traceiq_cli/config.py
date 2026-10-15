"""Where `traceiq login` keeps the server URL and API key.

One JSON file in the OS-conventional config directory, created 0600 inside a
0700 directory: it holds a live API key. TRACEIQ_URL / TRACEIQ_API_KEY in the
environment override it (CI never needs to log in), and TRACEIQ_CONFIG points
at a different file.
"""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from typing import Dict, Mapping, Optional, Tuple


def config_path(env: Optional[Mapping[str, str]] = None, platform: Optional[str] = None,
                home: Optional[Path] = None) -> Path:
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform
    home = Path.home() if home is None else home
    if env.get("TRACEIQ_CONFIG"):
        return Path(env["TRACEIQ_CONFIG"])
    if platform.startswith("win"):
        base = Path(env.get("APPDATA") or home / "AppData" / "Roaming")
    elif platform == "darwin":
        base = home / "Library" / "Application Support"
    else:
        base = Path(env.get("XDG_CONFIG_HOME") or home / ".config")
    return base / "traceiq" / "config.json"


def load_config(path: Path) -> Dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_config(path: Path, data: Dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, stat.S_IRWXU)
    except OSError:  # e.g. a shared parent we don't own; the file mode still holds
        pass
    # O_CREAT with 0600 so the key is never briefly world-readable; chmod
    # again in case the file already existed with a looser mode.
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.chmod(path, 0o600)


def credentials(env: Optional[Mapping[str, str]] = None,
                path: Optional[Path] = None) -> Tuple[Optional[str], Optional[str]]:
    """(base_url, api_key): environment first, then the config file."""
    env = os.environ if env is None else env
    cfg = load_config(path or config_path(env))
    url = env.get("TRACEIQ_URL") or cfg.get("url")
    key = env.get("TRACEIQ_API_KEY") or cfg.get("api_key")
    return (url.rstrip("/") if url else None), key
