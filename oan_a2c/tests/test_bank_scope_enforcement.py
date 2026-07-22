import ast
import os
import unittest

from oan_a2c.hooks import BANK_SCOPED

# List-returning query calls that BYPASS the permission_query_conditions hook
# (bank_scope_query). Any of these against a bank-scoped DocType must have its
# filters routed through permissions.bank_filters(), which fails closed.
BYPASS_LIST_CALLS = {
	"frappe.get_all",
	"frappe.db.get_all",
	"frappe.db.get_list",
}

# Legitimate unscoped access can opt out with a trailing `# bank-scope-exempt`
# comment on any line of the call. Use sparingly and with a reason.
EXEMPT_MARKER = "# bank-scope-exempt"


def _dotted_name(node):
	"""Return the dotted call name for `node.func`, e.g. 'frappe.db.get_all'."""
	parts = []
	while isinstance(node, ast.Attribute):
		parts.append(node.attr)
		node = node.value
	if isinstance(node, ast.Name):
		parts.append(node.id)
	else:
		return None
	return ".".join(reversed(parts))


def _first_doctype(call):
	"""String literal DocType from the first positional arg or `doctype=` kwarg."""
	if call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
		return call.args[0].value
	for kw in call.keywords:
		if kw.arg == "doctype" and isinstance(kw.value, ast.Constant):
			return kw.value.value
	return None


def _is_bank_filters(node):
	"""True if node is a call to bank_filters(...)."""
	return isinstance(node, ast.Call) and (_dotted_name(node.func) or "").endswith("bank_filters")


class TestBankScopeEnforcement(unittest.TestCase):
	def test_bank_scoped_list_queries_use_bank_filters(self):
		app_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
		bank_scoped = set(BANK_SCOPED)
		violations = []

		for root, _, files in os.walk(app_dir):
			# Don't police the tests themselves or generated/cache dirs.
			if os.sep + "tests" in root or "__pycache__" in root:
				continue
			for file in files:
				if not file.endswith(".py"):
					continue
				filepath = os.path.join(root, file)
				with open(filepath) as f:
					source = f.read()
				try:
					tree = ast.parse(source, filename=filepath)
				except SyntaxError:
					continue
				lines = source.splitlines()

				# Names bound directly from a bank_filters(...) call, e.g.
				# `filters = bank_filters(...)`, so callers can pass the variable.
				scoped_names = {
					t.id
					for node in ast.walk(tree)
					if isinstance(node, ast.Assign) and _is_bank_filters(node.value)
					for t in node.targets
					if isinstance(t, ast.Name)
				}

				for node in ast.walk(tree):
					if not isinstance(node, ast.Call):
						continue
					if _dotted_name(node.func) not in BYPASS_LIST_CALLS:
						continue
					if _first_doctype(node) not in bank_scoped:
						continue

					# Exempted inline?
					span = lines[node.lineno - 1 : (node.end_lineno or node.lineno)]
					if any(EXEMPT_MARKER in ln for ln in span):
						continue

					filt = next((kw.value for kw in node.keywords if kw.arg == "filters"), None)
					ok = _is_bank_filters(filt) or (isinstance(filt, ast.Name) and filt.id in scoped_names)
					if not ok:
						rel = os.path.relpath(filepath, app_dir)
						violations.append(f"{rel}:{node.lineno} {_dotted_name(node.func)}")

		if violations:
			self.fail(
				"These queries hit a bank-scoped DocType but do not scope their "
				"`filters` through permissions.bank_filters() (which bypasses the "
				"bank_scope_query hook). Route filters through bank_filters(), or add "
				f"a `{EXEMPT_MARKER}` comment with a reason:\n" + "\n".join(violations)
			)
