import frappe
from frappe import _
from frappe.model.document import Document


class A2CLead(Document):
	# Committed/terminal states are read-only via a normal save. Legitimate status
	# changes run through the workflow (apply_status_transition -> apply_workflow),
	# which bypasses before_save, so any before_save hit on these states is an
	# unintended field edit and is rejected.
	LOCKED_STATUSES = ("Processed", "Granted", "Rejected")

	def before_save(self):
		if not self.is_new():
			db_status = self.get_db_value("status")
			# apply_workflow saves the doc for draft -> draft moves (Processed -> Granted /
			# Rejected), so the transition itself lands here. apply_status_transition flags
			# it after the workflow has already checked legality and role.
			if db_status in self.LOCKED_STATUSES and not self.flags.in_workflow_transition:
				frappe.throw(
					_("Lead cannot be edited because it is {0}.").format(_(db_status)),
					frappe.ValidationError,
				)
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
			"A2C Consent Request",
			{"reference_doctype": "A2C Lead", "reference_name": self.name, "status": "Approved"},
		)
		has_scheduled_visit = frappe.db.exists(
			"A2C Visit Schedule", {"lead": self.name, "status": ["in", ["Scheduled", "Completed"]]}
		)

		missing = []
		if not has_credit:
			missing.append(_("credit information"))
		if not has_approved_consent:
			missing.append(_("an approved consent request"))
		if not has_scheduled_visit:
			missing.append(_("a scheduled visit"))

		if missing:
			frappe.throw(
				_("Lead cannot be Verified until {0} exists.").format(f" {_('and')} ".join(missing)),
				frappe.ValidationError,
			)

	def after_insert(self):
		self._clear_number_card_cache()

	def on_change(self):
		self._clear_number_card_cache()

	def on_trash(self):
		self._clear_number_card_cache()
		self._remove_lead_owned_records()

	def _remove_lead_owned_records(self):
		"""
		Let a lead be deleted together with the records that only exist for it.

		Runs from on_trash, which Frappe calls before its link check. The lead and
		its consent request point at each other (A2C Lead.consent_id and the consent
		request's reference_name), and a Processed/Granted/Rejected lead cannot be
		saved, so the link could never be cleared and neither record could go. The
		pointers are cleared with db.set_value, which skips the save-time lock.

		A lead with a loan application is a committed record, so nothing is removed
		and the standard link check refuses the delete. Delete permission on the
		lead (System Manager / A2C Administrator) is what gates this.
		"""
		if frappe.db.exists("A2C Loan Application", {"lead_id": self.name}):
			return

		consents = frappe.get_all(
			"A2C Consent Request",
			filters={"reference_doctype": "A2C Lead", "reference_name": self.name},
			pluck="name",
		)
		if self.consent_id and self.consent_id not in consents:
			consents.append(self.consent_id)

		if consents:
			frappe.db.set_value("A2C Lead", self.name, "consent_id", None, update_modified=False)
			self.consent_id = None
			# The farmer profile outlives the lead; only its pointer to the consent goes.
			for profile in frappe.get_all(
				"A2C Farmer Profile", filters={"consent_id": ["in", consents]}, pluck="name"
			):
				frappe.db.set_value("A2C Farmer Profile", profile, "consent_id", None, update_modified=False)
			for consent in consents:
				frappe.delete_doc("A2C Consent Request", consent, ignore_permissions=True)

		for doctype in ("A2C Credit Information", "A2C Visit Schedule", "A2C Lead Audit Event"):
			for name in frappe.get_all(doctype, filters={"lead": self.name}, pluck="name"):
				frappe.delete_doc(doctype, name, ignore_permissions=True)

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
