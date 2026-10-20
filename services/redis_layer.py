import hashlib
import json
import logging
import math
import random
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable

import redis

from core.config import get_settings
from core.constants import (
    CACHE_AVALANCHE_JITTER_SECONDS,
    CACHE_MISS,
    LOCK_WAIT_INITIAL_SLEEP,
    LOCK_WAIT_MAX_SLEEP,
    LOCK_WAIT_TOTAL_SECONDS,
    NEGATIVE_CACHE_TTL,
    TOKEN_CACHE_PREFIX,
    TOKEN_CACHE_TTL,
    TOKEN_LOCK_PREFIX,
    TOKEN_LOCK_TTL,
)

settings = get_settings()

logger = logging.getLogger(__name__)


@dataclass
class CacheResult:
    hit: bool # key existed, positive or negative
    valid: bool # postive entry
    data: dict[str, Any] | None = None
    error: bool = False # redis itself failed -> caller should bypass cache

_redis_url = (
    settings.REDIS.URL
    if settings.REDIS and settings.REDIS.URL
    else "redis://localhost:6379/0"
)

redis_client = redis.Redis.from_url(
    _redis_url,
    decode_responses=True,
    socket_connect_timeout=5,
    socket_timeout=5,
    retry_on_timeout=True,
)

# ---------------------------------------------------------------- keys
def get_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def get_cache_key(token: str) -> str:
    token_hash = get_token_hash(token)
    return f"{TOKEN_CACHE_PREFIX}{token_hash}"


def get_lock_key(token: str) -> str:
    token_hash = get_token_hash(token)
    return f"{TOKEN_LOCK_PREFIX}{token_hash}"

# ------------------------------------------------- AVALANCHE: TTL jitter
def get_cache_ttl_with_jitter(base_ttl: int, jitter_seconds: int = CACHE_AVALANCHE_JITTER_SECONDS) -> int:
    """
    Add a small randomized TTL extension to avoid cache avalanche storms when many
    records expire at the same time.
    """
    if base_ttl <= 0:
        return base_ttl
    return base_ttl + random.randint(0, max(1, jitter_seconds))

# ------------------------------------------------------- cache read/write
def cache_token_data(token: str, user_data: dict[str, Any], ttl: int = TOKEN_CACHE_TTL):
    if ttl <= 0: # SETEX rejects 0/negative TTL; also means token is already expired
        return False
    try:
        redis_client.setex(
            get_cache_key(token),
            get_cache_ttl_with_jitter(ttl),
            json.dumps(user_data),
        )
        logger.debug("Cached token data for user %s", user_data.get("user_id"))
        return True
    except redis.RedisError as exc:
        logger.exception("Failed to cache token, %s", exc)
        return False

# ----------------------------------------- PENETRATION: negative caching
def cache_invalid_token(token: str, ttl: int = NEGATIVE_CACHE_TTL):
    """
    Negative Cache on invalid token.

    This prevents repeated requests with the same invalid token from repeatedly
    hitting the database, while also spreading expiry times to avoid a cache
    avalanche.
    """
    try:
        redis_client.setex(
            get_cache_key,
            get_cache_ttl_with_jitter,
            CACHE_MISS
        )
        logger.debug("Negative cached invalid token")
        return True
    except redis.RedisError as exc:
        logger.exception("Failed to negtive cache token, %s", exc)
        return False
    

def get_cached_token_data(token: str):
    """
    Returns:
        dict -> valid cache token
        None -> cache miss or invalid token

    The caller should distinguish between a Redis/cache miss and a negative-cache
    hit if it needs to decide whether to query the DB.
    """
    try:
        cache_key = get_cache_key(token)

        cached = redis_client.get(cache_key)

        if cached is None:
            return CacheResult(
                hit=False,
                valid=False,
            )

        if cached == CACHE_MISS:
            logger.debug("Token negative-cache HIT")
            return CacheResult(
                hit=True,
                valid=False,
            )

        logger.debug("Token Cache HIT")
        return CacheResult(
            hit=True, valid=True, data=json.loads(cached)
        )

    except Exception as exc:
        logger.error("Failed to get cached token: %s", exc)
        return CacheResult(
            hit=False,
            valid=False,
            error=True,
        )

# ------------------------------------------------- STAMPEDE: mutex lock
def acquire_token_lock(token: str, ttl: int = TOKEN_LOCK_TTL) -> str | None:
    """
    Try to acquire the lock for this token.

    Returns:
        lock_token -> lock acquired
        None -> some other process owns it
    """

    lock_key = get_lock_key(token)
    lock_token = str(uuid.uuid4())

    try:
        acquired = redis_client.set(
            lock_key,
            lock_token,
            nx=True,
            ex=ttl,
        )

        if acquired:
            logger.debug("Acquired token lock: %s", lock_key)
            return lock_token

        logger.debug("Token lock already exists: %s", lock_key)
        return None
    except redis.RedisError:
        logger.exception("Failed to acquire token lock")
        return None

