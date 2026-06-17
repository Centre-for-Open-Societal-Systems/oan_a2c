import frappe
from frappe import _
from functools import wraps

def parse_multi_value(value, allowed=None):
    """Split a single value or comma-separated string into a de-duplicated list.

    - Accepts a string ("a,b"), a list/tuple, or None.
    - When `allowed` is provided (a collection), values not in it are silently dropped.
    - When `allowed` is None, all non-empty values are kept (use for free-text fields).
    - Order is preserved; duplicates are removed. Returns [] when nothing valid remains.
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        requested = [str(v).strip() for v in value]
    else:
        requested = [v.strip() for v in str(value).split(",")]
    seen = set()
    result = []
    for v in requested:
        if not v or v in seen:
            continue
        if allowed is not None and v not in allowed:
            continue
        seen.add(v)
        result.append(v)
    return result

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
    if pagination:
        res["pagination"] = pagination
    return res

def error_response(message, code="GENERIC_ERROR", details=None):
    return {
        "status": "error",
        "message": message,
        "code": code,
        "details": details or {},
    }

def handle_api_errors(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            res = func(*args, **kwargs)
            
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
            frappe.response.status_code = 403
            return error_response("Permission denied", "PERMISSION_DENIED")
        except frappe.AuthenticationError as e:
            frappe.local.message_log = []
            frappe.response.status_code = 401
            return error_response(str(e) or "Authentication failed", "AUTHENTICATION_ERROR")
        except frappe.DoesNotExistError as e:
            error_msg = str(e)
            
            # Extract Frappe's real error message from message_log if it exists
            messages = getattr(frappe.local, 'message_log', [])
            if messages:
                import json
                parsed_msgs = []
                for m in messages:
                    try:
                        parsed = json.loads(m)
                        if isinstance(parsed, dict) and "message" in parsed:
                            parsed_msgs.append(str(parsed["message"]))
                        else:
                            parsed_msgs.append(str(m))
                    except Exception:
                        parsed_msgs.append(str(m))
                
                if parsed_msgs:
                    error_msg = " | ".join(parsed_msgs)

            frappe.local.message_log = []
            frappe.response.status_code = 404
            return error_response(error_msg or "Resource not found", "NOT_FOUND")
        except (frappe.ValidationError, getattr(frappe, 'MandatoryError', Exception), getattr(frappe, 'UniqueValidationError', Exception), getattr(frappe, 'DuplicateEntryError', Exception), getattr(frappe, 'DataError', Exception)) as e:
            error_msg = str(e)
            
            # Extract Frappe's real error message from message_log if it exists
            messages = getattr(frappe.local, 'message_log', [])
            if messages:
                import json
                parsed_msgs = []
                for m in messages:
                    try:
                        parsed = json.loads(m)
                        if isinstance(parsed, dict) and "message" in parsed:
                            parsed_msgs.append(str(parsed["message"]))
                        else:
                            parsed_msgs.append(str(m))
                    except Exception:
                            parsed_msgs.append(str(m))
                
                if parsed_msgs:
                    error_msg = " | ".join(parsed_msgs)

            frappe.local.message_log = []
            frappe.response.status_code = 400
            return error_response(error_msg or "Validation Error", "VALIDATION_ERROR")
        except Exception as e:
            frappe.local.message_log = []
            frappe.log_error(frappe.get_traceback(), f"API Error in {func.__name__}")
            frappe.response.status_code = 500
            return error_response("An unexpected error occurred", "INTERNAL_ERROR")
    return wrapper

