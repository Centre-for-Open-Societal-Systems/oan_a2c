import ast
import os
import unittest

from oan_a2c.api.router import get_routes_spec


class TestApiDecoratorEnforcement(unittest.TestCase):
	"""Enforces decorator conventions across the A2C API surface.

	Mandates:
	1. Zero endpoints in oan_a2c/api use the legacy @frappe.whitelist decorator,
	   as all routing is strictly handled via Werkzeug REST routing and OpenAPI spec.
	2. All 96 endpoints registered in the OpenAPI route specification must be wrapped
	   with the `@handle_api_errors` decorator for consistent error formatting and request ID tracking.
	"""

	def test_no_whitelist_decorators_in_api(self):
		"""Assert that no functions in oan_a2c/api use @frappe.whitelist."""
		api_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "api"))
		whitelisted_functions = []

		for root, _, files in os.walk(api_dir):
			for file in files:
				if not file.endswith(".py"):
					continue

				filepath = os.path.join(root, file)
				with open(filepath) as f:
					try:
						tree = ast.parse(f.read(), filename=filepath)
					except SyntaxError:
						continue

				for node in ast.walk(tree):
					if isinstance(node, ast.FunctionDef):
						decorators = []
						for dec in node.decorator_list:
							if isinstance(dec, ast.Name):
								decorators.append(dec.id)
							elif isinstance(dec, ast.Call):
								if isinstance(dec.func, ast.Name):
									decorators.append(dec.func.id)
								elif isinstance(dec.func, ast.Attribute):
									decorators.append(dec.func.attr)
							elif isinstance(dec, ast.Attribute):
								decorators.append(dec.attr)

						if any("whitelist" in d for d in decorators):
							rel_path = os.path.relpath(filepath, api_dir)
							whitelisted_functions.append(f"{rel_path}:{node.lineno} def {node.name}")

		if whitelisted_functions:
			self.fail(
				"Found legacy `@frappe.whitelist` decorator on API functions. "
				"All endpoints must use REST routing without @frappe.whitelist:\n"
				+ "\n".join(whitelisted_functions)
			)

	def test_all_spec_routes_have_handle_api_errors(self):
		"""Assert that all routes defined in get_routes_spec() have the @handle_api_errors decorator."""
		routes = get_routes_spec()
		handlers = sorted(list(set(r[2] for r in routes)))
		base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

		missing_decorator = []

		for handler in handlers:
			parts = handler.split(".")
			fn_name = parts[-1]
			rel_module_path = os.path.join(*parts[:-1]) + ".py"
			abs_file = os.path.join(base_dir, rel_module_path)

			if not os.path.exists(abs_file):
				missing_decorator.append(f"{handler}: file not found at {abs_file}")
				continue

			with open(abs_file) as f:
				try:
					tree = ast.parse(f.read(), filename=abs_file)
				except SyntaxError as e:
					missing_decorator.append(f"{handler}: syntax error in {abs_file}: {e}")
					continue

			found = False
			has_handle_errors = False
			for node in ast.walk(tree):
				if isinstance(node, ast.FunctionDef) and node.name == fn_name:
					found = True
					decorators = []
					for dec in node.decorator_list:
						if isinstance(dec, ast.Name):
							decorators.append(dec.id)
						elif isinstance(dec, ast.Call):
							if isinstance(dec.func, ast.Name):
								decorators.append(dec.func.id)
							elif isinstance(dec.func, ast.Attribute):
								decorators.append(dec.func.attr)
						elif isinstance(dec, ast.Attribute):
							decorators.append(dec.attr)

					if "handle_api_errors" in decorators:
						has_handle_errors = True
					break

			if not found:
				missing_decorator.append(f"{handler}: function definition not found in {abs_file}")
			elif not has_handle_errors:
				missing_decorator.append(f"{handler}: missing `@handle_api_errors` decorator")

		if missing_decorator:
			self.fail(
				"The following API endpoints in route spec are missing `@handle_api_errors`:\n"
				+ "\n".join(missing_decorator)
			)
