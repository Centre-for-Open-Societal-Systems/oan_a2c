import frappe
from frappe import _
from functools import wraps
from pydantic import BaseModel, BeforeValidator, ValidationError as PydanticValidationError
from typing import Annotated, Optional
import inspect

class _DummyException(Exception):
    pass


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
                return error_response(
                    message="Validation failed",
                    code="VALIDATION_ERROR",
                    details=errors
                )
            
            # The native ** unpacking operator automatically maps to named parameters
            # or collects into **kwargs, depending on the decorated function's signature.
            validated_dict = validated.model_dump()
            return func(**validated_dict)
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
        if v_str.startswith('[') and v_str.endswith(']'):
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
                _("Invalid value '{0}'. Allowed values: {1}").format(v, allowed_list),
                frappe.ValidationError
            )
        seen.add(v)
        result.append(v)
    return result

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

SafeDate = Annotated[Optional[str], BeforeValidator(validate_date_string)]
SafeEmail = Annotated[Optional[str], BeforeValidator(validate_email_string)]

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

def error_response(message, code="GENERIC_ERROR", details=None):
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

def extract_message_from_str(val):
    if val.startswith("{") and val.endswith("}"):
        try:
            import json
            import ast
            try:
                parsed = json.loads(val)
            except Exception:
                parsed = ast.literal_eval(val)
            if isinstance(parsed, dict) and "message" in parsed:
                return str(parsed["message"])
        except Exception:
            pass
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

    messages = getattr(frappe.local, 'message_log', [])
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
                req_id = frappe.request.headers.get("X-Request-Id") or frappe.request.environ.get("REQUEST_ID")
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
        except frappe.PermissionError:
            frappe.local.message_log = []
            frappe.response["http_status_code"] = 403
            return error_response("Permission denied", "PERMISSION_DENIED")
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
            return error_response(
                message="Validation failed",
                code="VALIDATION_ERROR",
                details=errors
            )
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
        except (getattr(frappe, 'MandatoryError', _DummyException), 
                getattr(frappe, 'UniqueValidationError', _DummyException), 
                getattr(frappe, 'DuplicateEntryError', _DummyException), 
                getattr(frappe, 'DataError', _DummyException)) as e:
            import json
            log_title = f"DB/Constraint Error | {func.__name__}"
            log_message = json.dumps({
                "request_id": getattr(frappe.local, "request_id", None),
                "endpoint": func.__name__,
                "user": frappe.session.user if frappe.session else None,
                "traceback": frappe.get_traceback(),
                "exception": str(e)
            }, indent=2)
            frappe.log_error(title=log_title, message=log_message)
            frappe.local.message_log = []
            frappe.response["http_status_code"] = 400
            return error_response("Database constraint or data validation error occurred", "VALIDATION_ERROR")
        except Exception as e:
            import json
            log_title = f"API Error | {func.__name__}"
            log_message = json.dumps({
                "request_id": getattr(frappe.local, "request_id", None),
                "endpoint": func.__name__,
                "user": frappe.session.user if frappe.session else None,
                "traceback": frappe.get_traceback(),
                "exception": str(e)
            }, indent=2)
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

    doc = apply_workflow(doc, action)

    # apply_workflow moves `workflow_state` but not the separate `status` Select field that the
    # rest of the app (lists, summaries, filters) reads. Mirror the new state onto `status` so
    # the two stay in lockstep. update_modified is left default so the change is timestamped.
    if doc.get("status") != doc.workflow_state:
        doc.db_set("status", doc.workflow_state)

    return doc


