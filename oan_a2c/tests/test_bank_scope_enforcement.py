import ast
import os
import unittest

from oan_a2c.hooks import BANK_SCOPED

# List-returning query calls that BYPASS the permission_query_conditions hook
# (bank_scope_query) AND DocPerm. On a bank-scoped DocType these are banned
# outright: use frappe.get_list, which runs both the bank_scope_query hook and
# DocPerm, so bank isolation and read-permission can't be forgotten. Genuinely
# trusted access (background jobs, hooks, already-authorized lookups) opts out
# with a `# bank-scope-exempt` comment and a reason.
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


def _static_sql_text(node):
	"""
	Best-effort extraction of the literal text of a raw-SQL first argument.

	Handles plain strings and f-strings (JoinedStr): only the literal parts are
	returned; interpolated `{...}` expressions are ignored. Returns "" when the
	first arg isn't a static string (e.g. a variable), so such calls are skipped
	rather than guessed at.
	"""
	if not node.args:
		return ""
	arg = node.args[0]
	if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
		return arg.value
	if isinstance(arg, ast.JoinedStr):
		return "".join(
			v.value for v in arg.values if isinstance(v, ast.Constant) and isinstance(v.value, str)
		)
	return ""


class TestBankScopeEnforcement(unittest.TestCase):
	def test_bank_scoped_list_queries_use_get_list(self):
		"""Ban permission-bypassing list calls (get_all / db.get_all / db.get_list)
		on bank-scoped DocTypes. They skip BOTH the bank_scope_query hook and
		DocPerm, so a forgotten guard leaks cross-bank rows (and, when the caller is
		bank-unbound, every bank's rows) -- exactly the list_products regression.

		The fix is to use frappe.get_list, which enforces both automatically. Only
		genuinely trusted, session-less, or already-authorized access opts out with a
		trailing `# bank-scope-exempt` comment stating why.
		"""
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

				for node in ast.walk(tree):
					if not isinstance(node, ast.Call):
						continue
					if _dotted_name(node.func) not in BYPASS_LIST_CALLS:
						continue
					if _first_doctype(node) not in bank_scoped:
						continue

					# Exempted? Accept the marker on the call span or on the line
					# immediately above (comment-above-call is a common style).
					start = node.lineno - 1
					span = lines[max(0, start - 1) : (node.end_lineno or node.lineno)]
					if any(EXEMPT_MARKER in ln for ln in span):
						continue

					rel = os.path.relpath(filepath, app_dir)
					violations.append(f"{rel}:{node.lineno} {_dotted_name(node.func)}")

		if violations:
			self.fail(
				"These calls hit a bank-scoped DocType with a permission-bypassing "
				"query (get_all / db.get_all / db.get_list), which skips both the "
				"bank_scope_query hook and DocPerm. Use frappe.get_list instead, or "
				f"add a `{EXEMPT_MARKER}` comment with a reason:\n" + "\n".join(violations)
			)

	def test_raw_sql_on_bank_scoped_tables_mentions_bank(self):
		"""
		frappe.db.sql bypasses ALL permission machinery. This can't verify the
		predicate is correct, only that a raw query touching a bank-scoped table
		(`tab<DocType>`) at least references `bank`. It's a smell detector, not a
		proof of isolation; genuinely trusted queries opt out with the marker.

		NOTE: this does not — and cannot statically — cover `ignore_permissions=True`
		or dynamically-built SQL. Those remain manual-review responsibilities.
		"""
		app_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
		scoped_tables = {f"tab{dt}" for dt in BANK_SCOPED}
		violations = []

		for root, _, files in os.walk(app_dir):
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

				for node in ast.walk(tree):
					if not isinstance(node, ast.Call) or _dotted_name(node.func) != "frappe.db.sql":
						continue
					sql = _static_sql_text(node)
					if not sql:
						continue
					if not any(tbl in sql for tbl in scoped_tables):
						continue
					if "bank" in sql.lower():
						continue

					span = lines[node.lineno - 1 : (node.end_lineno or node.lineno)]
					if any(EXEMPT_MARKER in ln for ln in span):
						continue

					rel = os.path.relpath(filepath, app_dir)
					violations.append(f"{rel}:{node.lineno}")

		if violations:
			self.fail(
				"These frappe.db.sql calls query a bank-scoped table (tab<DocType>) "
				"without referencing `bank`, and raw SQL bypasses bank_scope_query "
				"entirely. Add a bank predicate, or a "
				f"`{EXEMPT_MARKER}` comment with a reason:\n" + "\n".join(violations)
			)
