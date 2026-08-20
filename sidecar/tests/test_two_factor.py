"""
Getting past the verification code.

The app could send a code and then reject every one of them. Three separate
things caused it, and each on its own is enough:

  - Apple's HSA2 challenge is *stateful*. `validate_2fa_code` chooses its
    verifier from state only `request_2fa_code` sets up -- for an account with
    trusted devices that is a live websocket bridge -- and with no bridge in
    hand pyicloud silently falls back to a legacy endpoint Apple no longer
    accepts. A failure to bootstrap the bridge was caught and ignored by the
    sign-in screen, after which nothing could ever verify.

  - Arming the challenge twice retires the first code. The sign-in flow asked
    for a code, then asked again the moment it saw 2FA_REQUIRED, so the message
    the user was reading was already dead by the time they read it.

  - Anything that rebuilds the session mid-challenge throws the challenge away.
    The background sync did exactly that on its timer.

And one the other way round: `validate_2fa_code` returns `not requires_2sa`, so
a *correct* code whose trust handshake had not landed yet came back as False and
was reported as the user's mistake.
"""

from __future__ import annotations

import threading

import pytest

from reminders_sidecar.icloud import (
    AuthRequired,
    ICloudClient,
    NetworkError,
    TwoFactorRequired,
)


class FakeApple:
    """
    Stands in for PyiCloudService's HSA2 surface, with its real awkwardness.

    In particular: the trusted-device route only verifies while a bridge is
    armed, Apple closes that bridge after one verdict either way, and
    `validate_2fa_code` reports "not trusted yet" and "wrong code" identically.
    """

    def __init__(
        self,
        code="123456",
        method="trusted_device",
        bridge_starts=True,
        has_phone=True,
        trust_lands_on=1,
    ):
        self._code = code
        self._bridge_starts = bridge_starts
        self._has_phone = has_phone
        self._trust_lands_on = trust_lands_on

        self.two_factor_delivery_method = method
        self.two_factor_delivery_notice = None
        self._trusted_device_bridge_state = None
        self._requires_mfa = True
        self.is_trusted_session = False

        self.arms = 0
        self.sms_sends = 0
        self.trust_calls = 0
        self.codes_seen: list[str] = []

    @property
    def requires_2fa(self) -> bool:
        return self._requires_mfa or not self.is_trusted_session

    # -- delivery -----------------------------------------------------------
    def request_2fa_code(self) -> bool:
        self.arms += 1
        if self.two_factor_delivery_method == "security_key":
            return False
        if self.two_factor_delivery_method == "sms":
            self.sms_sends += 1
            return True
        if not self._bridge_starts:
            raise RuntimeError("Trusted-device bridge bootstrap failed.")
        self._trusted_device_bridge_state = object()
        return True

    def _trusted_phone_number(self):
        return object() if self._has_phone else None

    def _request_sms_2fa_code(self, notice=None):
        self.sms_sends += 1
        self.two_factor_delivery_method = "sms"
        self.two_factor_delivery_notice = notice
        return True

    # -- verification -------------------------------------------------------
    def validate_2fa_code(self, code: str) -> bool:
        self.codes_seen.append(code)
        armed = self._trusted_device_bridge_state is not None
        # One verdict per bridge; Apple closes it either way.
        self._trusted_device_bridge_state = None
        if self.two_factor_delivery_method == "trusted_device" and not armed:
            # The legacy endpoint, which is what pyicloud reaches for with no
            # bridge in hand. Apple refuses it whatever the code says.
            return False
        if code != self._code:
            return False
        self.trust_session()
        return not self.requires_2fa

    def trust_session(self) -> bool:
        self.trust_calls += 1
        self._requires_mfa = False
        if self.trust_calls >= self._trust_lands_on:
            self.is_trusted_session = True
        return self.is_trusted_session


def client_awaiting_2fa(apple: FakeApple) -> ICloudClient:
    c = ICloudClient("a@b.c")
    c._api = apple
    c._pending_2fa = True
    return c


