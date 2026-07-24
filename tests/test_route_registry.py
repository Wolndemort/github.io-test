from admin_module.api import router


def _route_keys():
    keys = []
    for route in router.routes:
        methods = getattr(route, "methods", None) or {"GET"}
        for method in methods:
            keys.append((route.path, method))
    return keys


def test_critical_routes_are_registered():
    keys = set(_route_keys())
    assert ("/webapp/open-turnstile", "POST") in keys
    assert ("/webapp/client-cabinet", "GET") in keys
    assert ("/webapp/client-cabinet/freeze", "POST") in keys
    assert ("/v1/payments/yookassa/webhook", "POST") in keys
    assert ("/health", "GET") not in keys  # health belongs to FastAPI app, not this router


def test_router_has_no_duplicate_path_method_pairs():
    keys = _route_keys()
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    assert duplicates == []
