import asyncio
import re
import time
from datetime import datetime
from pathlib import Path

from telegram import Bot

try:
    from core.logger import logger
    from storage.storage import Storage
except ImportError:
    from app.core.logger import logger
    from app.storage.storage import Storage

try:
    from telegram.error import RetryAfter
except Exception:
    RetryAfter = None


class DownloadWorker:
    def __init__(self, manager: "DownloadManager", token: str, timeout: float, poll_interval: float = 1.0):
        self.manager = manager
        self.token = token
        self.timeout = timeout
        self.poll_interval = poll_interval
        self._running = True
        self.storage = Storage()

    def stop(self) -> None:
        self._running = False

    def run(self) -> None:
        logger.info("Download worker started")
        while self._running:
            task = self.manager.claim_next_task()
            if not task:
                time.sleep(self.poll_interval)
                continue
            self._run_task(task)

    def _run_task(self, task) -> None:
        task_id = task["id"]
        try:
            destination = self._destination_for(task)
            self.manager.mark_destination(task_id, str(destination))
            temp_path = self.storage.temp_path_for(destination)
            self.storage.unlink(temp_path)
            asyncio.run(self._download(task["file_id"], temp_path))
            self.storage.validate_image(temp_path)
            self.storage.atomic_replace(temp_path, destination)
            self.manager.mark_done(task_id)
            logger.info("Downloaded Telegram photo task %s to %s", task_id, destination)
        except Exception as exc:
            try:
                if "temp_path" in locals():
                    self.storage.unlink(temp_path)
            except Exception:
                pass
            logger.warning("Telegram photo download task %s failed: %s", task_id, exc)
            self.manager.mark_failed(task_id, str(exc))

    async def _download(self, file_id: str, destination: Path) -> None:
        bot = Bot(self.token)
        try:
            self.manager.wait_for_rate_limit()
            tg_file = await bot.get_file(
                file_id,
                read_timeout=self.timeout,
                write_timeout=self.timeout,
                connect_timeout=self.timeout,
                pool_timeout=self.timeout,
            )
        except TypeError:
            self.manager.wait_for_rate_limit()
            tg_file = await bot.get_file(file_id)
        except Exception as exc:
            if RetryAfter and isinstance(exc, RetryAfter):
                self.manager.wait_for_retry_after(float(exc.retry_after))
            raise

        try:
            self.manager.wait_for_rate_limit()
            await tg_file.download_to_drive(
                custom_path=str(destination),
                read_timeout=self.timeout,
                write_timeout=self.timeout,
                connect_timeout=self.timeout,
                pool_timeout=self.timeout,
            )
        except TypeError:
            self.manager.wait_for_rate_limit()
            await tg_file.download_to_drive(custom_path=str(destination))
        except Exception as exc:
            if RetryAfter and isinstance(exc, RetryAfter):
                self.manager.wait_for_retry_after(float(exc.retry_after))
            raise

    def _destination_for(self, task) -> Path:
        dt = self._message_datetime(task.get("message_date"))
        month = dt.strftime("%Y-%m")
        uniq = task.get("file_unique_id") or f"{task['chat_id']}_{task.get('message_id')}_{task['file_id']}"
        safe_caption = re.sub(r"[^a-zA-Z0-9_-]+", "_", (task.get("caption") or "").strip())[:40] or "photo"
        filename = f"{task['site'].lower()}_{task['task']}_{task['when_label']}_{dt.strftime('%Y%m%d_%H%M%S')}_{uniq}_{safe_caption}.jpg"
        return self.storage.allocate_photo_path(month, task["site"], task["task"], task["when_label"], filename)

    def _message_datetime(self, raw: str) -> datetime:
        if raw:
            try:
                return datetime.fromisoformat(raw)
            except ValueError:
                pass
        return datetime.utcnow()
