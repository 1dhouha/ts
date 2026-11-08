"""Public holder has only immutable allowlisted DTOs; no owner/core reference."""
from dataclasses import dataclass, field
from threading import RLock

from city_core.transitions import freeze, plain
from .records import LiveError, integer


@dataclass(slots=True)
class PublicStore:
    lock: object = field(default_factory=RLock, repr=False)
    run_id: str = ''
    items: tuple = ()
    next_seq: int = 1
    limit: int = 512

    def reset(self, run_id):
        with self.lock:
            self.run_id, self.items, self.next_seq = run_id, (), 1

    def append(self, reading):
        with self.lock:
            record = freeze({**reading, 'run_id': self.run_id, 'seq': self.next_seq})
            self.items = (*self.items, record)[-self.limit:]
            self.next_seq += 1
            return record


@dataclass(frozen=True, slots=True)
class PublicReader:
    _store: PublicStore = field(repr=False)

    def observations(self):
        with self._store.lock:
            return plain(self._store.items)

    def capture_reported(self, cursor=None):
        """Atomic public bootstrap/page; holds only the allowlisted report store."""
        with self._store.lock:
            if cursor is None:
                records = plain(self._store.items)
                page = {'run_id': self._store.run_id, 'records': records,
                        'next_after_seq': self._store.next_seq - 1, 'at_end': True}
            else:
                from .records import exact_keys, identifier
                exact_keys(cursor, ('run_id', 'after_seq'), ('limit',))
                identifier(cursor['run_id'])
                page = self.events_since(cursor['run_id'], cursor['after_seq'], cursor.get('limit', 100))
            return {**page, 'frame_seq': self._store.next_seq - 1,
                    'first_retained_seq': self._store.items[0]['seq'] if self._store.items else self._store.next_seq}

    def events_since(self, run_id, after_seq, limit=100):
        integer(after_seq, 0, 100000000)
        integer(limit, 1, 1000)
        with self._store.lock:
            if run_id != self._store.run_id:
                raise LiveError('OLD_RUN')
            first = self._store.items[0]['seq'] if self._store.items else self._store.next_seq
            if after_seq < first - 1:
                raise LiveError('HISTORY_GAP')
            if after_seq >= self._store.next_seq:
                raise LiveError('CURSOR_INVALID')
            records = tuple(item for item in self._store.items if item['seq'] > after_seq)[:limit]
            cursor = records[-1]['seq'] if records else after_seq
            return {'schema_version': 'twin-observations-0.1-local', 'run_id': run_id,
                    'records': plain(records), 'next_after_seq': cursor,
                    'at_end': cursor == self._store.next_seq - 1}
