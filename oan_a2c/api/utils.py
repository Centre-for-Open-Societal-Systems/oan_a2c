import inspect
from functools import wraps
from typing import Annotated

import frappe  # pyright: ignore[reportMissingImports]
from frappe import _  # pyright: ignore[reportMissingImports]
from frappe.utils import flt  # pyright: ignore[reportMissingImports]
from pydantic import BaseModel, BeforeValidator
from pydantic import ValidationError as PydanticValidationError

from oan_a2c.a2c_marketplace.permissions import (  # pyright: ignore[reportMissingTypeStubs]
	BankNotActive,
	BankNotOnboarded,
)


class _DummyException(Exception):
	pass


class PasswordChangeRequired(frappe.AuthenticationError):
	"""Raised when an admin-issued temporary password must be rotated before use.

	Subclasses AuthenticationError so it still fails closed everywhere a plain
	auth failure would, but handle_api_errors catches it first to return a
	distinct PASSWORD_CHANGE_REQUIRED code and a 403 rather than a 401: the
	credentials were correct, the account is simply gated behind an action the
	user can take. A 401 would also read to the client as an expired session and
	bounce the user to a global logout instead of the set-password step.
	"""


def validate_password_complexity(value: str) -> str:
	"""Shared password rule: at least one letter, one digit and one symbol.

	Three schemas (registration, first-login, change-password) enforce the same
	rule; defining it once keeps the requirement — and the error strings the
	frontend shows — from drifting apart between them.
	"""
	if not any(c.isalpha() for c in value):
		raise ValueError("Password must contain at least one letter.")
	if not any(c.isdigit() for c in value):
		raise ValueError("Password must contain at least one number.")
	if not any(not c.isalnum() for c in value):
		raise ValueError("Password must contain at least one special character.")
	return value


def validate_request(schema: type[BaseModel]):
	"""Decorator to validate whitelisted API inputs using a Pydantic schema.

	Parses, casts types, and validates the inputs.
	Returns a standardized error response if validation fails.
	"""

	def decorator(func):
		@wraps(func)
		def wrapper(*args, **kwargs):
			sig = inspect.signature(func)
			bound = sig.bind_partial(*args, **kwargs)
			bound.apply_defaults()

			params = {}
			for k, v in bound.arguments.items():
				if k == "kwargs" and isinstance(v, dict):
					params.update(v)
				else:
					params[k] = v

			try:
				validated = schema(**params)
			except PydanticValidationError as e:
				errors = {}
				for err in e.errors():
					loc = ".".join(str(loc_item) for loc_item in err["loc"])
					errors[loc] = err["msg"]

				frappe.response["http_status_code"] = 400
				frappe.local.message_log = []
				return error_response(message="Validation failed", code="VALIDATION_ERROR", details=errors)

			# The native ** unpacking operator automatically maps to named parameters
			# or collects into **kwargs, depending on the decorated function's signature.
			validated_dict = validated.model_dump()
			return func(**validated_dict)

		return wrapper

	return decorator


def require_role(roles: list[str]):
	"""Decorator that enforces the caller holds at least one of `roles`. Must sit below @handle_api_errors."""

	def decorator(fn):
		@wraps(fn)
		def wrapper(*args, **kwargs):
			user_doc = frappe.get_doc("User", frappe.session.user)
			if not any(d.role in roles for d in user_doc.roles):
				roles_str = ", ".join(roles)
				frappe.throw(_("Only {0} can perform this action.").format(roles_str), frappe.PermissionError)
			return fn(*args, **kwargs)

		return wrapper

	return decorator


def parse_multi_value(value, allowed=None):
	"""Split a single value or comma-separated string into a de-duplicated list.

	- Accepts a string ("a,b"), a list/tuple, or None.
	- When `allowed` is provided (a collection), values not in it raise a ValidationError.
	- When `allowed` is None, all non-empty values are kept (use for free-text fields).
	- Order is preserved; duplicates are removed.
	"""
	if value is None:
		return []
	if isinstance(value, (list, tuple)):
		requested = [str(v).strip() for v in value]
	else:
		v_str = str(value).strip()
		if v_str.startswith("[") and v_str.endswith("]"):
			try:
				parsed = frappe.parse_json(v_str)
				requested = [str(v).strip() for v in parsed] if isinstance(parsed, list) else [v_str]
			except Exception:
				requested = [v.strip() for v in v_str.split(",")]
		else:
			requested = [v.strip() for v in v_str.split(",")]
	seen = set()
	result = []
	for v in requested:
		if not v or v in seen:
			continue
		if allowed is not None and v not in allowed:
			allowed_list = ", ".join(str(a) for a in allowed)
			frappe.throw(
				_("Invalid value '{0}'. Allowed values: {1}").format(v, allowed_list), frappe.ValidationError
			)
		seen.add(v)
		result.append(v)
	return result


