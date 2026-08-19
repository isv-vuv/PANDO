import math


def format_bytes(size_b, unknown_size="Unknown size"):
    if size_b is None:
        return unknown_size
    if size_b == 0:
        return "0 B"
    units = ("B", "KB", "MB", "GB", "TB")
    i = int(math.floor(math.log(size_b, 1024))) if size_b > 0 else 0
    p = math.pow(1024, i)
    s = round(size_b / p, 2) if p > 0 else 0
    return f"{s} {units[i]}"


def format_eta(seconds):
    if seconds is None or seconds < 0:
        return "--:--"
    minutes, sec = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{sec:02d}"
    return f"{minutes:02d}:{sec:02d}"

