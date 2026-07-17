import frappe
from frappe import _
from frappe.model.document import Document


class A2CLead(Document):
	def before_save(self):
		if not self.is_new():
			db_status = self.get_db_value("status")
			if db_status == "Processed":
				frappe.throw(
					_("Lead cannot be edited because it is already Processed"), frappe.ValidationError
				)
			elif db_status == "Rejected" and self.status != "Rejected":
				frappe.throw(_("Status is locked because the lead is Rejected"), frappe.ValidationError)
			# Guard the transition *into* Verified: only fire when the status is
			# actually changing to Verified (not on re-saves of an already
			# Verified lead), so the prerequisites are enforced once, up front.
			if self.status == "Verified" and db_status != "Verified":
				self._enforce_verification_prerequisites()
		self._enforce_phone_uniqueness()
		self._enforce_external_id_uniqueness()

	def _enforce_verification_prerequisites(self):
		"""
		A lead may only become 'Verified' once both exist and are linked to it:
		  - at least one A2C Credit Information, and
		  - an A2C Consent Request with status 'Approved'.
		This guarantees credit info and an approved consent are in place before
		verification, on every path (API, Desk, bulk edit).
		"""
		has_credit = frappe.db.exists("A2C Credit Information", {"lead": self.name})
		has_approved_consent = frappe.db.exists(
			"A2C Consent Request", {"lead": self.name, "status": "Approved"}
		)

		missing = []
		if not has_credit:
			missing.append(_("credit information"))
		if not has_approved_consent:
			missing.append(_("an approved consent request"))

		if missing:
			frappe.throw(
				_("Lead cannot be Verified until {0} exists.").format(_(" and ").join(missing)),
				frappe.ValidationError,
			)

	def after_insert(self):
		self._clear_number_card_cache()

	def on_change(self):
		self._clear_number_card_cache()

	def on_trash(self):
		self._clear_number_card_cache()

	def _clear_number_card_cache(self):
		"""
		Proactively invalidates Redis cache keys for all Dashboard Number Cards
		associated with the 'A2C Lead' DocType to maintain real-time aggregates.
		"""
		try:
			cards = frappe.get_all("Number Card", filters={"document_type": "A2C Lead"}, pluck="name")
			for card in cards:
				cache_key = f"number_card_data:{card}"
				frappe.cache().delete_value(cache_key)
		except Exception:
			# Fail close. Never allow cache clearance anomalies to block core lead
			# transactions — but leave a trace so silent cache drift is diagnosable.
			frappe.logger().warning(
				f"Number Card cache invalidation failed for lead {self.name}: {frappe.get_traceback(with_context=False)}"
			)

	def _enforce_external_id_uniqueness(self):
		"""
		Enforce uniqueness of external_id ONLY if it is populated.
		Allows multiple internal leads (where external_id is blank) to exist.
		"""
		if not self.external_id:
			return

		existing = frappe.db.get_value(
			"A2C Lead",
			{
				"external_id": self.external_id,
				"name": ("!=", self.name or ""),
			},
			"name",
		)
		if existing:
			frappe.throw(
				_("A lead ({0}) already exists with External Reference ID {1}.").format(
					existing, self.external_id
				),
				frappe.DuplicateEntryError,
			)

	def _enforce_phone_uniqueness(self):
		"""
		Enforce uniqueness of phone_number among active (non-terminal) leads only.

		A converted or closed lead must not block a new lead for the same farmer
		re-entering the funnel in a later season. Frappe's built-in 'unique'
		constraint is too coarse for this conditional requirement.
		"""
		if not self.phone_number:
			return

		active_statuses = ("Active", "Verified")
		existing = frappe.db.get_value(
			"A2C Lead",
			{
				"phone_number": self.phone_number,
				"status": ("in", active_statuses),
				"name": ("!=", self.name or ""),
			},
			"name",
		)
		if existing:
			frappe.throw(
				_(
					"An active lead ({0}) already exists for phone number {1}. "
					"Duplicate leads are blocked to maintain clean funnel data."
				).format(existing, self.phone_number),
				frappe.DuplicateEntryError,
			)
