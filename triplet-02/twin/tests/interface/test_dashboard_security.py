import ast
from pathlib import Path
import unittest


class InterfaceDashboardSecurityTests(unittest.TestCase):
    def test_dashboard_does_not_inject_credentials_into_template(self):
        repository = Path(__file__).resolve().parents[2]
        source = (repository / "tools" / "interface_dashboard.py").read_text(
            encoding="utf-8"
        )
        template = (
            repository / "tools" / "templates" / "dashboard.html"
        ).read_text(encoding="utf-8")
        module = ast.parse(source)
        root = next(
            node
            for node in module.body
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "root"
        )
        response = next(
            node
            for node in ast.walk(root)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "TemplateResponse"
        )
        context = response.args[1]

        self.assertIsInstance(context, ast.Dict)
        self.assertEqual(
            [key.value for key in context.keys if isinstance(key, ast.Constant)],
            ["request"],
        )
        self.assertNotIn("DEFAULT_TOKENS", source)
        self.assertNotRegex(template, r"\{\{[^}]*token")
