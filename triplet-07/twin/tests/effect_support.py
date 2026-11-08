from city_core.catalog import CONTRACT_VERSION


def request(tag="valve_position", *, revision=0, effect_id="eff-1",
            source="B_ACTUATOR", run_id="r-test", **fields):
    if not fields:
        fields = {"target_id": "V-12", "opening_fraction": 0.0}
    return {"contract_version": CONTRACT_VERSION, "run_id": run_id,
            "effect_id": effect_id, "expected_revision": revision,
            "source": source, "correlation_id": "cmd-17", "effect": {"tag": tag, **fields}}
