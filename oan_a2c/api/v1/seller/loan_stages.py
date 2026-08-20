import frappe
from frappe import _
from pydantic import BaseModel, Field, field_validator

from oan_a2c.a2c_marketplace.permissions import (
	bank_scoped,
	get_user_bank,
	is_bank_unbound,
)
from oan_a2c.a2c_marketplace.roles import (
	ADMIN_ROLE,
	BANK_ADMIN_ROLE,
	BANK_AGENT_ROLE,
	require_role,
)
from oan_a2c.api.utils import handle_api_errors, success_response, validate_request


class AddStageSchema(BaseModel):
	label: str = Field(..., min_length=1, max_length=140)
	archetype_state: str = Field(..., min_length=1, max_length=50)
	sequence: int | None = Field(None, ge=1, le=1000)
	external_code: str | None = Field(None, max_length=140)
	description: str | None = Field(None, max_length=2000)

	@field_validator("archetype_state")
	@classmethod
	def validate_archetype(cls, v):
		allowed = {"In Transition", "Completed"}
		if v not in allowed:
			raise ValueError(_("Archetype state must be one of: {0}").format(", ".join(allowed)))
		return v


class StageConfigItem(BaseModel):
	stage_id: str | None = Field(None, min_length=1, max_length=140)
	label: str = Field(..., min_length=1, max_length=140)
	archetype_state: str | None = Field(None, max_length=50)
	sequence: int | None = Field(None, ge=1, le=1000)
	external_code: str | None = Field(None, max_length=140)
	description: str | None = Field(None, max_length=2000)

	@field_validator("archetype_state")
	@classmethod
	def validate_archetype(cls, v):
		if v is not None:
			allowed = {"In Transition", "Completed"}
			if v not in allowed:
				raise ValueError(_("Archetype state must be one of: {0}").format(", ".join(allowed)))
		return v


class SyncStagesSchema(BaseModel):
	stages: list[StageConfigItem] = Field(..., min_length=1)


def _resolve_bank(bank_arg: str | None = None) -> str:
	"""Helper to resolve the effective bank for the calling user."""
	user = frappe.session.user
	if is_bank_unbound(user):
		if bank_arg:
			return bank_arg
		frappe.throw(_("Bank parameter is required for platform administrators."), frappe.ValidationError)
	user_bank = get_user_bank(user)
	if not user_bank:
		frappe.throw(_("User is not associated with any bank."), frappe.PermissionError)
	return user_bank


@frappe.whitelist(allow_guest=False, methods=["GET"])
@handle_api_errors
@require_role([BANK_ADMIN_ROLE, BANK_AGENT_ROLE, ADMIN_ROLE, "System Manager"])
def get_stages(bank: str | None = None):
	"""List all loan status stages configured for the caller's bank."""
	effective_bank = _resolve_bank(bank)

	stages = frappe.get_all(
		"A2C Loan Status Stage",
		filters={"bank": effective_bank},
		fields=[
			"name",
			"bank",
			"stage_id",
			"label",
			"archetype_state",
			"sequence",
			"external_code",
			"description",
			"creation",
			"modified",
		],
		order_by="sequence asc, creation asc",
	)

	counts = frappe.get_all(
		"A2C Loan Application",
		filters={"bank": effective_bank},
		fields=["stage_id", {"COUNT": "name", "as": "count"}],
		group_by="stage_id",
	)
	count_map = {c.stage_id: c.count for c in counts if c.stage_id}

	for s in stages:
		s["application_count"] = count_map.get(s.stage_id, 0)

	return success_response(
		data={"stages": stages, "bank": effective_bank},
		message=_("Loan status stages retrieved successfully"),
	)


@frappe.whitelist(allow_guest=False, methods=["POST"])
@bank_scoped
@validate_request(AddStageSchema)
@handle_api_errors
@require_role([BANK_ADMIN_ROLE, ADMIN_ROLE, "System Manager"])
def add_stage(bank: str | None = None, **kwargs):
	"""Add a single new stage to the bank's pipeline."""
	effective_bank = _resolve_bank(bank)

	label = kwargs["label"].strip()
	archetype_state = kwargs["archetype_state"]
	sequence = kwargs.get("sequence")
	external_code = kwargs.get("external_code")
	description = kwargs.get("description")

	if frappe.db.exists("A2C Loan Status Stage", {"bank": effective_bank, "label": label}):
		frappe.throw(
			_("A stage with label '{0}' already exists for your bank.").format(label),
			frappe.DuplicateEntryError,
		)

	if sequence is None:
		max_seq = frappe.db.get_value(
			"A2C Loan Status Stage",
			{"bank": effective_bank},
			"coalesce(max(sequence), 0)",
		)
		sequence = (max_seq or 0) + 1

	doc = frappe.get_doc(
		{
			"doctype": "A2C Loan Status Stage",
			"bank": effective_bank,
			"label": label,
			"archetype_state": archetype_state,
			"sequence": sequence,
			"external_code": external_code,
			"description": description,
		}
	)
	doc.insert(ignore_permissions=False)

	return success_response(
		data={
			"name": doc.name,
			"stage_id": doc.stage_id,
			"label": doc.label,
			"archetype_state": doc.archetype_state,
			"sequence": doc.sequence,
			"external_code": doc.external_code,
		},
		message=_("Loan status stage added successfully"),
	)