# ------------------------------------------------------------- delivery ---
def test_arming_the_challenge_reports_how_the_code_was_sent():
    """
    "Enter the code on your iPhone" is the wrong instruction for a code that
    arrived by text, and the user cannot type a code they are looking past.
    """
    c = client_awaiting_2fa(FakeApple(method="sms"))
    info = c.arm_2fa()
    assert info == {"sent": True, "method": "sms", "notice": None}
    assert c.status()["two_factor"]["method"] == "sms"


def test_a_bridge_that_will_not_start_falls_back_to_a_text():
    """
    The bridge is the fragile half of the flow. Ignoring its failure -- which is
    what the sign-in screen did -- leaves a challenge nothing can verify, so
    every code afterwards comes back invalid.
    """
    apple = FakeApple(bridge_starts=False)
    c = client_awaiting_2fa(apple)

    info = c.arm_2fa()

    assert info["sent"] is True
    assert info["method"] == "sms"
    assert apple.sms_sends == 1
    assert "error" not in info, "recovered, so there is nothing to report"


def test_a_bridge_failure_with_no_phone_to_fall_back_to_is_reported():
    apple = FakeApple(bridge_starts=False, has_phone=False)
    c = client_awaiting_2fa(apple)

    info = c.arm_2fa()

    assert info["sent"] is False
    assert "bridge" in info["error"].lower()


def test_a_security_key_account_is_told_what_is_wrong():
    """Not a typo, and no code will ever work -- so don't blame the typing."""
    c = client_awaiting_2fa(FakeApple(method="security_key"))
    c.arm_2fa()

    with pytest.raises(TwoFactorRequired) as e:
        c.submit_2fa("123456")
    assert "security key" in e.value.message


# --------------------------------------------------------- verification ---
def test_a_correct_code_signs_in():
    apple = FakeApple()
    c = client_awaiting_2fa(apple)
    c.arm_2fa()

    st = c.submit_2fa("123456")

    assert st["authenticated"] is True
    assert st["needs_2fa"] is False
    assert st["two_factor"] == {}


def test_a_code_typed_with_spaces_is_still_that_code():
    """Apple's own message and the Windows autofill both hand over "123 456"."""
    apple = FakeApple()
    c = client_awaiting_2fa(apple)
    c.arm_2fa()

    c.submit_2fa(" 123 456 ")

    assert apple.codes_seen == ["123456"]


def test_a_short_code_is_caught_before_it_burns_the_challenge():
    apple = FakeApple()
    c = client_awaiting_2fa(apple)
    c.arm_2fa()

    with pytest.raises(TwoFactorRequired):
        c.submit_2fa("1234")

    assert apple.codes_seen == [], "a half-typed code must not spend the bridge"
    assert c.submit_2fa("123456")["authenticated"] is True


def test_a_wrong_code_is_reported_as_wrong():
    apple = FakeApple()
    c = client_awaiting_2fa(apple)
    c.arm_2fa()

    with pytest.raises(TwoFactorRequired) as e:
        c.submit_2fa("999999")
    assert "rejected" in e.value.message
    assert apple.trust_calls == 0, "a refused code never reaches the handshake"


def test_a_correct_code_is_not_called_wrong_because_trust_lagged():
    """
    `validate_2fa_code` returns `not requires_2sa`, which stays true when the
    handshake *after* a correct code has not landed. Reported as a bad code it
    sends people round the loop typing codes that were never the problem -- and
    each loop retires the one they were holding.
    """
    apple = FakeApple(trust_lands_on=2)
    c = client_awaiting_2fa(apple)
    c.arm_2fa()

    st = c.submit_2fa("123456")

    assert st["authenticated"] is True
    assert apple.trust_calls >= 2


def test_a_dead_challenge_sends_a_fresh_code_instead_of_blaming_the_user():
    """
    Apple closes the bridge after one verdict, so a second attempt has nothing
    to verify against. Calling that "invalid code" is a dead end: there is no
    code that would have worked.
    """
    apple = FakeApple()
    c = client_awaiting_2fa(apple)
    c.arm_2fa()
    with pytest.raises(TwoFactorRequired):
        c.submit_2fa("999999")

    with pytest.raises(TwoFactorRequired) as e:
        c.submit_2fa("123456")

    assert "new one" in e.value.message
    assert e.value.data["sent"] is True
    assert apple.arms == 2, "a new challenge, and so a new code"
    # And the code from *that* challenge works.
    assert c.submit_2fa("123456")["authenticated"] is True


