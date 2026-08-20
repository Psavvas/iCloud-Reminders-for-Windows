"""
Staying signed in.

The app used to ask for a password on every launch. Two things caused it: the
sidecar never attempted to restore a session at startup, so it reported "not
authenticated" from a cold start regardless of what was on disk; and nothing
ever saved the credential, so there was nothing to restore *from* once Apple
expired the session token.
"""

from __future__ import annotations

import threading

import pytest

from reminders_sidecar.icloud import AuthRequired, ICloudClient, NetworkError
from reminders_sidecar.server import Server
from reminders_sidecar.sync import SyncEngine
from tests.test_sync_and_server import FakeClient


class FakeKeyringClient(ICloudClient):
    """An ICloudClient whose keyring and network are both in memory."""

    def __init__(self, apple_id="a@b.c", password_accepted=True, offline=False):
        super().__init__(apple_id)
        self.saved: dict[str, str] = {}
        self.connects: list[str | None] = []
        self.password_accepted = password_accepted
        # Apple unreachable, as opposed to Apple saying no. The app used to
        # treat the two the same, which is how a Wi-Fi blip cost a password.
        self.offline = offline

    def remember_password(self, password):
        self.saved[self.apple_id] = password
        return True

    def forget_password(self):
        self.saved.pop(self.apple_id, None)

    @property
    def has_saved_password(self):
        return self.apple_id in self.saved

    def connect(self, password=None, accept_terms=False):
        self.connects.append(password)
        if self.offline:
            raise NetworkError("Could not reach iCloud", "name resolution failed")
        effective = password or self.saved.get(self.apple_id)
        if not effective or not self.password_accepted:
            raise AuthRequired("iCloud rejected the sign-in")
        self._api = object()
        self._pending_2fa = False
        return self.status()


@pytest.fixture
def server(tmp_path):
    s = Server(db_path=str(tmp_path / "s.db"), apple_id="a@b.c", cookie_dir=None)
    s.client = FakeKeyringClient()
    s.sync = SyncEngine(s.cache, s.client, emit=s.emit)
    s._methods = s._build_methods()
    s.events: list[tuple] = []
    s.emit = lambda e, d: s.events.append((e, d))  # type: ignore[assignment]
    s._kick_sync = lambda full=False: None  # type: ignore[assignment]
    s._kick_push = lambda: None  # type: ignore[assignment]
    yield s
    s.cache.close()


def call(server, method, **params):
    return server._methods[method](params)


# ---------------------------------------------------------------- storing ---
def test_signing_in_saves_the_password(server):
    call(server, "login", apple_id="a@b.c", password="hunter2")
    assert server.client.saved == {"a@b.c": "hunter2"}


def test_the_password_is_kept_even_when_login_stops_at_2fa(server):
    """
    2FA means the password was accepted; it is the *second* factor that is
    outstanding. Discarding it here would mean nothing to restore from later.
    """
    from reminders_sidecar.icloud import TwoFactorRequired

    def needs_2fa(password=None, accept_terms=False):
        raise TwoFactorRequired("Two-factor authentication required")

    server.client.connect = needs_2fa  # type: ignore[assignment]
    with pytest.raises(TwoFactorRequired):
        call(server, "login", apple_id="a@b.c", password="hunter2")
    assert server.client.saved == {"a@b.c": "hunter2"}


def test_a_rejected_password_is_not_saved(server):
    server.client.password_accepted = False
    with pytest.raises(AuthRequired):
        call(server, "login", apple_id="a@b.c", password="wrong")
    assert server.client.saved == {}


def test_turning_the_setting_off_stops_saving(server):
    call(server, "set_settings", remember_password=False)
    call(server, "login", apple_id="a@b.c", password="hunter2")
    assert server.client.saved == {}


def test_signing_out_clears_the_saved_password(server):
    call(server, "login", apple_id="a@b.c", password="hunter2")
    # sign_out swaps in a fresh client, so hold on to the one that did the work.
    old = server.client
    call(server, "sign_out")
    assert old.saved == {}
    assert server.client is not old


# --------------------------------------------------------------- restoring ---
def test_a_cold_start_restores_the_session_without_a_password(server):
    server.client.saved["a@b.c"] = "hunter2"
    server.client._api = None

    server._restore_session()
    _join_threads()

    assert server.client.connected
    # Restored from storage: no password was passed in.
    assert server.client.connects == [None]
    assert any(e == "auth_changed" for e, _ in server.events)


