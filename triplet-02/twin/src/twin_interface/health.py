"""Non-mutating service health report helpers."""


def liveness_report(service):
    """The caller is live when it can answer this request."""
    return {"status": "UP", "service": service}


def readiness_report(service, runtime, *, required_checks=None, optional_checks=None):
    """Combine the service boundary and LiveCity's own operational checks."""
    checks = {}
    required_checks = required_checks or {}
    optional_checks = optional_checks or {}

    for name, available in required_checks.items():
        checks[name] = {"status": "UP" if available else "DOWN"}

    try:
        runtime_report = runtime.health() if runtime is not None else None
        if type(runtime_report) is not dict:
            runtime_report = None
        runtime_checks = (
            runtime_report.get("checks")
            if runtime_report is not None
            else None
        )
        if type(runtime_checks) is not dict:
            runtime_report = None
            runtime_checks = {}
    except Exception:
        runtime_report = None
        runtime_checks = {}

    if runtime_report is None:
        checks["livecity"] = {"status": "DOWN"}
        checks["city_core"] = {"status": "DOWN"}
    else:
        checks["livecity"] = {
            "status": "UP" if runtime_report.get("status") == "UP" else "DOWN"
        }
        checks["city_core"] = {
            "status": "UP"
            if runtime_report.get("status") == "UP"
            and type(runtime_checks.get("city_core")) is dict
            and runtime_checks["city_core"].get("status") == "UP"
            else "DOWN"
        }
        for name, check in runtime_checks.items():
            if name == "city_core":
                continue
            checks[name] = {
                "status": "UP"
                if type(check) is dict and check.get("status") == "UP"
                else "DOWN"
            }

    for name, available in optional_checks.items():
        if available is True or available == "UP":
            status = "UP"
        elif available is False or available == "DEGRADED":
            status = "DEGRADED"
        else:
            status = "DOWN"
        checks[name] = {"status": status}

    if any(check["status"] == "DOWN" for check in checks.values()):
        status = "DOWN"
    elif any(check["status"] == "DEGRADED" for check in checks.values()):
        status = "DEGRADED"
    else:
        status = "READY"

    return {"status": status, "service": service, "checks": checks}


def readiness_http_status(report):
    return 503 if report["status"] == "DOWN" else 200
