"""Redis-backed login failure tracking and account lockout helpers."""
import redis

from .config import settings

LOCKOUT_WINDOW_SECONDS = 15 * 60
LOCKOUT_SECONDS = 30 * 60
MAX_FAILED_LOGINS = 5


def redis_client(db: int = 0):
    return redis.Redis(host=settings.redis_host, port=settings.redis_port, db=db)


def login_failure_key(username: str) -> str:
    return f"login-failures:{username.lower()}"


def login_lock_key(username: str) -> str:
    return f"login-lock:{username.lower()}"


def is_account_locked(username: str) -> int:
    ttl = redis_client().ttl(login_lock_key(username))
    return ttl if ttl and ttl > 0 else 0


def record_failed_login(username: str):
    """Increment the rolling failure counter and lock at the threshold."""
    client = redis_client()
    key = login_failure_key(username)
    failures = client.incr(key)
    if failures == 1:
        client.expire(key, LOCKOUT_WINDOW_SECONDS)
    if failures >= MAX_FAILED_LOGINS:
        client.set(login_lock_key(username), "1", ex=LOCKOUT_SECONDS)
        client.delete(key)


def clear_login_failures(username: str):
    """Clear both the rolling failure counter and any active lockout."""
    client = redis_client()
    client.delete(login_failure_key(username))
    client.delete(login_lock_key(username))
