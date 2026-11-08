"""Capability-specific presentation serialization. No role parameter grants truth."""


def capture_operator_frame(owner):
    return owner.capture_dashboard_frame()


def read_reported_frame(public_reader, cursor=None):
    page = public_reader.capture_reported(cursor)
    return {**page, 'schema_version': 'twin-dashboard-report-0.1-local',
            'visibility': 'REPORTED_ONLY'}
