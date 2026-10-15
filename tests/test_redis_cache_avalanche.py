from services.redis_layer import get_cache_ttl_with_jitter


def test_cache_ttl_has_randomized_jitter():
    base_ttl = 300
    jitter = 30

    for _ in range(20):
        ttl = get_cache_ttl_with_jitter(base_ttl, jitter)
        assert base_ttl <= ttl <= base_ttl + jitter
