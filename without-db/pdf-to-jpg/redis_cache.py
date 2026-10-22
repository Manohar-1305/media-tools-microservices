import hashlib
import json
import os

import redis

redis_client = redis.Redis(
    host=os.getenv("REDIS_HOST", "redis"),
    port=int(os.getenv("REDIS_PORT", "6379")),
    db=int(os.getenv("REDIS_DB", "0")),
    password=os.getenv("REDIS_PASSWORD") or None,
    decode_responses=False,
    socket_connect_timeout=3,
    socket_timeout=5,
    health_check_interval=30,
)

CACHE_TTL = int(os.getenv("REDIS_CACHE_TTL", "3600"))
CACHE_MAX_BYTES = int(os.getenv("REDIS_CACHE_MAX_BYTES", "5242880"))


def cache_key(service, file_bytes, settings=None):
    digest = hashlib.sha256()
    digest.update(service.encode())
    digest.update(b"\0")
    digest.update(file_bytes)
    digest.update(b"\0")
    digest.update(json.dumps(settings or {}, sort_keys=True).encode())
    return "media-tools:conversion:" + digest.hexdigest()


def cache_get(key):
    try:
        return redis_client.get(key)
    except redis.RedisError:
        return None


def cache_set(key, output_bytes):
    if len(output_bytes) > CACHE_MAX_BYTES:
        return False
    try:
        redis_client.setex(key, CACHE_TTL, output_bytes)
        return True
    except redis.RedisError:
        return False
