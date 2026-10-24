import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"


@dataclass
class Config:
    data_dir: Path = Path("data")
    subreddits: list[str] = field(default_factory=lambda: ["NixOS"])
    # How many hot posts to archive per subreddit.
    posts_per_sub: int = 25
    # Thread re-fetches, in hours after the first one.
    refetch_hours: list[float] = field(default_factory=lambda: [2, 12])
    # Only fetch threads of posts seen in hot within this many hours.
    hot_window_hours: float = 24
    # Cap on extra requests (more replies / continue thread) per thread fetch.
    max_thread_requests: int = 30
    # Random pause between requests, seconds.
    min_delay: float = 2.0
    max_delay: float = 5.0
    # Wait for the rate-limit window to reset when fewer requests than this remain.
    ratelimit_floor: int = 20
    user_agent: str = DEFAULT_UA

    @property
    def db_path(self) -> Path:
        return self.data_dir / "archive.sqlite"

    @property
    def cookies_path(self) -> Path:
        return self.data_dir / "cookies.json"


def load(path: str | None) -> Config:
    path = path or os.environ.get("ARCHIVVIT_CONFIG")
    raw = {}
    if path:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    cfg = Config(**{k: v for k, v in raw.items() if k != "data_dir"})
    if "data_dir" in raw:
        cfg.data_dir = Path(raw["data_dir"])
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    return cfg