def to_tz_aware_iso(dt):
	"""Convert a naive Frappe datetime to a timezone-aware ISO 8601 string in the system timezone."""
	if not dt:
		return None
	import pytz
	from frappe.utils import get_datetime, get_system_timezone

	dt_obj = get_datetime(dt)
	system_tz = pytz.timezone(get_system_timezone())
	return system_tz.localize(dt_obj).isoformat()


def from_tz_aware_iso(value):
	"""Inverse of to_tz_aware_iso: parse an ISO 8601 string (tz-aware or naive)
	into a naive datetime in the system timezone.

	Frappe Datetime fields map to MariaDB `datetime` columns, which reject
	tz-aware ISO strings like '2026-08-04T11:05:27+05:30' (the 'T' separator and
	offset are invalid). Use this before writing an ISO timestamp produced by
	to_tz_aware_iso (e.g. a webhook `published_at`) into a Datetime field.
	"""
	if not value:
		return None

	import pytz
	from frappe.utils import get_datetime, get_system_timezone

	if isinstance(value, str):
		from datetime import datetime

		try:
			dt_obj = datetime.fromisoformat(value)
		except ValueError:
			# Fall back to Frappe's tolerant parser for non-ISO inputs.
			return get_datetime(value)
	else:
		dt_obj = value

	if dt_obj.tzinfo is not None:
		system_tz = pytz.timezone(get_system_timezone())
		dt_obj = dt_obj.astimezone(system_tz).replace(tzinfo=None)
	return dt_obj


def validate_date_string(v):
	"""Validate that a string represents a valid date or datetime."""
	if v:
		try:
			import datetime

			try:
				datetime.date.fromisoformat(v)
			except ValueError:
				datetime.datetime.fromisoformat(v)
		except ValueError:
			raise ValueError("Invalid date format. Expected YYYY-MM-DD or ISO 8601 string.")
	return v


def validate_email_string(v):
	"""Validate that a string represents a valid email address."""
	if v:
		from frappe.utils import validate_email_address

		if not validate_email_address(v):
			raise ValueError("Invalid email address format")
	return v


def validate_phone_string(v):
	"""Validate and normalize a phone number.

	Single source of truth for phone validation across every API schema. Accepts
	both international (`+251912345678`) and local (`0912345678`) formats, and
	tolerates spaces/dashes/parens in the input (common when users paste). The
	rule is on the digit count, not the punctuation:

	  - strip everything except digits and a single leading '+'
	  - require 10-15 digits
	  - a leading 0 (local format) is allowed

	Returns the normalized value (separators removed, '+' preserved if present).
	Passes through None/empty so it composes with optional fields; required-ness
	is enforced by the RequiredPhone variant.
	"""
	if v is None or v == "":
		return v
	import re

	raw = str(v).strip()
	has_plus = raw.startswith("+")
	digits = re.sub(r"\D", "", raw)

	if not (10 <= len(digits) <= 15):
		raise ValueError("Phone number must contain between 10 and 15 digits.")
	# First significant digit can't be 0 for an international number; a local
	# number may legitimately start with 0 (e.g. 0912345678).
	if has_plus and digits.startswith("0"):
		raise ValueError("An international (+) phone number cannot start with 0.")

	return f"+{digits}" if has_plus else digits


def validate_required_phone_string(v):
	"""Like validate_phone_string but rejects a missing/empty value.

	Use for phone fields that are mandatory (e.g. lead / onboarding create),
	so the same format rule applies and absence is a validation error, not a pass.
	"""
	if v is None or str(v).strip() == "":
		raise ValueError("Phone number is required.")
	return validate_phone_string(v)


SafeDate = Annotated[str | None, BeforeValidator(validate_date_string)]
SafeEmail = Annotated[str | None, BeforeValidator(validate_email_string)]
SafePhone = Annotated[str | None, BeforeValidator(validate_phone_string)]
RequiredPhone = Annotated[str, BeforeValidator(validate_required_phone_string)]


