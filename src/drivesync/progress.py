"""rclone one-line stats and incremental log reading, with no GI dependency."""

from pathlib import Path
import re
import time


_SIZE = r"(\d+(?:\.\d+)?)\s*(B|KiB|MiB|GiB|TiB|kB|MB|GB|TB)"
_STATS = re.compile(
    r"^(?:\d{4}/\d{2}/\d{2} \S+\s+INFO\s*:\s*|INFO\s*:\s*)?"
    r"(?:Transferred:\s*)?" + _SIZE + r"\s*/\s*" + _SIZE +
    r",\s*(\d+(?:\.\d+)?)%,\s*" + _SIZE +
    r"/s,\s*ETA\s+(\S+?)(?:\s+\(xfr#(\d+)/(\d+)\))?\s*$")


def _bytes(number, unit):
    powers = {"B": 0, "KiB": 1, "MiB": 2, "GiB": 3, "TiB": 4,
              "kB": 1, "MB": 2, "GB": 3, "TB": 4}
    return float(number) * (1024 if unit in ("B", "KiB", "MiB", "GiB", "TiB") else 1000) ** powers[unit]


def parse_progress(text):
    """Return the latest complete stats line; speed is bytes/second, ETA nullable."""
    latest = None
    for line in text.splitlines():
        match = _STATS.fullmatch(line.strip())
        if not match:
            continue
        done, du, total, tu, percent, speed, su, eta, files, count = match.groups()
        seconds = None
        if eta != "-":
            parts = re.findall(r"(\d+(?:\.\d+)?)(ms|d|h|m|s)", eta)
            if not parts or "".join(n + u for n, u in parts) != eta:
                continue
            seconds = round(sum(float(n) * {"d": 86400, "h": 3600, "m": 60,
                                           "s": 1, "ms": .001}[u] for n, u in parts))
        latest = dict(bytes_done=round(_bytes(done, du)), bytes_total=round(_bytes(total, tu)),
                      percent=min(100, float(percent)), speed=_bytes(speed, su), eta_seconds=seconds,
                      files_done=int(files) if files is not None else None,
                      files_total=int(count) if count is not None else None)
    return latest


class LogProgress:
    """Tail only new complete lines, at most every two seconds, with bounded memory."""

    def __init__(self, path, publish, clock=time.monotonic):
        self.path = Path(path)
        self.publish = publish
        self.clock = clock
        self.last_poll = float("-inf")
        self.offset = 0
        self.pending = b""

    def poll(self):
        moment = self.clock()
        if moment - self.last_poll < 2:
            return
        self.last_poll = moment
        latest = None
        try:
            with self.path.open("rb") as stream:
                if stream.seek(0, 2) < self.offset:
                    self.offset, self.pending = 0, b""
                stream.seek(self.offset)
                while chunk := stream.read(65536):
                    lines = (self.pending + chunk).split(b"\n")
                    self.pending = lines.pop()[-65536:]
                    parsed = parse_progress(b"\n".join(lines).decode("utf-8", errors="replace"))
                    if parsed is not None:
                        latest = parsed
                self.offset = stream.tell()
        except FileNotFoundError:
            return
        if latest is not None:
            self.publish(latest)