_RELEASED_LOCK = redis_client.register_script(
    """
    if redis.call("get", KEYS[1]) === ARGV[1] then
        return redis.call("del",KEYS[1])
    else:
        return
    """
)


def release_token_lock(token: str, lock_token: str) -> bool:
    """
    Release the lock only if we still own it.
    """
    # Important:
    # GET + DELETE must happen atomically.
    # Otherwise:
    # Worker A lock expires
    # Worker B acquires lock
    # Worker A deletes Worker B's lock

    try:
        return _RELEASED_LOCK(keys=[get_lock_key(token)], args=[lock_token]) == 1
    except redis.RedisError:
        logger.exception("Failed to release token lock")
        return False


# --------------------------- The piece that ties all three protections together
def fetch_token_data(
        token: str,
        loader: Callable[[str], dict[str, Any] | None],
        max_ttl: int | None = None
) -> dict[str, Any] | None:
    """
    Cache-aside read with stampede / penetration / avalanche protection

    loader(token) -> dict if the token maps to a valid user, None if it doesn't.
                     Exceptions (e.g. DB down) propagate and are NEVER negative-cached.
    max_ttl       -> cap on positive-cache TTL (pass the JWT's remaining lifetime so a
                     cached entry can never outlive the token itself).
    Returns the user dict, or None if the token is invalid.
    """

    ttl = TOKEN_CACHE_TTL if max_ttl is None else min(TOKEN_CACHE_TTL, max_ttl)

    def _from(result: CacheResult):
        return result.data if result.valid else None

    def _laod_and_cache() -> dict[str, Any] | None:
        data = loader(token)
        if data is None:
            cache_invalid_token(token)
        else:
            cache_token_data(token)
        return data

    # fast path
    result = get_cached_token_data(token)
    if result.error:
        return loader(token)
    if result.hit:
        return _from(result)

    # 2) miss -> try to become the single loader (stampede)
    lock_token = acquire_token_lock(token)
    if lock_token:
        try:
            # double-check: another worker may have filled it between our GET and SET NX
            result = get_cached_token_data(token)
            if result.hit:
                return _from(result)
            return _laod_and_cache()
        finally:
            release_token_lock(token, lock_token)


    # 3) someone else is loading -> poll the cache with exponential backoff + jitter
    deadline = time.monotonic() + LOCK_WAIT_TOTAL_SECONDS
    sleep_for = LOCK_WAIT_INITIAL_SLEEP
    while time.monotonic() < deadline:
        time.sleep(sleep_for*random.uniform(0.5,1.0))
        result = get_cached_token_data(token)
        if result.error:
            return loader(token)
        if result.hit:
            return _from(token)
        sleep_for = min(sleep_for * 2, LOCK_WAIT_MAX_SLEEP)

    # 4) lock holder was too slow/crashed -> fall back to a direct load (bounded by lock TTL)
    logger.warning("Lock wait timed out for %s, loading directly", get_token_hash(token))
    return loader(token)
    



























RATE_LIMIT_SCRIPT = """
local current = redis.call("INCR", KEYS[1])
if current == 1 then
    redis.call("EXPIRE", KEYS[1], ARGV[1])
end

return current
"""

def increment_rate_limit(
        key: str,
        window_seconds: int,
        limit: int
) -> tuple[bool, int]:
    """
    Increment the request count for fixed window.
    Returns:
        allowed: wether request is allowed
        count: current request count
    """
    try:
        # older implementation
        # count = redis_client.incr(key)

        # if count == 1:
        #     redis_client.expire(
        #         key,
        #         window_seconds
        #     )

        # Current implementation to avoid race condition
        count = redis_client.eval(
            RATE_LIMIT_SCRIPT,
            1,
            key,
            window_seconds,
        )


        allowed = count <= limit
        return allowed, count
    except redis.RedisError:
        logger.exception(
            "Rate Limiter Redis Error"
        )

        return True, 0

def check_login_rate_limit(user_id: int):
    key = f"rate:login:user:{user_id}"

    return increment_rate_limit(
        key=key,
        window_seconds=60,
        limit=10
    )

TOKEN_BUCKET_SCRIPT = """
local key = KEYS[1]

local now = tonumber(ARGV[1])
local capacity = tonumber(ARGV[2])
local refill_rate = tonumber(ARGV[3])
local requested = tonumber(ARGV[4])
local ttl = tonumber(ARGV[5])

local data = redis.call("HMGET", key, "tokens", "last_refill")

local tokens = tonumber(data[1])
local last_refill = tonumber(data[2])

if tokens == nil then
    tokens = capacity
    last_refill = now
end

-- Calculate elapsed time
local elapsed = math.max(0, now - last_refill)

-- Refill tokens
tokens = math.min(
    capacity,
    tokens + (elapsed * refill_rate)
)

local allowed = 0

if tokens >= requested then

    tokens = tokens - requested
    allowed = 1

end

-- Save state
redis.call(
    "HSET",
    key,
    "tokens",
    tokens,
    "last_refill",
    now
)

redis.call(
    "EXPIRE",
    key,
    ttl
)

return {
    allowed,
    tokens
}
"""