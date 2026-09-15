"""Adapter isolating private pyrekordbox config access for DB discovery.

The private ``_get_rb6_config``/``_get_rb7_config`` helpers live behind this
single seam so a signature change in pyrekordbox produces one localized
failure instead of scattered private imports.  The public
``get_pioneer_install_dir``/``get_pioneer_app_dir`` helpers are called
directly — they are part of the stable API.
"""

import logging
from pathlib import Path

log = logging.getLogger(__name__)


def rekordbox_db_candidates() -> list[Path]:
    """Return existing Rekordbox database paths, Rekordbox 7 first then 6.

    Each version's config is probed independently: if one config raises,
    the other version's candidate is still returned.

    Returns
    -------
    list[Path]
        Candidate ``master.db`` paths that exist on disk, ordered
        Rekordbox 7 then Rekordbox 6.  Empty when neither version's
        config yields an existing database.
    """
    from pyrekordbox.config import (  # noqa: PLC0415
        _get_rb6_config,  # type: ignore[attr-defined]
        _get_rb7_config,  # type: ignore[attr-defined]
        get_pioneer_app_dir,
        get_pioneer_install_dir,
    )

    prog = get_pioneer_install_dir()
    app = get_pioneer_app_dir()

    candidates: list[Path] = []
    for get_config in (_get_rb7_config, _get_rb6_config):
        try:
            cfg = get_config(prog, app)
        except Exception:  # one broken config must not mask the other
            log.debug("Rekordbox config probe failed", exc_info=True)
            continue
        db_path = cfg.get("db_path")
        if db_path is not None:
            candidate = Path(str(db_path))
            if candidate.exists():
                candidates.append(candidate)
    return candidates
