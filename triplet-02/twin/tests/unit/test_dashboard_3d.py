from pathlib import Path
import struct

from city_dashboard.server import ASSETS, WEB


def test_3d_viewer_assets_are_allowlisted_and_present():
    expected = {
        '/redesign.css': 'redesign.css',
        '/city3d-loader.js': 'city3d-loader.js',
        '/3d/assets/city3d-viewer.js': '3d/assets/city3d-viewer.js',
        '/3d/assets/city3d.css': '3d/assets/city3d.css',
        '/3d/models/city-cs-challenge.glb': '3d/models/city-cs-challenge.glb',
    }
    for route, relative_path in expected.items():
        assert ASSETS[route][0] == relative_path
        assert (WEB / relative_path).is_file()
    assert '/3d/../../server.py' not in ASSETS


def test_city_model_is_glb_version_two():
    model = WEB / '3d/models/city-cs-challenge.glb'
    with model.open('rb') as source:
        magic, version, declared_length = struct.unpack('<4sII', source.read(12))
    assert magic == b'glTF'
    assert version == 2
    assert declared_length == model.stat().st_size


def test_viewer_consumes_dashboard_frames_without_private_report_fallback():
    root = Path(__file__).resolve().parents[2]
    dashboard_app = (root / 'src/city_dashboard/web/app.js').read_text(encoding='utf-8')
    viewer_source = (root / 'tools/dashboard-3d/src/main.jsx').read_text(encoding='utf-8')
    assert "new CustomEvent('twin-dashboard-frame'" in dashboard_app
    assert "visibility === 'PRIVATE_OPERATOR_ONLY'" in viewer_source
    assert "frame.visibility === 'PRIVATE_OPERATOR_ONLY'" in viewer_source
    assert "visibility === 'REPORTED_ONLY'" in viewer_source
    assert "Twin source value (S_real)" in viewer_source
    assert "Reported sensor value (S_reported)" in viewer_source
    assert "requestJSON('/api/operator/scenario'" in viewer_source
    assert "Guardian verdict" in viewer_source
    assert "BRAIN SERVICE NOT CONNECTED" in viewer_source
    assert "source value inside the Twin simulation" in viewer_source
    assert "scenario_catalog" in viewer_source
    assert "scenario_id: selectedScenarioId" in viewer_source
    assert "No hidden queue or automatic replay" in viewer_source
    assert "Event category" in viewer_source
    assert "Security perimeter" not in viewer_source
    assert "Health" in viewer_source
    assert "fetch('/api/twin/state')" not in viewer_source
    assert "fetch('/api/guardian" not in viewer_source.lower()
    assert "Trust scores, HMAC verification, anomaly verdicts" in viewer_source


def test_dashboard_does_not_claim_marker_coordinates_are_real():
    root = Path(__file__).resolve().parents[2]
    viewer_source = (root / 'tools/dashboard-3d/src/main.jsx').read_text(encoding='utf-8')
    assert 'position: {x: 0.52, y: 0.12, z: 0.48}' in viewer_source
    assert 'position: {x: 0.68, y: 0.08, z: 0.55}' in viewer_source
    assert 'marker positions are illustrative placeholders' in viewer_source
    assert 'not asset coordinates' in viewer_source
    assert "fetch('/api/guardian" not in viewer_source.lower()
