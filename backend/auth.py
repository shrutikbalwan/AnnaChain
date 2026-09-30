"""AnnaChain — who is allowed to see the operations dashboard.

Three levels of access, and the distinction matters:

  * **Public** — the buyer's trace page. Deliberately open. A record a buyer
    has to ask permission to see is not traceability.
  * **Device** — the node's own ingest path. Authenticated by the signature on
    every record, not by a password. A node has no password to lose.
  * **Operator** — everything else: the live dashboard, alerts, the fleet.
    That is commercial information about who is shipping what, and it needs a
    login.

Passwords are stored as PBKDF2-HMAC-SHA256 with a per-user salt. Not bcrypt or
argon2 only because those are dependencies, and PBKDF2 at 200k iterations is in
the standard library and is honest. Sessions are random tokens with an expiry,
kept in the database so a restart does not silently log everyone out.
"""
import hashlib, hmac, os, secrets, time

ITERATIONS = 200_000
SESSION_HOURS = 12


def hash_password(password: str, salt: bytes = None) -> tuple[str, str]:
    salt = salt or os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return salt.hex(), dk.hex()


def check_password(password: str, salt_hex: str, hash_hex: str) -> bool:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(),
                             bytes.fromhex(salt_hex), ITERATIONS)
    # Constant-time: a timing difference here leaks the password one byte at a time.
    return hmac.compare_digest(dk.hex(), hash_hex)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def expiry() -> float:
    return time.time() + SESSION_HOURS * 3600
