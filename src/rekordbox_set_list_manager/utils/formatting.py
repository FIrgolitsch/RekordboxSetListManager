"""Shared display formatting helpers."""


def format_total_duration(seconds: int) -> str:
    """Format *seconds* as ``H:MM:SS``, or ``M:SS`` when under an hour.

    Parameters
    ----------
    seconds : int
        Total duration in seconds.

    Returns
    -------
    str
        ``H:MM:SS`` when *seconds* is at least an hour, else ``M:SS``.

    """
    mins, secs = divmod(seconds, 60)
    hours, mins = divmod(mins, 60)
    return f"{hours}:{mins:02d}:{secs:02d}" if hours else f"{mins}:{secs:02d}"
