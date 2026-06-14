import frappe
from frappe import _
from functools import wraps

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
        except frappe.DoesNotExistError:
            frappe.local.message_log = []
            frappe.response.status_code = 404
            return error_response("Resource not found", "NOT_FOUND")
        except frappe.ValidationError as e:
            frappe.local.message_log = []
            frappe.response.status_code = 400
            return error_response(str(e), "VALIDATION_ERROR")
        except Exception as e:
            frappe.local.message_log = []
            frappe.log_error(frappe.get_traceback(), f"API Error in {func.__name__}")
            frappe.response.status_code = 500
            return error_response("An unexpected error occurred", "INTERNAL_ERROR")
    return wrapper

