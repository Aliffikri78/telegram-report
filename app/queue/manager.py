import threading
from typing import Callable

try:
    from core.logger import logger
except ImportError:
    from app.core.logger import logger


class QueueManager:
    def __init__(self):
        self._threads = []
        self._lock = threading.Lock()

    def submit(self, target: Callable, *args, **kwargs) -> threading.Thread:
        thread = threading.Thread(target=target, args=args, kwargs=kwargs, daemon=True)
        thread.start()
        with self._lock:
            self._threads.append(thread)
        logger.info("Started queued task in thread %s", thread.name)
        return thread
