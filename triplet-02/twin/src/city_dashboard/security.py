"""Ephemeral local sessions and bounded request admission, never persisted."""
from collections import deque
import hashlib
import hmac
from http.cookies import SimpleCookie, CookieError
import secrets
from threading import RLock
import time

from city_live.records import LiveError


class Sessions:
    def __init__(self, secret):
        if type(secret) is not str or not 16 <= len(secret) <= 256:
            raise ValueError('Use a 16–256 character launch passphrase')
        self._digest = hashlib.sha256(secret.encode('utf-8')).digest()
        self._sessions = {}
        self._attempts = deque(maxlen=5)
        self._lock = RLock()

    def login(self, secret):
        with self._lock:
            now = time.monotonic()
            if len(self._attempts) == 5 and now - self._attempts[0] < 10:
                raise LiveError('RATE_LIMITED')
            self._attempts.append(now)
            if type(secret) is not str or len(secret) > 256 or not hmac.compare_digest(
                    self._digest, hashlib.sha256(secret.encode('utf-8')).digest()):
                raise LiveError('NOT_AUTHORIZED')
            self._sessions = {key: expiry for key, expiry in self._sessions.items() if expiry > now}
            if len(self._sessions) >= 16:
                raise LiveError('SESSION_LIMIT')
            token = secrets.token_urlsafe(32)
            self._sessions = {**self._sessions, token: now + 3600}
            return token

    def _token(self, cookie):
        try:
            parsed = SimpleCookie()
            parsed.load(cookie or '')
            return parsed['city_session'].value if 'city_session' in parsed else ''
        except CookieError:
            return ''

    def require(self, cookie):
        with self._lock:
            token = self._token(cookie)
            if self._sessions.get(token, 0) <= time.monotonic():
                raise LiveError('NOT_AUTHORIZED')
            return token

    def logout(self, cookie):
        with self._lock:
            token = self.require(cookie)
            self._sessions = {key: expiry for key, expiry in self._sessions.items() if key != token}


class RateLimit:
    def __init__(self):
        self._times = deque(maxlen=40)
        self._lock = RLock()

    def allow(self):
        with self._lock:
            now = time.monotonic()
            if len(self._times) == 40 and now - self._times[0] < 1:
                return False
            self._times.append(now)
            return True