def test_auth_status_says_restoring_before_the_attempt_finishes(server):
    """
    The UI reads auth_status the instant it sees `ready`. If that races the
    restore thread and reports a plain "not authenticated", the sign-in form
    flashes up at someone who is already signed in -- which is exactly what the
    app looked like it was doing.
    """
    server.client.saved["a@b.c"] = "hunter2"
    server.client._api = None

    gate = threading.Event()
    original = server.client.connect

    def slow(password=None, accept_terms=False):
        gate.wait(2)
        return original(password=password, accept_terms=accept_terms)

    server.client.connect = slow  # type: ignore[assignment]
    server._restore_session()
    try:
        st = call(server, "auth_status")
        assert st["authenticated"] is False
        assert st["restoring"] is True
        assert st["can_restore"] is True
    finally:
        gate.set()
        _join_threads()


def test_nothing_to_restore_from_reports_so_immediately(server):
    server.client._api = None
    server._restore_session()
    _join_threads()

    st = call(server, "auth_status")
    assert st["authenticated"] is False
    assert st["restoring"] is False
    assert st["can_restore"] is False


# ------------------------------------------------------- retry during sync ---
def test_an_expired_session_is_rebuilt_mid_sync_instead_of_surfacing(tmp_path):
    """
    Apple expires session tokens on its own schedule. Every background pass
    after that used to raise AUTH_REQUIRED straight through to the UI, which is
    what "signed out all the time" actually was.
    """
    from reminders_sidecar.db import Cache

    cache = Cache(tmp_path / "r.db")
    client = FakeClient()
    client.saved = {"a@b.c": "hunter2"}
    calls = {"n": 0}
    restored = {"n": 0}

    real_lists = client.lists

    def expiring_lists():
        calls["n"] += 1
        if calls["n"] == 1:
            raise AuthRequired("Your iCloud session expired")
        return real_lists()

    def restore():
        restored["n"] += 1
        return True

    client.lists = expiring_lists  # type: ignore[assignment]
    client.restore = restore  # type: ignore[assignment]

    engine = SyncEngine(cache, client)
    engine.sync_now(full=True)

    assert restored["n"] == 1
    assert calls["n"] == 2  # failed, restored, succeeded
    assert cache.lists()
    cache.close()


def test_a_second_failure_is_reported_rather_than_looping(tmp_path):
    from reminders_sidecar.db import Cache

    cache = Cache(tmp_path / "r2.db")
    client = FakeClient()
    client.lists = _always_expired  # type: ignore[assignment]
    client.restore = lambda: True  # type: ignore[assignment]

    engine = SyncEngine(cache, client)
    with pytest.raises(AuthRequired):
        engine.sync_now(full=True)
    cache.close()


def test_a_failed_restore_does_not_swallow_the_error(tmp_path):
    from reminders_sidecar.db import Cache

    cache = Cache(tmp_path / "r3.db")
    client = FakeClient()
    client.lists = _always_expired  # type: ignore[assignment]
    client.restore = lambda: False  # type: ignore[assignment]

    engine = SyncEngine(cache, client)
    with pytest.raises(AuthRequired):
        engine.sync_now(full=True)
    cache.close()


def _always_expired():
    raise AuthRequired("Your iCloud session expired")


def _join_threads():
    for t in threading.enumerate():
        if t.name in ("restore", "sync", "push") and t is not threading.current_thread():
            t.join(timeout=3)


# ------------------------------------------------------- expiry, unstubbed ---
#
# The tests above stub client.restore, which is what let the bug below live:
# the recovery path was only ever exercised against a fake that always said it
# worked. These use the real method.


def test_restore_rebuilds_a_session_that_apple_has_expired():
    """
    CloudKit answering 401 leaves the PyiCloudService object in place -- it has
    no idea its token died -- so the client still looks connected. restore()
    treats a connected client as nothing to do, so it returned True without
    reconnecting and the caller retried on the same dead session.
    """
    client = FakeKeyringClient()
    client.saved = {"a@b.c": "hunter2"}
    client._api = object()  # a live-looking, actually-expired session
    client._pending_2fa = False

    assert client.connected  # this is what fooled restore()

    client.invalidate()
    assert not client.connected
    assert client.restore() is True
    assert client.connects == [None], "restore must reconnect from the keyring"


def test_an_expired_session_does_not_keep_reporting_itself_as_signed_in():
    """
    The status the UI reads has to agree with reality, or the app shows an
    account that cannot sync and never says why.
    """
    client = FakeKeyringClient()
    client._api = object()
    assert client.status()["authenticated"] is True
    client.invalidate()
    assert client.status()["authenticated"] is False


