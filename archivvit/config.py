"""Settings live in <data_dir>/settings.json; missing keys fall back to the defaults here."""

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

DEFAULT_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"


@dataclass
class Config:
    subreddits: list[str] = field(default_factory=lambda: ["NixOS"])
    # Minutes between scrape passes when running `archivvit serve`.
    scrape_interval_minutes: int = 60
    # How many hot posts to archive per subreddit.
    posts_per_sub: int = 25
    # Thread re-fetches, in hours after the first one.
    refetch_hours: list[float] = field(default_factory=lambda: [2, 12])
    # Only fetch threads of posts seen in hot within this many hours.
    hot_window_hours: float = 24
    # Cap on extra requests (other sorts, more replies, continue thread) per thread fetch.
    max_thread_requests: int = 30
    # Delete posts (and their comments) this many days after they were last in hot or
    # fetched; kept posts are never deleted. 0 keeps everything.
    retention_days: int = 0
    # Random pause between requests, seconds.
    min_delay: float = 2.0
    max_delay: float = 5.0
    # Wait for the rate-limit window to reset when fewer requests than this remain.
    ratelimit_floor: int = 20
    user_agent: str = DEFAULT_UA
    data_dir: Path = field(default=Path("data"), metadata={"saved": False})

    @property
    def db_path(self) -> Path:
        return self.data_dir / "archive.sqlite"

    @property
    def cookies_path(self) -> Path:
        return self.data_dir / "cookies.json"

    @property
    def settings_path(self) -> Path:
        return self.data_dir / "settings.json"


SAVED = [f.name for f in fields(Config) if f.metadata.get("saved", True)]


def default_data_dir() -> Path:
    return Path(os.environ.get("ARCHIVVIT_DATA", "data"))


def load(data_dir: Path | str | None = None) -> Config:
    cfg = Config(data_dir=Path(data_dir) if data_dir else default_data_dir())
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    try:
        raw = json.loads(cfg.settings_path.read_text())
    except FileNotFoundError:
        raw = {}
    for k in SAVED:
        if k in raw:
            setattr(cfg, k, raw[k])
    return cfg


def save(cfg: Config):
    data = {k: v for k, v in asdict(cfg).items() if k in SAVED}
    tmp = cfg.settings_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(cfg.settings_path)


def update(cfg: Config, form: dict[str, str]) -> list[str]:
    """Apply the settings form to `cfg`. Returns errors; `cfg` is only changed when there are none."""
    errors = []
    new = {}

    def num(key, kind, lo, hi=None):
        try:
            v = kind(form.get(key, "").strip())
        except ValueError:
            errors.append(f"{key.replace('_', ' ')}: not a number")
            return
        if v < lo or (hi is not None and v > hi):
            errors.append(f"{key.replace('_', ' ')}: must be between {lo} and {hi}" if hi else
                          f"{key.replace('_', ' ')}: must be at least {lo}")
            return
        new[key] = v

    subs = []
    for line in form.get("subreddits", "").replace(",", "\n").splitlines():
        s = line.strip().removeprefix("/").removeprefix("r/").strip("/")
        if not s:
            continue
        if not s.replace("_", "").isalnum() or len(s) > 21:
            errors.append(f"subreddits: {s!r} is not a subreddit name")
        elif s.lower() not in {x.lower() for x in subs}:
            subs.append(s)
    if not subs and not errors:
        errors.append("subreddits: add at least one")
    new["subreddits"] = subs

    num("scrape_interval_minutes", int, 10, 24 * 60)
    num("posts_per_sub", int, 1, 100)
    num("hot_window_hours", float, 1)
    num("max_thread_requests", int, 0, 500)
    num("retention_days", int, 0, 36500)
    num("min_delay", float, 0.5)
    num("max_delay", float, 0.5)
    if "min_delay" in new and "max_delay" in new and new["max_delay"] < new["min_delay"]:
        errors.append("max delay: must not be below min delay")
    try:
        hours = [float(x) for x in form.get("refetch_hours", "").replace(",", " ").split()]
        if any(h <= 0 for h in hours):
            raise ValueError
        new["refetch_hours"] = sorted(hours)
    except ValueError:
        errors.append("refetch hours: positive numbers separated by commas")

    if not errors:
        for k, v in new.items():
            setattr(cfg, k, v)
    return errors
