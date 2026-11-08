from pathlib import Path
import importlib.util
import tempfile
import unittest
from unittest.mock import patch

class ManifestTests(unittest.TestCase):
    def test_code_markers_never_exclude_substantive_bytes(self):
        path=Path(__file__).resolve().parents[1]/'tools/dashboard_manifest.py'
        spec=importlib.util.spec_from_file_location('manifest_test',path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        self.assertEqual(module.content_bytes(path),path.read_bytes())

    def test_only_phase_report_evidence_suffix_is_normalized(self):
        path=Path(__file__).resolve().parents[1]/'tools/dashboard_manifest.py'
        spec=importlib.util.spec_from_file_location('manifest_test',path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        temp_root=path.parents[2]/'tmp/dashboard_remaining';temp_root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as folder:
            root=Path(folder).resolve();self.assertTrue(root.is_relative_to(temp_root.resolve()))
            report=root/'docs/architecture/dashboard/d07/D07_REPORT.md'
            report.parent.mkdir(parents=True);report.write_bytes(b'criteria\n<!-- EXECUTION EVIDENCE: suffix -->checks')
            other=root/'docs/integration/OTHER_REPORT.md';other.parent.mkdir(parents=True);other.write_bytes(report.read_bytes())
            with patch.object(module,'ROOT',root):
                self.assertEqual(module.content_bytes(report),b'criteria\n')
                self.assertEqual(module.content_bytes(other),other.read_bytes())
