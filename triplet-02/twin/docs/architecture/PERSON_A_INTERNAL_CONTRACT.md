# Person A Internal Contract

## 6. Effect Envelope and Receipt Format

```json
{
  "contract_version": "pa-internal-0.1-draft",
  "run_id": "r-test",
  "effect_id": "eff-1",
  "expected_revision": 0,
  "source": "B_ACTUATOR",
  "correlation_id": "cmd-17",
  "effect": {
    "tag": "valve_position",
    "target_id": "V-12",
    "opening_fraction": 0.0
  }
}
```

```json
{
  "context": {
    "run_id": "r-test",
    "tick": 0,
    "sim_time_ms": 0,
    "revision": 1,
    "model_version": "pa-model-0.1-draft",
    "catalog_version": "pa-catalog-0.1-draft",
    "contract_version": "pa-internal-0.1-draft"
  },
  "effect_id": "eff-1",
  "sequence": 0,
  "correlation_id": "cmd-17",
  "outcome": "APPLIED",
  "duplicate": false
}
```

## 7. Next Section
