"""On-demand fetches from the web UI: a thread, one comment, or a subreddit's hot page.

One background worker runs them in order through the shared client, whose requests go
ahead of the scheduler's. The UI polls `status()` and reloads until a job is done.
"""

import logging
import queue
import threading
import time
from dataclasses import dataclass, field

from . import config
from .client import Reddit
from .scrape import fetch_hot, fetch_thread
from .store import Store

log = logging.getLogger(__name__)

# Jobs are remembered this long after finishing, so the page can say how it went.
KEEP_SECONDS = 600
# "More replies"/"continue this thread" requests for a single comment's page.
COMMENT_REQUESTS = 10

THREAD, COMMENT, HOT = "thread", "comment", "hot"


@dataclass
class Job:
    kind: str
    target: str  # post id (no t3_), "<post id>/<comment id>", or a subreddit name
    state: str = "queued"  # queued, running, done, failed
    phase: str = ""
    error: str | None = None
    finished_at: float | None = None
    created_at: float = field(default_factory=time.monotonic)

    @property
    def key(self) -> str:
        return job_key(self.kind, self.target)

    @property
    def active(self) -> bool:
        return self.state in ("queued", "running")


def job_key(kind: str, target: str) -> str:
    return f"{kind}:{target.lower()}"


class Fetcher(threading.Thread):
    def __init__(self, data_dir, reddit: Reddit):
        super().__init__(name="fetcher", daemon=True)
        self.data_dir = data_dir
        self.reddit = reddit
        self.queue: queue.Queue[Job] = queue.Queue()
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()

    def submit(self, kind: str, target: str) -> Job:
        if kind not in (THREAD, COMMENT, HOT):
            raise ValueError(kind)
        with self.lock:
            self._forget_old()
            job = self.jobs.get(job_key(kind, target))
            if job and job.active:
                return job
            job = Job(kind, target)
            self.jobs[job.key] = job
        self.queue.put(job)
        return job

    def status(self, kind: str, target: str) -> Job | None:
        with self.lock:
            return self.jobs.get(job_key(kind, target))

    def _forget_old(self):
        now = time.monotonic()
        for k in [k for k, j in self.jobs.items() if j.finished_at and now - j.finished_at > KEEP_SECONDS]:
            del self.jobs[k]

    def run(self):
        while True:
            self.run_job(self.queue.get())

    def run_job(self, job: Job):
        job.state = "running"
        cfg = config.load(self.data_dir)
        store = Store(cfg.db_path)
        try:
            getattr(self, f"_{job.kind}")(cfg, store, job)
            job.state = "done"
        except Exception as e:
            log.exception("fetch %s failed", job.key)
            job.state, job.error = "failed", f"{type(e).__name__}: {e}"
        finally:
            store.close()
            job.finished_at = time.monotonic()

    def _thread(self, cfg, store, job):
        job.phase = "thread page"

        def first(post, comments):
            store.record_thread(post, post.id, comments, count=False)
            job.phase = "more comments"

        post, comments, extra = fetch_thread(
            self.reddit.urgent(), f"/comments/{job.target}/", cfg.max_thread_requests, on_first=first
        )
        store.record_thread(post, post.id, comments)
        log.info("fetched %s on demand: %d/%s comments (+%d requests)", post.id, len(comments), post.comment_count, extra)

    def _comment(self, cfg, store, job):
        pid, cid = job.target.split("/")
        job.phase = "comment page"
        post, comments, _ = fetch_thread(
            self.reddit.urgent(), f"/comments/{pid}/comment/{cid}/", COMMENT_REQUESTS, extra_sorts=False
        )
        store.record_thread(post, post.id, comments, count=False)

    def _hot(self, cfg, store, job):
        job.phase = "hot page"
        posts = fetch_hot(self.reddit.urgent(), job.target, cfg.posts_per_sub)
        store.record_hot(job.target, posts)
