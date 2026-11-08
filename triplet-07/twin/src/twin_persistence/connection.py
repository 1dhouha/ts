from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
from threading import RLock


class SQLiteConnectionManager:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_file = None
        self._lock = RLock()
        self._acquire_owner_lock()
        try:
            self.connection = sqlite3.connect(
                self.path, timeout=30, isolation_level=None, check_same_thread=False,
            )
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA synchronous=FULL")
            self.connection.execute("PRAGMA foreign_keys=ON")
            self.connection.execute("PRAGMA busy_timeout=30000")
        except BaseException:
            self.close()
            raise

    def _acquire_owner_lock(self):
        lock_path = self.path.with_suffix(self.path.suffix + ".owner")
        self._lock_file = open(lock_path, "a+b")
        self._lock_file.seek(0, os.SEEK_END)
        if self._lock_file.tell() == 0:
            self._lock_file.write(b"\0")
            self._lock_file.flush()
        self._lock_file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self._lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self._lock_file.close()
            self._lock_file = None
            raise RuntimeError("PERSISTENCE_IN_USE") from error

    @contextmanager
    def transaction(self):
        with self._lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                yield self.connection
                self.connection.execute("COMMIT")
            except BaseException:
                if self.connection.in_transaction:
                    self.connection.execute("ROLLBACK")
                raise

    def close(self):
        connection = getattr(self, "connection", None)
        if connection is not None:
            connection.close()
            self.connection = None
        lock_file = self._lock_file
        if lock_file is not None:
            try:
                lock_file.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            finally:
                lock_file.close()
                self._lock_file = None
