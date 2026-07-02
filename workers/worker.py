from __future__ import annotations

import os

from redis import Redis
from rq import Worker

listen = ["tracking"]
redis_conn = Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))

if __name__ == "__main__":
    worker = Worker(listen, connection=redis_conn)
    worker.work()
