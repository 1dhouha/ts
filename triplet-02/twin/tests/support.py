from city_core import baseline_catalog, baseline_config, build_city


def city(**kwargs):
    result = build_city("r-test", **kwargs)
    if not result.ok:
        raise AssertionError(result.error)
    return result.value