def assert_amount_within_product_range(amount, min_amount=None, max_amount=None):
	"""Reject a loan amount the chosen product cannot actually offer.

	The bound is per-product, so no request schema can express it -- a global
	`le=` only says what the platform permits, not what this bank offers. Shared by
	every path that attaches an amount to a product (the Development Agent's
	create_loan_application and the farmer's self-service create_application) so the
	two cannot drift.

	Args:
		amount: the requested/credit-information amount, already cast to a number.
		min_amount: product floor; falsy (None/0) means no floor.
		max_amount: product ceiling; falsy (None/0) means no ceiling.

	Raises:
		frappe.ValidationError: when the amount falls outside the product's range.
	"""
	amount = flt(amount)
	if max_amount and amount > flt(max_amount):
		frappe.throw(
			_("Requested amount exceeds the maximum of {0} for this product.").format(flt(max_amount)),
			frappe.ValidationError,
		)
	if min_amount and amount < flt(min_amount):
		frappe.throw(
			_("Requested amount is below the minimum of {0} for this product.").format(flt(min_amount)),
			frappe.ValidationError,
		)


def success_response(data=None, message="Success", meta=None, pagination=None):
	"""
	Developer-facing payload builder. Decoupled from the final JSON envelope.
	Provides IDE autocomplete and contract enforcement for API endpoints.
	"""
	return {
		"data": data,
		"message": message,
		"meta": meta,
		"pagination": pagination,
	}


def _envelope_success(data=None, message="Success", meta=None, pagination=None):
	res = {
		"status": "success",
		"message": message,
		"data": data,
		"meta": meta or {},
	}
	req_id = getattr(frappe.local, "request_id", None)
	if req_id:
		res["request_id"] = req_id
	if pagination:
		res["pagination"] = pagination
	return res


def error_response(message, code="GENERIC_ERROR", details=None):  # pyright: ignore[reportUnknownParameterType, reportMissingParameterType]
	res = {
		"status": "error",
		"message": message,
		"code": code,
		"details": details or {},
	}
	req_id = getattr(frappe.local, "request_id", None)
	if req_id:
		res["request_id"] = req_id
	return res


def check_rate_limit(key: str, limit: int, window: int):
	"""
	Apply rate limits using Redis counter.
	key    — unique per user+endpoint
	limit  — max calls allowed in window
	window — seconds
	"""
	cache = frappe.cache()
	count = cache.get_value(key) or 0

	if int(count) >= limit:
		frappe.response.status_code = 429
		frappe.throw(_("Rate limit exceeded. Try again later."), frappe.ValidationError)

	pipeline = cache.pipeline()
	pipeline.incr(key)
	pipeline.expire(key, window)
	pipeline.execute()


def extract_message_from_str(val):
	if isinstance(val, str) and val.startswith("{") and val.endswith("}"):
		try:
			import ast
			import json

			try:
				parsed = json.loads(val)
			except Exception:
				parsed = ast.literal_eval(val)
			if isinstance(parsed, dict) and "message" in parsed:
				val = str(parsed["message"])
		except Exception:
			# Best-effort extraction only; unparseable input falls through to the
			# raw value. Debug level so it's available when troubleshooting but
			# doesn't add noise (this fires on any non-dict-shaped string).
			frappe.logger().debug("Could not parse message payload; returning raw value")

	if isinstance(val, str) and "<" in val and ">" in val:
		import re

		val = re.sub(r"<[^>]+>", "", val).strip()
	return val


def get_error_message(e, default_msg="Validation Error"):
	error_msg = ""
	if hasattr(e, "args") and e.args:
		first_arg = e.args[0]
		if isinstance(first_arg, dict):
			error_msg = first_arg.get("message") or str(first_arg)
		elif isinstance(first_arg, str):
			error_msg = extract_message_from_str(first_arg)
		else:
			error_msg = str(first_arg)
	else:
		error_msg = str(e)

	error_msg = extract_message_from_str(error_msg)

	messages = getattr(frappe.local, "message_log", [])
	if messages:
		parsed_msgs = []
		for m in messages:
			if isinstance(m, dict):
				msg_str = m.get("message") or str(m)
			elif isinstance(m, str):
				msg_str = extract_message_from_str(m)
			else:
				msg_str = str(m)
			if msg_str:
				parsed_msgs.append(msg_str)
		if parsed_msgs:
			return " | ".join(parsed_msgs)
	return error_msg or default_msg


