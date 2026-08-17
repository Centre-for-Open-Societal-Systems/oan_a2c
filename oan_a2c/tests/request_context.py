"""Request-scoped scaffolding shared by the auth test modules.

This lives in a module of its own so a new auth test class can go into its own
`test_auth_<topic>.py` instead of being appended to an existing one. Appending is
what makes two feature branches collide at the end of the same file every time
one of them adds a test — see `docs/merge-hygiene.md`.
"""

import frappe


class RequestContextMixin:
	"""Stands up the request-scoped globals the auth code reaches for.

	LoginManager, the JWT middleware and the rate limiter all read
	frappe.local.request / cookie_manager / request_ip, which only exist during a
	real web request. Every auth test class needs the same scaffolding, so it
	lives here rather than being copied per class.
	"""

	def setUp(self):
		super().setUp()
		# _dict, not {} — frappe.local.response is a _dict at runtime and other test
		# modules set attributes on it (frappe.local.response.type = ...). A plain
		# dict here survives the module boundary and breaks them with an
		# AttributeError that looks like their own bug.
		frappe.local.response = frappe._dict()
		frappe.set_user("Administrator")

		# frappe.local.request_ip is normally set by HTTPRequest.set_request_ip() during
		# the web request cycle. In unit tests HTTPRequest is never instantiated, so the
		# value stays None. LoginAttemptTracker uses it as its Redis hash key — passing
		# None causes Redis to reject the HDEL call with a DataError.
		frappe.local.request_ip = "127.0.0.1"

		# Mock request for LoginManager and middleware
		self._original_request = getattr(frappe.local, "request", None)
		frappe.local.request = frappe._dict(
			{
				"path": "",
				"headers": {},
				"cookies": frappe._dict(),
				"scheme": "http",
				"remote_addr": "127.0.0.1",
			}
		)

		# Mock CookieManager for LoginManager
		from frappe.auth import CookieManager

		self._original_cookie_manager = getattr(frappe.local, "cookie_manager", None)
		frappe.local.cookie_manager = CookieManager()

		# Patch get_request_header for middleware tests
		self._original_get_request_header = getattr(frappe, "get_request_header", None)
		frappe.get_request_header = self._mock_get_request_header
		self._mock_headers = {}

	def tearDown(self):
		frappe.get_request_header = self._original_get_request_header

		# Restore original request
		if self._original_request:
			frappe.local.request = self._original_request
		else:
			if hasattr(frappe.local, "request"):
				delattr(frappe.local, "request")

		# Restore original cookie_manager
		if self._original_cookie_manager:
			frappe.local.cookie_manager = self._original_cookie_manager
		else:
			if hasattr(frappe.local, "cookie_manager"):
				delattr(frappe.local, "cookie_manager")

		super().tearDown()

	def _mock_get_request_header(self, key):
		return self._mock_headers.get(key)