@frappe.whitelist(allow_guest=False, methods=["POST"])
@bank_scoped
@validate_request(SyncStagesSchema)
@handle_api_errors
@require_role([BANK_ADMIN_ROLE, ADMIN_ROLE, "System Manager"])
def sync_stages(bank: str | None = None, **kwargs):
	"""
	Single API to manage the entire pipeline:
	- Renames stages
	- Changes ordering / sequences
	- Updates archetype state & metadata
	- Deletes any omitted stages (fails safely if applications are active on them)
	- Inserts any new stage items without a stage_id
	"""
	effective_bank = _resolve_bank(bank)
	stage_items = kwargs["stages"]

	existing_stages = frappe.get_all(
		"A2C Loan Status Stage",
		filters={"bank": effective_bank},
		fields=["name", "stage_id", "label", "archetype_state", "sequence"],
	)
	existing_by_stage_id = {s.stage_id: s for s in existing_stages}

	kept_stage_ids = set()

	# Validate duplicate labels in submitted payload
	labels_seen = set()
	for _idx, item in enumerate(stage_items):
		label = item.label.strip() if hasattr(item, "label") else item["label"].strip()
		if label.lower() in labels_seen:
			frappe.throw(
				_("Duplicate stage label '{0}' in request.").format(label),
				frappe.ValidationError,
			)
		labels_seen.add(label.lower())

	# Process updates and new additions
	for idx, item in enumerate(stage_items):
		stg_id = item.stage_id if hasattr(item, "stage_id") else item.get("stage_id")
		label = (item.label if hasattr(item, "label") else item["label"]).strip()
		archetype_state = (
			item.archetype_state if hasattr(item, "archetype_state") else item.get("archetype_state")
		)
		# Default sequence to array order if not explicitly given
		sequence = (item.sequence if hasattr(item, "sequence") else item.get("sequence")) or (idx + 1)
		external_code = item.external_code if hasattr(item, "external_code") else item.get("external_code")
		description = item.description if hasattr(item, "description") else item.get("description")

		if stg_id and stg_id in existing_by_stage_id:
			# Existing stage: Update
			kept_stage_ids.add(stg_id)
			doc = frappe.get_doc("A2C Loan Status Stage", existing_by_stage_id[stg_id].name)
			old_label = doc.label

			doc.label = label
			if archetype_state:
				doc.archetype_state = archetype_state
			doc.sequence = sequence
			if external_code is not None:
				doc.external_code = external_code
			if description is not None:
				doc.description = description
			doc.save(ignore_permissions=False)

			# Keep denormalized stage_label on existing loans in sync
			if old_label != label:
				frappe.db.sql(
					"""
					UPDATE `tabA2C Loan Application`
					SET `stage_label` = %(new_label)s
					WHERE `bank` = %(bank)s AND `stage_id` = %(stage_id)s
					""",
					{"new_label": label, "bank": effective_bank, "stage_id": stg_id},
				)
		else:
			# New stage within sync list
			if not archetype_state:
				archetype_state = "In Transition"
			new_doc = frappe.get_doc(
				{
					"doctype": "A2C Loan Status Stage",
					"bank": effective_bank,
					"label": label,
					"archetype_state": archetype_state,
					"sequence": sequence,
					"external_code": external_code,
					"description": description,
				}
			)
			new_doc.insert(ignore_permissions=False)
			kept_stage_ids.add(new_doc.stage_id)

	# Delete stages that were removed from the pipeline
	for stg_id, stage_info in existing_by_stage_id.items():
		if stg_id not in kept_stage_ids:
			doc = frappe.get_doc("A2C Loan Status Stage", stage_info.name)
			# doc.delete() will run on_trash which throws if applications are using this stage
			doc.delete(ignore_permissions=False)

	# Return the refreshed stages list
	return get_stages(bank=effective_bank)