def handle_api_errors(func):
	@wraps(func)
	def wrapper(*args, **kwargs):
		if not getattr(frappe.local, "request_id", None):
			req_id = None
			if frappe.request:
				headers = getattr(frappe.request, "headers", None)
				environ = getattr(frappe.request, "environ", None)
				req_id = (headers.get("X-Request-Id") if headers else None) or (
					environ.get("REQUEST_ID") if environ else None
				)
			if not req_id:
				import uuid

				req_id = str(uuid.uuid4())
			frappe.local.request_id = req_id

		try:
			res = func(*args, **kwargs)

			# Bypass JSON envelope wrapping for binary/file download responses
			if getattr(frappe.local, "response", None) and frappe.local.response.get("type") == "download":
				return res

			message = "Success"
			pagination = None
			meta = None
			data = res

			if isinstance(res, dict) and "data" in res:
				data = res["data"]
				message = res.get("message", "Success")
				pagination = res.get("pagination")
				meta = res.get("meta")

			return _envelope_success(data=data, message=message, pagination=pagination, meta=meta)
		except BankNotOnboarded:
			# Distinct from a plain 403: the user is authenticated and role-correct
			# but their bank registration isn't complete, so guide them to finish it.
			frappe.local.message_log = []
			frappe.response["http_status_code"] = 403
			return error_response(
				"Your bank registration is not complete. Finish onboarding to access this resource.",
				"BANK_NOT_ONBOARDED",
			)
		except BankNotActive as e:
			# Distinct from a plain 403: the user's bank exists but isn't Active yet
			# (In Review/Suspended), so tell them to complete KYC / await approval
			# rather than returning an opaque "Permission denied".
			frappe.local.message_log = []
			frappe.response["http_status_code"] = 403
			return error_response(
				str(e) or "Your bank is not active yet. Complete onboarding to manage products.",
				"BANK_NOT_ACTIVE",
			)
		except frappe.PermissionError:
			frappe.local.message_log = []
			frappe.response["http_status_code"] = 403
			return error_response("Permission denied", "PERMISSION_DENIED")
		except PasswordChangeRequired as e:
			# Must precede the AuthenticationError branch below — this subclasses it.
			# Distinct from a 401: the credentials were correct, but the password was
			# issued by an admin and has to be rotated before any session exists.
			frappe.local.message_log = []
			frappe.response["http_status_code"] = 403
			return error_response(
				str(e) or "You must set your own password before signing in.",
				"PASSWORD_CHANGE_REQUIRED",
			)
		except frappe.AuthenticationError as e:
			frappe.local.message_log = []
			frappe.response["http_status_code"] = 401
			return error_response(str(e) or "Authentication failed", "AUTHENTICATION_ERROR")
		except PydanticValidationError as e:
			errors = {}
			for err in e.errors():
				loc = ".".join(str(loc_item) for loc_item in err["loc"])
				errors[loc] = err["msg"]
			frappe.local.message_log = []
			frappe.response["http_status_code"] = 400
			return error_response(message="Validation failed", code="VALIDATION_ERROR", details=errors)
		except frappe.DoesNotExistError as e:
			error_msg = get_error_message(e, "Resource not found")
			frappe.local.message_log = []
			frappe.response["http_status_code"] = 404
			return error_response(error_msg, "NOT_FOUND")
		except frappe.ValidationError as e:
			error_msg = get_error_message(e, "Validation Error")
			frappe.local.message_log = []
			frappe.response["http_status_code"] = 400
			return error_response(error_msg, "VALIDATION_ERROR")
		except (
			getattr(frappe, "MandatoryError", _DummyException),
			getattr(frappe, "UniqueValidationError", _DummyException),
			getattr(frappe, "DuplicateEntryError", _DummyException),
			getattr(frappe, "DataError", _DummyException),
		) as e:
			import json

			log_title = f"DB/Constraint Error | {func.__name__}"
			log_message = json.dumps(
				{
					"request_id": getattr(frappe.local, "request_id", None),
					"endpoint": func.__name__,
					"user": frappe.session.user if frappe.session else None,
					"traceback": frappe.get_traceback(),
					"exception": str(e),
				},
				indent=2,
			)
			frappe.log_error(title=log_title, message=log_message)
			frappe.local.message_log = []
			frappe.response["http_status_code"] = 400
			error_msg = get_error_message(e, "Database constraint or data validation error occurred")
			return error_response(error_msg, "VALIDATION_ERROR")
		except Exception as e:
			import json

			log_title = f"API Error | {func.__name__}"
			log_message = json.dumps(
				{
					"request_id": getattr(frappe.local, "request_id", None),
					"endpoint": func.__name__,
					"user": frappe.session.user if frappe.session else None,
					"traceback": frappe.get_traceback(),
					"exception": str(e),
				},
				indent=2,
			)
			frappe.log_error(title=log_title, message=log_message)
			frappe.local.message_log = []
			frappe.response["http_status_code"] = 500
			return error_response("An unexpected error occurred", "INTERNAL_ERROR")

	return wrapper


