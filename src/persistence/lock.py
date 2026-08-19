import contextlib
import os
import sys

_LOCK_IMPL: str = "auto"


def _use_fcntl():
    global _LOCK_IMPL
    if _LOCK_IMPL == "auto":
        try:
            import fcntl

            _LOCK_IMPL = "fcntl"
        except ImportError:
            _LOCK_IMPL = "fallback"
    return _LOCK_IMPL == "fcntl"


class FileLock:
    def __init__(self, path: str):
        self._path = path
        self._fd: int | None = None
        self._file = None

    def acquire(self, blocking: bool = False) -> bool:
        if self._fd is not None:
            return True

        try:
            self._file = open(self._path, "w")
            self._fd = self._file.fileno()

            if _use_fcntl():
                import fcntl

                flags = fcntl.LOCK_EX
                if not blocking:
                    flags |= fcntl.LOCK_NB
                try:
                    fcntl.flock(self._fd, flags)
                    self._file.write(str(os.getpid()))
                    self._file.flush()
                    return True
                except (IOError, BlockingIOError):
                    self._release_fd()
                    return False
            else:
                try:
                    import portalocker
                except ImportError:
                    if self._file is not None:
                        self._file.close()
                        self._file = None
                    self._fd = None
                    raise RuntimeError(
                        "No file locking available. Install 'portalocker' or use a platform with fcntl."
                    )
                portalocker.lock(self._file, portalocker.LOCK_EX)
                self._file.write(str(os.getpid()))
                self._file.flush()
                return True
        except (IOError, OSError) as e:
            self._release_fd()
            raise RuntimeError(f"Failed to acquire file lock at {self._path}: {e}")

    def release(self):
        self._release_fd()

    def _release_fd(self):
        if self._fd is not None and _use_fcntl():
            try:
                import fcntl

                fcntl.flock(self._fd, fcntl.LOCK_UN)
            except Exception:
                pass
        if self._file is not None:
            try:
                self._file.close()
            except Exception:
                pass
        self._fd = None
        self._file = None

    @property
    def is_locked(self) -> bool:
        return self._fd is not None

    @contextlib.contextmanager
    def locked(self, blocking: bool = False):
        acquired = self.acquire(blocking=blocking)
        try:
            yield acquired
        finally:
            if acquired:
                self.release()

    def __enter__(self):
        self.acquire(blocking=False)
        return self

    def __exit__(self, *args):
        self.release()

    def __del__(self):
        self._release_fd()
