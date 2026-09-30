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


# ── backing off repeated failures ─────────────────────────────────────────
#
# PBKDF2 makes each guess cost the server ~0.1 s, which is not a limit. So
# failures are counted per username (case-folded) and per source address, and
# after FREE_ATTEMPTS in a row the pair is locked for BASE_DELAY_S, doubling
# with every further failure up to MAX_DELAY_S. While locked, even the right
# password is refused: otherwise the lock only slows a guesser down between
# guesses it still gets to make. A success clears both counters.
#
# Per username stops a guesser spreading one account across many addresses;
# per source stops one address spraying many usernames. The price is that
# someone can lock the real operator out for up to MAX_DELAY_S by failing on
# purpose, which is the usual trade and is written down in the README.
#
# State is persisted to the login_throttle table (see db.py) on server shutdown
# and reloaded on startup, so a restart does not clear active lockouts.
FREE_ATTEMPTS = 5
BASE_DELAY_S = 30
MAX_DELAY_S = 15 * 60


class Throttle:
    def __init__(self, clock=time.time, db=None):
        self.clock = clock
        self.db = db        # injected by app.py; None in unit tests (no side effects)
        self.state = {}     # key -> [consecutive failures, locked until]

    @staticmethod
    def _keys(username, source):
        return (("user", (username or "").strip().casefold()), ("src", source or "?"))

    def retry_after(self, username, source) -> float:
        now = self.clock()
        return max([0.0] + [self.state[k][1] - now
                            for k in self._keys(username, source) if k in self.state])

    def failure(self, username, source) -> float:
        """Count a failure. Returns the lock now in force, in seconds (0 if none)."""
        now, delay = self.clock(), 0.0
        for k in self._keys(username, source):
            st = self.state.setdefault(k, [0, 0.0])
            st[0] += 1
            if st[0] >= FREE_ATTEMPTS:
                d = min(BASE_DELAY_S * 2 ** (st[0] - FREE_ATTEMPTS), MAX_DELAY_S)
                st[1] = max(st[1], now + d)
            delay = max(delay, st[1] - now)
        return max(0.0, delay)

    def success(self, username, source):
        for k in self._keys(username, source):
            self.state.pop(k, None)

    # ── persistence ───────────────────────────────────────────────────────

    def load(self):
        """Restore throttle state from the database on server startup.

        Entries whose lockout window has already passed are dropped silently —
        they accumulate over time otherwise. A restart after a lockout expires
        behaves as if the lockout never happened, which is correct.
        """
        if self.db is None:
            return
        now = self.clock()
        try:
            for row in self.db.conn().execute(
                    "SELECT key_type, key_value, failures, locked_until "
                    "FROM login_throttle WHERE locked_until > ?", (now,)):
                k = (row["key_type"], row["key_value"])
                self.state[k] = [row["failures"], row["locked_until"]]
        except Exception:
            pass   # table may not exist on first run; db.init() creates it

    def save(self):
        """Persist active lockouts to the database on server shutdown.

        Called by the lifespan context manager in app.py. Only entries still
        within their lockout window are written; expired ones are discarded.
        """
        if self.db is None:
            return
        now = self.clock()
        c = self.db.conn()
        try:
            c.execute("DELETE FROM login_throttle")
            for (ktype, kval), (failures, locked_until) in self.state.items():
                if locked_until > now:
                    c.execute(
                        "INSERT INTO login_throttle"
                        "(key_type, key_value, failures, locked_until)"
                        " VALUES(?,?,?,?)",
                        (ktype, kval, failures, locked_until))
            c.commit()
        except Exception:
            pass
