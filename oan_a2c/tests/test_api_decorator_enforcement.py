import ast
import os
import unittest

class TestApiDecoratorEnforcement(unittest.TestCase):
    def test_whitelist_endpoints_decorated(self):
        api_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "api"))
        whitelisted_missing_decorator = []

        # Exempt specific integration files like dev.py
        exempt_files = {"dev.py"}

        for root, _, files in os.walk(api_dir):
            for file in files:
                if not file.endswith(".py") or file in exempt_files:
                    continue

                filepath = os.path.join(root, file)
                with open(filepath, "r") as f:
                    try:
                        tree = ast.parse(f.read(), filename=filepath)
                    except SyntaxError:
                        continue

                for node in ast.walk(tree):
                    if isinstance(node, ast.FunctionDef):
                        decorators = []
                        for dec in node.decorator_list:
                            # Extract name of decorator
                            if isinstance(dec, ast.Name):
                                decorators.append(dec.id)
                            elif isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name):
                                decorators.append(dec.func.id)
                            elif isinstance(dec, ast.Attribute):
                                decorators.append(dec.attr)

                        # If whitelisted but missing handle_api_errors, report it
                        if any("whitelist" in d for d in decorators):
                            if not any("handle_api_errors" in d for d in decorators):
                                rel_path = os.path.relpath(filepath, api_dir)
                                whitelisted_missing_decorator.append(f"{rel_path}:{node.lineno} def {node.name}")

        if whitelisted_missing_decorator:
            self.fail(
                "The following whitelisted endpoints are missing the `@handle_api_errors` decorator:\n"
                + "\n".join(whitelisted_missing_decorator)
            )