# --- Workflow helpers ------------------------------------------------------
#
# The A2C Lead / A2C Loan Application status fields are governed by Frappe
# Workflows (see development/workflow_design_lead_loan.md). Status can only
# change via apply_workflow(doc, action), which validates the transition is
# legal from the current state and allowed for the user's role.
#
# To keep the existing API contract unchanged, the status-update endpoints still
# accept a *target status*; we map (current_state -> target_status) to the
# workflow *action* and apply it. The map below is derived directly from the
# transition tables in the design doc.

# (current_workflow_state, target_status) -> action name
_WORKFLOW_TRANSITION_ACTIONS = {
	"A2C Lead": {
		("Active", "Verified"): "Verify",
		("Verified", "Processed"): "Mark Processed",
		("Processed", "Granted"): "Grant",
		("Processed", "Rejected"): "Reject",
		("Active", "Rejected"): "Reject",
		("Verified", "Rejected"): "Reject",
		("Active", "Dormant"): "Mark Dormant",
		("Verified", "Dormant"): "Mark Dormant",
		("Dormant", "Active"): "Reactivate",
	},
	"A2C Loan Application": {
		("Draft", "Processing"): "Send for Review",
		("Processing", "Approved"): "Approve",
		("Processing", "Rejected"): "Reject",
	},
}


def apply_status_transition(doc, target_status):
	"""
	Move `doc` to `target_status` through its workflow.

	Resolves the workflow action for (current_state -> target_status) and calls
	apply_workflow, which enforces legality + role permissions. Raises
	frappe.ValidationError with a clear message if the transition is not allowed
	from the current state (mirroring the old imperative "status is locked" /
	"invalid status" errors). No-op if already in the target state.
	"""
	from frappe.model.workflow import apply_workflow

	current = doc.get("workflow_state") or doc.get("status")
	if current == target_status:
		return doc

	action = _WORKFLOW_TRANSITION_ACTIONS.get(doc.doctype, {}).get((current, target_status))
	if not action:
		frappe.throw(
			_("Cannot change status from '{0}' to '{1}'.").format(current, target_status),
			frappe.ValidationError,
		)

	# The workflow engine (apply_workflow) and the db_set mirror below both
	# BYPASS Document.before_save, so the doctype's own verification gate never
	# runs on this path. Enforce the business prerequisites here, before the
	# transition, so a lead cannot become Verified without credit info + an
	# approved consent regardless of workflow/role permissions.
	if doc.doctype == "A2C Lead" and target_status == "Verified":
		doc._enforce_verification_prerequisites()

	doc = apply_workflow(doc, action)

	# apply_workflow moves `workflow_state` but not the separate `status` Select field that the
	# rest of the app (lists, summaries, filters) reads. Mirror the new state onto `status` so
	# the two stay in lockstep. update_modified is left default so the change is timestamped.
	if doc.get("status") != doc.workflow_state:
		doc.db_set("status", doc.workflow_state)

	return doc


def notify_lead_event(lead_id, subject, message=None, notification_type="Alert"):
	"""Create a persistent Notification Log for a lead-related webhook event.

	Targets the lead's assigned agent (falls back to no-op if unassigned) so the
	event shows up in the recipient's notification bell. This both persists to the
	DB and pushes realtime via Frappe's Notification Log, so the bell updates live
	and survives a reload.

	Best-effort: never raises, so an inbound webhook is not failed just because a
	notification could not be created.
	"""
	try:
		if not lead_id:
			return
		for_user = frappe.db.get_value("A2C Lead", lead_id, "assigned_to")
		if not for_user:
			# No assigned agent to notify; nothing to do.
			return
		frappe.get_doc(
			{
				"doctype": "Notification Log",
				"for_user": for_user,
				"type": notification_type,
				"subject": subject,
				"email_content": message or subject,
				"document_type": "A2C Lead",
				"document_name": lead_id,
			}
		).insert(ignore_permissions=True)
	except Exception:
		# Notifications are non-critical; log and move on.
		frappe.logger().warning(f"Could not create Notification Log for lead {lead_id}")