def test_the_sync_retry_drops_the_dead_session_before_restoring(tmp_path):
    """The whole point of the retry: the second attempt must be a new session."""
    from reminders_sidecar.db import Cache

    cache = Cache(tmp_path / "r4.db")
    client = FakeKeyringClient()
    client.saved = {"a@b.c": "hunter2"}
    client._api = object()

    order: list[str] = []
    calls = {"n": 0}

    def expiring_lists():
        calls["n"] += 1
        order.append(f"lists:{calls['n']}")
        if calls["n"] == 1:
            raise AuthRequired("Your iCloud session expired")
        return [{"id": "List/A", "title": "Inbox", "color_hex": None, "count": 0}]

    client.lists = expiring_lists  # type: ignore[assignment]
    client.reminders_for = lambda list_id: ([], {})  # type: ignore[assignment]
    client.sync_cursor = lambda: "c0"  # type: ignore[assignment]

    real_invalidate = client.invalidate

    def traced_invalidate():
        order.append("invalidate")
        real_invalidate()

    client.invalidate = traced_invalidate  # type: ignore[assignment]

    engine = SyncEngine(cache, client)
    engine.sync_now(full=True)

    assert order == ["lists:1", "invalidate", "lists:2"], order
    assert client.connects == [None], "the retry must run on a rebuilt session"
    cache.close()


# ------------------------------------------------- unreachable, not expired ---
#
# The other half of "it signs me out all the time". Everything above assumes a
# failure means the session is gone. Most of them don't: the laptop woke before
# its Wi-Fi did, the VPN was mid-handshake, Apple was briefly unwell. Answering
# those with the sign-in form -- or worse, the sticky notice that only a sign-in
# clears -- turns a ten-second blip into a password prompt.


def test_a_network_failure_is_not_a_lost_session():
    client = FakeKeyringClient(offline=True)
    client.saved["a@b.c"] = "hunter2"

    assert client.restore() is False
    assert isinstance(client.last_restore_error, NetworkError)
    assert client.restore_is_retryable is True


def test_a_rejected_credential_is_not_retried_forever():
    """Apple saying no is not a blip; only the user can fix it."""
    client = FakeKeyringClient(password_accepted=False)
    client.saved["a@b.c"] = "stale"

    assert client.restore() is False
    assert isinstance(client.last_restore_error, AuthRequired)
    assert client.restore_is_retryable is False


def test_the_startup_restore_retries_a_network_failure_before_giving_up(server):
    """
    Launching before the network is up is the most common way this fails. One
    attempt and then a password form is what the app looked like it was doing.
    """
    server.client.saved["a@b.c"] = "hunter2"
    server.client.offline = True
    server.RESTORE_BACKOFF_SECONDS = (0.01, 0.01, 0.01)

    original = server.client.connect

    def connect(password=None, accept_terms=False):
        # Third attempt: the network is there now.
        if len(server.client.connects) >= 2:
            server.client.offline = False
        return original(password=password, accept_terms=accept_terms)

    server.client.connect = connect  # type: ignore[assignment]
    server._restore_session()
    _join_threads()

    assert server.client.connected, server.client.connects
    assert len(server.client.connects) > 1, "one attempt is not a retry"


def test_the_ui_is_told_to_keep_waiting_while_a_retry_is_pending(server):
    """
    `restoring` is what holds the gate on "signing you back in". Dropping it
    between attempts flashes the password form at someone whose session is fine.
    """
    server.client.saved["a@b.c"] = "hunter2"
    server.client.offline = True
    server.RESTORE_BACKOFF_SECONDS = (0.5,)

    server._restore_session()
    threading.Event().wait(0.15)
    st = call(server, "auth_status")
    assert st["restoring"] is True
    server._stop.set()
    _join_threads()
    server._stop.clear()


def test_an_unreachable_icloud_does_not_report_itself_as_a_sign_out(tmp_path):
    """
    A restore that failed on the network used to surface as AUTH_REQUIRED, which
    raises the sticky "iCloud sign-in needed" notice -- and the only thing that
    clears that notice is signing in.
    """
    from reminders_sidecar.db import Cache

    cache = Cache(tmp_path / "n1.db")
    client = FakeKeyringClient(offline=True)
    client.saved["a@b.c"] = "hunter2"
    client._api = object()
    client.lists = _always_expired  # type: ignore[assignment]

    events: list[tuple] = []
    engine = SyncEngine(cache, client, emit=lambda e, d: events.append((e, d)))

    with pytest.raises(NetworkError):
        engine.sync_now(full=True)

    assert not [e for e, _ in events if e == "auth_changed"], (
        "nothing here established that the session is gone"
    )
    cache.close()


def test_a_session_apple_really_ended_still_reaches_the_user(tmp_path):
    """The counterweight: a genuine expiry must not be swallowed as a blip."""
    from reminders_sidecar.db import Cache

    cache = Cache(tmp_path / "n2.db")
    client = FakeKeyringClient(password_accepted=False)
    client.saved["a@b.c"] = "stale"
    client._api = object()
    client.lists = _always_expired  # type: ignore[assignment]

    events: list[tuple] = []
    engine = SyncEngine(cache, client, emit=lambda e, d: events.append((e, d)))

    with pytest.raises(AuthRequired):
        engine.sync_now(full=True)

    assert [e for e, _ in events if e == "auth_changed"]
    cache.close()
