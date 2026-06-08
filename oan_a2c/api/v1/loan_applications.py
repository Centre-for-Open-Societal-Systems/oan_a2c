import frappe
from frappe.utils import cint
import json

def _get_app(application_id):
    if not frappe.db.exists("A2C Loan Application", application_id):
        frappe.throw("Loan Application not found", frappe.DoesNotExistError)
    return frappe.get_doc("A2C Loan Application", application_id)

@frappe.whitelist()
def get_loan_summary():
    try:
        if not frappe.has_permission("A2C Loan Application", "read"):
            return {"status": "error", "message": "Not permitted to view Loan Applications"}

        counts = frappe.get_all(
            "A2C Loan Application",
            fields=["status", {"COUNT": "*"}],
            group_by="status"
        )
        
        summary = {"total": 0, "processing": 0, "approved": 0, "rejected": 0}
        for row in counts:
            count = row.get("COUNT(*)", 0)
            summary["total"] += count
            if row.status in ["Under Review", "Submitted", "In Progress"]:
                summary["processing"] += count
            elif row.status == "Approved":
                summary["approved"] += count
            elif row.status == "Rejected":
                summary["rejected"] += count
        
        return {
            "status": "success",
            "summary": summary
        }
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Get Loan Summary Error")
        return {"status": "error", "message": str(e)}

@frappe.whitelist()
def get_loan_assignment_summary():
    try:
        if not frappe.has_permission("A2C Loan Application", "read"):
            return {"status": "error", "message": "Not permitted to view Loan Applications"}

        user = frappe.session.user

        total = frappe.db.count("A2C Loan Application")
        my_applications = frappe.db.count("A2C Loan Application", {"loan_officer": user})
        unassigned = frappe.db.count("A2C Loan Application", {"loan_officer": ["in", ["", None]]})

        return {
            "status": "success",
            "summary": {
                "all_applications": total,
                "my_applications": my_applications,
                "unassigned": unassigned
            }
        }
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Get Loan Assignment Summary Error")
        return {"status": "error", "message": str(e)}

