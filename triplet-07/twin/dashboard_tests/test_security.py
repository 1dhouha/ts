import unittest
from unittest.mock import patch

from city_live.records import LiveError


class SecurityTests(unittest.TestCase):
    def test_login_throttles_and_sessions_expire_without_echoing_secret(self):
        from city_dashboard.security import Sessions
        with patch('city_dashboard.security.time.monotonic', return_value=10) as clock:
            sessions = Sessions('one-launch-long-passphrase!')
            token = sessions.login('one-launch-long-passphrase!')
            self.assertNotIn('passphrase', token)
            self.assertEqual(sessions.require('city_session=' + token), token)
            for bad in ('wrong', None, 'x' * 257, {}):
                with self.assertRaisesRegex(LiveError, 'NOT_AUTHORIZED'):
                    sessions.login(bad)
            with self.assertRaisesRegex(LiveError, 'RATE_LIMITED'):
                sessions.login('one-launch-long-passphrase!')
            clock.return_value = 3620
            with self.assertRaisesRegex(LiveError, 'NOT_AUTHORIZED'):
                sessions.require('city_session=' + token)
            fresh = sessions.login('one-launch-long-passphrase!')
            self.assertNotEqual(fresh, token)
            sessions.logout('city_session=' + fresh)
            with self.assertRaisesRegex(LiveError, 'NOT_AUTHORIZED'):
                sessions.require('city_session=' + fresh)

    def test_session_limit_and_malformed_cookies(self):
        from city_dashboard.security import Sessions
        with patch('city_dashboard.security.time.monotonic') as clock:
            sessions = Sessions('one-launch-long-passphrase!')
            for i in range(16):
                clock.return_value = i * 11
                sessions.login('one-launch-long-passphrase!')
            clock.return_value = 16 * 11
            with self.assertRaisesRegex(LiveError, 'SESSION_LIMIT'):
                sessions.login('one-launch-long-passphrase!')
            for cookie in ('', 'bad', 'city_session=unknown', 'city_session="unterminated'):
                with self.assertRaisesRegex(LiveError, 'NOT_AUTHORIZED'):
                    sessions.require(cookie)
        for secret in ('short', None, 'x' * 257):
            with self.assertRaises(ValueError): Sessions(secret)

    def test_global_rate_limit_and_recovery(self):
        from city_dashboard.security import RateLimit
        with patch('city_dashboard.security.time.monotonic', return_value=0) as clock:
            limit = RateLimit()
            self.assertTrue(all(limit.allow() for _ in range(40)))
            self.assertFalse(limit.allow())
            clock.return_value = 1.1
            self.assertTrue(limit.allow())
