# SRP reference vectors

The sign-in path in `sidecar/src/auth.rs` reimplements Apple's web SRP
handshake in Rust. The implementation it replaced delegated that handshake to
[pyicloud], which in turn uses the [srp] package. pyicloud is the only version
of this flow known to work against Apple in production, so it — not this
codebase — is the reference.

The vectors in `auth.rs`'s test module are generated from that reference rather
than from our own output. A test that asserts an implementation's own result
proves only that it is deterministic; it passes just as happily when the
construction is wrong.

## What the reference pins down

Running pyicloud's stack confirmed four things the Rust code gets right, each
of which would silently break sign-in if changed:

- `srp.no_username_in_x()` — `x = H(salt || H(":" || derived))`, with the
  username deliberately excluded.
- `srp.rfc5054_enable()` — `k` and `u` hash `N`, `g`, `A` and `B` left-padded
  to the 256-byte modulus width.
- M1 *does* include `H(username)` even though `x` does not. The asymmetry is
  correct, not a transcription error.
- `A`, `B`, `S` are hashed as minimal big-endian integers (leading zero bytes
  stripped) when computing M1, M2 and the session key `K` — matching
  `BigUint::to_bytes_be`.

## Known divergence: salts beginning with `0x00`

pyicloud's SRP backend converts the salt to an OpenSSL BIGNUM and back in both
`gen_x` and `calculate_M`, which strips leading zero bytes. This code hashes
the salt verbatim.

For any salt whose first byte is non-zero the two implementations agree
exactly. For a salt beginning with `0x00` — roughly one account in 256 — they
compute different `x` and different M1.

This is unresolved and **cannot be settled from the code alone**: it depends on
what Apple's server expects, and both readings are plausible (pyicloud may
simply be quietly broken for those accounts). Since Apple fixes the salt when
the password is set, an affected account fails sign-in *every* time rather than
intermittently, which makes it easy to misread as a wrong password.

Settle it during release testing with a disposable account, ideally by
resetting its password until `signin/init` returns a salt starting with `0x00`.
Until then, treat "correct password rejected consistently" as a candidate
symptom.

## Regenerating the vectors

The chosen salt starts with `0x01` on purpose, so the vectors exercise the
common path both implementations agree on.

```bash
pip install srp
pip download pyicloud --no-deps --no-binary :all: -d /tmp/pyi
tar xzf /tmp/pyi/pyicloud-*.tar.gz -C /tmp/pyi
```

```python
import base64, hashlib, importlib.util, srp

spec = importlib.util.spec_from_file_location(
    "sp", "/tmp/pyi/pyicloud-2.6.5/pyicloud/srp_password.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

srp.rfc5054_enable()      # exactly what pyicloud.base does
srp.no_username_in_x()

user = "vector@example.com"
password = "correct horse battery staple"
salt = bytes(range(1, 17))                              # no leading zero byte
B = hashlib.sha256(b"server-public-seed").digest() * 8  # deterministic server public

print("salt", base64.b64encode(salt).decode())
print("B   ", base64.b64encode(B).decode())

for proto in (m.SrpProtocolType.S2K, m.SrpProtocolType.S2K_FO):
    sp = m.SrpPassword(password)
    usr = srp.User(user, sp, hash_alg=srp.SHA256, ng_type=srp.NG_2048,
                   bytes_a=bytes(range(1, 33)))
    usr.start_authentication()
    sp.set_encrypt_info(salt, 1000, 32, proto)
    m1 = usr.process_challenge(salt, B)
    print(proto.value, "derived", sp.encode().hex())
    print(proto.value, "m1     ", m1.hex())
    print(proto.value, "m2     ", usr.H_AMK.hex())
```

`bytes_a` fixes the client private exponent so the handshake is reproducible;
`auth.rs` passes the same value into `make_proof`.

## Still not covered

These vectors validate the cryptography. They say nothing about the surrounding
flow — cookie and header capture across `signin/init` and `signin/complete`,
2FA and trust-token handling, or `accountLogin` session restoration. Those need
a disposable Apple account, and remain the largest untested surface in the
sidecar.

[pyicloud]: https://github.com/timlaing/pyicloud
[srp]: https://pypi.org/project/srp/
