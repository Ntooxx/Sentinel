from __future__ import annotations

import signal
import time
from typing import Callable, Optional


class MonitorService:
    """Interruptible polling loop used for continuous project monitoring."""

    def __init__(self, interval_seconds: int, logger=None, error_delay_seconds: int = 5):
        self.interval_seconds = max(1, int(interval_seconds))
        self.error_delay_seconds = max(1, int(error_delay_seconds))
        self.logger = logger
        self.running = False

    def stop(self) -> None:
        self.running = False

    def run(self, callback: Callable[[], None]) -> None:
        self.running = True
        self._install_signal_handlers()

        while self.running:
            started = time.time()
            try:
                callback()
            except Exception:
                if self.logger:
                    self.logger.exception("Sentinel scan loop failed")
                self._sleep_interruptibly(self.error_delay_seconds)
                continue

            elapsed = time.time() - started
            self._sleep_interruptibly(max(0.0, self.interval_seconds - elapsed))

    def _sleep_interruptibly(self, seconds: int | float) -> None:
        remaining = float(seconds)
        while self.running and remaining > 0:
            chunk = min(1.0, remaining)
            time.sleep(chunk)
            remaining -= chunk

    def _install_signal_handlers(self) -> None:
        def handle_signal(signum, frame):  # noqa: ANN001, ARG001
            if self.logger:
                self.logger.info("Received signal %s; stopping monitor loop", signum)
            self.stop()

        signal.signal(signal.SIGINT, handle_signal)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, handle_signal)
