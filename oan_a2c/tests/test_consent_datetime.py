import unittest
from datetime import datetime

import frappe
from frappe.utils import now_datetime

from oan_a2c.api.utils import from_tz_aware_iso, to_tz_aware_iso


class TestFromTzAwareIso(unittest.TestCase):
	"""Regression guard for from_tz_aware_iso (inverse of to_tz_aware_iso).

	Bug: the consent direct-response path builds a synthetic webhook payload
	whose `published_at` comes from to_tz_aware_iso(now_datetime()), e.g.
	'2026-08-04T11:05:27.619565+05:30'. process_consent_data wrote that string
	straight into the Datetime column A2C Consent Request.websub_delivered_at,
	which MariaDB rejects (1292 Incorrect datetime value) because of the 'T'
	separator and offset. The crash rolled back and stamped status 'Failed', so
	the farmer profile was never created. The existing webhook test never caught
	this because its fixture used a naive 'YYYY-MM-DD HH:MM:SS' published_at.
	"""

	def test_none_and_empty_return_none(self):
		self.assertIsNone(from_tz_aware_iso(None))
		self.assertIsNone(from_tz_aware_iso(""))

	def test_tz_aware_iso_becomes_naive_datetime(self):
		result = from_tz_aware_iso("2026-08-04T11:05:27.619565+05:30")
		self.assertIsInstance(result, datetime)
		self.assertIsNone(result.tzinfo, "must be naive to be storable in a MariaDB datetime column")

	def test_naive_iso_string_passthrough(self):
		# The pre-existing webhook fixture format must keep working.
		result = from_tz_aware_iso("2026-06-06 10:42:03")
		self.assertIsInstance(result, datetime)
		self.assertIsNone(result.tzinfo)

	def test_round_trip_preserves_instant(self):
		dt = now_datetime()
		self.assertEqual(from_tz_aware_iso(to_tz_aware_iso(dt)), dt)

	def test_converted_value_persists_to_websub_delivered_at(self):
		"""End-to-end guard: the value produced for `published_at` must be
		writable to the actual Datetime column via the same set_value path
		process_consent_data uses."""
		cr = frappe.get_doc({"doctype": "A2C Consent Request", "status": "Pending OTP"}).insert(
			ignore_permissions=True
		)
		try:
			published_at = to_tz_aware_iso(now_datetime())
			frappe.db.set_value(
				"A2C Consent Request",
				cr.name,
				"websub_delivered_at",
				from_tz_aware_iso(published_at),
			)
			self.assertIsNotNone(frappe.db.get_value("A2C Consent Request", cr.name, "websub_delivered_at"))
		finally:
			frappe.delete_doc("A2C Consent Request", cr.name, ignore_permissions=True, force=True)
			frappe.db.commit()