def test_the_challenge_is_armed_once_per_sign_in_attempt():
    """
    Arming twice is not harmless: Apple mints a new code and retires the old
    one, so the message the user is reading goes stale as they read it.
    """
    apple = FakeApple()
    c = client_awaiting_2fa(apple)
    c.arm_2fa()
    assert apple.arms == 1

    c.submit_2fa("123456")
    assert apple.arms == 1


# --------------------------------------------------- holding the challenge ---
def test_a_pending_challenge_survives_a_restore():
    """
    The background sync recovers an expired session by rebuilding it. Doing that
    while a code is outstanding hands Apple a new challenge and retires the code
    being typed -- the timer alone was enough to make a correct code fail.
    """
    apple = FakeApple()
    c = client_awaiting_2fa(apple)
    c.arm_2fa()

    assert c.awaiting_2fa is True
    assert c.restore() is False
    assert c._api is apple, "the live challenge must not be thrown away"
    assert c.submit_2fa("123456")["authenticated"] is True


def test_the_sync_retry_leaves_an_outstanding_code_alone(tmp_path):
    from reminders_sidecar.db import Cache
    from reminders_sidecar.sync import SyncEngine

    cache = Cache(tmp_path / "2fa.db")
    c = client_awaiting_2fa(FakeApple())
    c.arm_2fa()

    invalidated = []
    c.invalidate = lambda: invalidated.append(1)  # type: ignore[assignment]

    engine = SyncEngine(cache, c)
    with pytest.raises(AuthRequired):
        engine.sync_now()

    assert invalidated == []
    cache.close()


def test_a_login_and_a_restore_do_not_race_for_the_session():
    """
    The startup restore and a password typed into the gate are two threads
    reaching for the same field. Whichever finished last won, and when that was
    the restore it discarded the challenge the login had just armed.
    """
    c = ICloudClient("a@b.c")
    order: list[str] = []
    inside = threading.Event()
    release = threading.Event()

    def slow_connect(password=None, accept_terms=False):
        with c._lock:
            order.append("connect:start")
            inside.set()
            release.wait(2)
            order.append("connect:end")
            c._api = FakeApple()
            c._pending_2fa = True
            return c.status()

    c.connect = slow_connect  # type: ignore[assignment]
    t = threading.Thread(target=slow_connect, daemon=True)
    t.start()
    inside.wait(2)

    restored: list[bool] = []
    r = threading.Thread(target=lambda: restored.append(c.restore()), daemon=True)
    r.start()
    release.set()
    t.join(3)
    r.join(3)

    assert order == ["connect:start", "connect:end"]
    # The restore ran after, saw an outstanding challenge, and left it alone.
    assert restored == [False]
    assert c.awaiting_2fa is True


# --------------------------------------------------------- classification ---
def test_a_403_inside_a_record_name_is_not_an_expired_session():
    """
    The old test was `"401" in text or "403" in text`, which matches record
    names, counts and request ids. Every one of those turned a working session
    into "sign in again".
    """
    err = ICloudClient._classify(RuntimeError("record Reminder/403abc is missing"))
    assert isinstance(err, NetworkError)


def test_an_actual_401_still_is_one():
    err = ICloudClient._classify(
        RuntimeError("401 Client Error: Unauthorized for url: https://p01.icloud.com")
    )
    assert isinstance(err, AuthRequired)


def test_our_own_errors_are_not_reclassified():
    """
    A NetworkError whose detail happened to mention a 403 used to come back out
    as AUTH_REQUIRED, which is a sign-out over a bad gateway.
    """
    original = NetworkError("Could not reach iCloud", "403 from the proxy")
    assert ICloudClient._classify(original) is original
