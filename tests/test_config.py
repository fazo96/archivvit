import json

from archivvit import config


def form(**kw):
    base = {
        "subreddits": "NixOS\nr/linux\n/r/selfhosted/\nnixos",
        "posts_per_sub": "25", "scrape_interval_minutes": "60", "refetch_hours": "12, 2",
        "hot_window_hours": "24", "max_thread_requests": "30", "min_delay": "2", "max_delay": "5",
    }
    return {**base, **kw}


def test_defaults_without_file(tmp_path):
    cfg = config.load(tmp_path)
    assert cfg.subreddits == ["NixOS"] and cfg.data_dir == tmp_path


def test_update_save_load_roundtrip(tmp_path):
    cfg = config.load(tmp_path)
    assert config.update(cfg, form()) == []
    assert cfg.subreddits == ["NixOS", "linux", "selfhosted"]
    assert cfg.refetch_hours == [2.0, 12.0]
    config.save(cfg)
    saved = json.loads((tmp_path / "settings.json").read_text())
    assert "data_dir" not in saved and saved["subreddits"] == ["NixOS", "linux", "selfhosted"]
    assert config.load(tmp_path).refetch_hours == [2.0, 12.0]


def test_update_rejects_bad_input_and_keeps_cfg(tmp_path):
    cfg = config.load(tmp_path)
    errors = config.update(cfg, form(subreddits="not a sub!", scrape_interval_minutes="1",
                                     refetch_hours="soon", min_delay="9", max_delay="3"))
    assert len(errors) == 4
    assert cfg.subreddits == ["NixOS"] and cfg.scrape_interval_minutes == 60


def test_update_needs_a_subreddit(tmp_path):
    cfg = config.load(tmp_path)
    assert config.update(cfg, form(subreddits="  \n")) == ["subreddits: add at least one"]