@frappe.whitelist()
def get_loan_metadata():
    try:
        if not frappe.has_permission("A2C Loan Application", "read"):
            return {"status": "error", "message": "Not permitted"}
            
        meta = frappe.get_meta("A2C Loan Application")
        status_field = meta.get_field("status")
        type_field = meta.get_field("loan_type")
        
        statuses = status_field.options.split("\n") if status_field and status_field.options else []
        loan_types = type_field.options.split("\n") if type_field and type_field.options else []
        
        return {
            "status": "success",
            "statuses": statuses,
            "loan_types": loan_types
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

@frappe.whitelist()
def get_all_loans(status=None, loan_amount=None, min_loan_amount=None, max_loan_amount=None, loan_type=None, location=None, from_date=None, to_date=None, page=1, page_size=20):
    try:
        if not frappe.has_permission("A2C Loan Application", "read"):
            return {"status": "error", "message": "Not permitted to view Loan Applications"}

        page = cint(page) or 1
        page_size = cint(page_size) or 20
        offset = (page - 1) * page_size

        filters = {}

        if status:
            filters['status'] = status
        
        if min_loan_amount and max_loan_amount:
            filters['loan_amount'] = ("between", [min_loan_amount, max_loan_amount])
        elif min_loan_amount:
            filters['loan_amount'] = (">=", min_loan_amount)
        elif max_loan_amount:
            filters['loan_amount'] = ("<=", max_loan_amount)
        elif loan_amount:
            filters['loan_amount'] = loan_amount

        if loan_type:
            filters['loan_type'] = loan_type

        if location:
            filters['location'] = ("like", f"%{location}%")

        if from_date and to_date:
            filters['creation'] = ("between", [from_date, f"{to_date} 23:59:59"])
        elif from_date:
            filters['creation'] = (">=", from_date)
        elif to_date:
            filters['creation'] = ("<=", f"{to_date} 23:59:59")

        total_records = frappe.db.count("A2C Loan Application", filters=filters)

        records = frappe.get_all(
            "A2C Loan Application",
            filters=filters,
            fields=["name as application_id", "first_name", "last_name", "phone_number", 
                    "status", "loan_amount", "loan_type", "location", "creation"],
            order_by="creation DESC",
            limit_start=offset,
            limit_page_length=page_size
        )

        return {
            "status": "success",
            "results": records,
            "total": total_records,
            "page": page,
            "page_size": page_size
        }
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Get All Loans Error")
        return {"status": "error", "message": str(e)}

@frappe.whitelist()
def get_basic_profile(lead_id):
    try:
        app_names = frappe.get_all("A2C Loan Application", filters={"lead_id": lead_id}, pluck="name", limit=1)
        if not app_names:
            return {"status": "error", "message": "Loan Application not found for this lead"}
        doc = frappe.get_doc("A2C Loan Application", app_names[0])
        return {
            "status": "success",
            "data": {
                "first_name": doc.first_name,
                "last_name": doc.last_name,
                "phone_number": doc.phone_number,
                "email": doc.email,
                "location": doc.location
            }
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

@frappe.whitelist()
def get_full_profile(lead_id):
    try:
        app_names = frappe.get_all("A2C Loan Application", filters={"lead_id": lead_id}, pluck="name", limit=1)
        if not app_names:
            return {"status": "error", "message": "Loan Application not found for this lead"}
        doc = frappe.get_doc("A2C Loan Application", app_names[0])
        
        excluded_fields = [
            'loan_amount', 'loan_type', 'loan_reason', 'status', 
            'current_step', 'loan_officer', 'application_id'
        ]
        
        data = doc.as_dict()
        filtered_data = {
            k: v for k, v in data.items() 
            if k not in excluded_fields and not k.startswith('_')
        }
        
        return {
            "status": "success",
            "data": filtered_data
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

@frappe.whitelist()
def get_credit_info(application_id):
    try:
        doc = _get_app(application_id)
        return {
            "status": "success",
            "data": {
                "loan_amount": doc.loan_amount,
                "loan_type": doc.loan_type,
                "loan_reason": doc.loan_reason,
                "status": doc.status
            }
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

@frappe.whitelist(methods=["POST"])
def edit_credit_info(**kwargs):
    try:
        data = kwargs
        application_id = data.get("application_id")
        if not application_id:
            return {"status": "error", "message": "application_id is required"}

        doc = _get_app(application_id)
        
        if "loan_amount" in data: doc.loan_amount = data["loan_amount"]
        if "loan_type" in data: doc.loan_type = data["loan_type"]
        if "loan_reason" in data: doc.loan_reason = data["loan_reason"]
        if "status" in data: doc.status = data["status"]

        doc.save(ignore_permissions=True)
        frappe.db.commit()

        return {
            "status": "success",
            "message": "Credit info updated successfully"
        }
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Edit Credit Info Error")
        return {"status": "error", "message": str(e)}

@frappe.whitelist(methods=["POST"])
def upload_supporting_documents(application_id):
    try:
        if not frappe.request.files:
            return {"status": "error", "message": "No files found in request"}
            
        doc = _get_app(application_id)
        uploaded_files = []
        
        ALLOWED_EXTENSIONS = ('.pdf', '.png', '.jpg', '.jpeg')
        MAX_FILE_SIZE = 5 * 1024 * 1024 # 5 MB

        for key, file_storage in frappe.request.files.items():
            filename = file_storage.filename.lower()
            if not filename.endswith(ALLOWED_EXTENSIONS):
                return {"status": "error", "message": f"Invalid file type for {filename}. Only PDF, PNG, and JPG are allowed."}
                
            content = file_storage.read()
            if len(content) > MAX_FILE_SIZE:
                return {"status": "error", "message": f"File {filename} exceeds the 5MB size limit."}
                
            file_doc = frappe.get_doc({
                "doctype": "File",
                "file_name": file_storage.filename,
                "content": content,
                "attached_to_doctype": "A2C Loan Application",
                "attached_to_name": doc.name,
                "is_private": 1
            })
            file_doc.insert(ignore_permissions=True)
            uploaded_files.append({
                "file_url": file_doc.file_url,
                "file_name": file_doc.file_name
            })

        frappe.db.commit()
        return {"status": "success", "files": uploaded_files}

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Upload Documents Error")
        return {"status": "error", "message": str(e)}

@frappe.whitelist()
def get_supporting_documents(application_id):
    try:
        files = frappe.get_all(
            "File",
            filters={
                "attached_to_doctype": "A2C Loan Application",
                "attached_to_name": application_id
            },
            fields=["file_name", "file_url", "creation"]
        )
        return {
            "status": "success",
            "files": files
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}
