import frappe
from frappe.utils import cint
import json

def _get_app(application_id):
    if not frappe.db.exists("A2C Loan Application", application_id):
        frappe.throw("Loan Application not found", frappe.DoesNotExistError)
    return frappe.get_doc("A2C Loan Application", application_id)

@frappe.whitelist()
def get_basic_profile(lead_id):
    try:
        lead_doc = frappe.get_doc("A2C Lead", lead_id)
        if not lead_doc.farmer_profile:
            return {"status": "error", "message": "Farmer Profile not found for this lead"}
        
        doc = frappe.get_doc("A2C Farmer Profile", lead_doc.farmer_profile)
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
def get_full_profile(application_id):
    try:
        doc = _get_app(application_id)

        data = doc.as_dict()
        filtered_data = {
            k: v for k, v in data.items() 
            if not k.startswith('_') and k not in ('doctype', 'docstatus', 'idx')
        }
        
        return {
            "status": "success",
            "data": filtered_data
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

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
            if row.status == "Processing":
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
        
        statuses = [s for s in status_field.options.split("\n") if s] if status_field and status_field.options else []
        
        return {
            "status": "success",
            "statuses": statuses
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
            fields=["name as application_id", "status", "loan_amount", "loan_type", "location", "creation"],
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
                "name": file_doc.name,
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
        # Validate that the application exists
        _get_app(application_id)
        
        files = frappe.get_all(
            "File",
            filters={
                "attached_to_doctype": "A2C Loan Application",
                "attached_to_name": application_id
            },
            fields=["name", "file_name", "file_url", "creation"]
        )
        return {
            "status": "success",
            "files": files
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

@frappe.whitelist(methods=["POST"])
def create_loan_application(lead_id):
    """
    Creates an A2C Loan Application by copying data from the Lead's linked Farmer Profile and Credit Information.
    """
    try:
        if not lead_id:
            return {"status": "error", "message": "lead_id is required"}

        # Check if loan application already exists
        existing = frappe.get_all("A2C Loan Application", filters={"lead_id": lead_id}, limit=1)
        if existing:
            return {"status": "error", "message": "Loan application already exists for this lead"}

        lead_doc = frappe.get_doc("A2C Lead", lead_id)
        
        # Get Farmer Profile
        farmer_profile_name = lead_doc.get("farmer_profile")
        if not farmer_profile_name:
            return {"status": "error", "message": "No Farmer Profile found for this lead. Webhook consent might not be completed."}
        
        farmer_profile = frappe.get_doc("A2C Farmer Profile", farmer_profile_name)

        # Get Credit Info (most recent)
        credit_infos = frappe.get_all(
            "A2C Credit Information", 
            filters={"lead": lead_id}, 
            fields=["loan_type", "loan_amount", "purpose_message"],
            order_by="creation desc",
            limit=1
        )

        loan_app = frappe.new_doc("A2C Loan Application")
        loan_app.lead_id = lead_id
        loan_app.farmer_profile = farmer_profile.name
        
        # Copy from farmer profile
        loan_app.first_name = farmer_profile.first_name
        loan_app.last_name = farmer_profile.last_name
        loan_app.location = farmer_profile.location
        loan_app.phone_number = farmer_profile.phone_number or lead_doc.phone_number
        loan_app.farmer_id = farmer_profile.farmer_id
        loan_app.consent_id = farmer_profile.consent_id
        
        if not credit_infos:
            return {"status": "error", "message": "Credit Information is missing for this lead. A loan application requires a valid loan amount."}
            
        loan_app.loan_type = credit_infos[0].loan_type
        loan_app.loan_amount = credit_infos[0].loan_amount
        loan_app.loan_reason = credit_infos[0].purpose_message
        loan_app.status = "Draft"
        loan_app.insert(ignore_permissions=True)
        frappe.db.commit()

        return {
            "status": "success",
            "message": "Loan application created successfully",
            "application_id": loan_app.name
        }

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Create Loan Application Error")
        return {"status": "error", "message": str(e)}

@frappe.whitelist(methods=["POST"])
def update_loan_status(application_id, status):
    """
    Updates the status of a loan application. Cannot update if current status is Rejected or Approved.
    """
    try:
        if not application_id or not status:
            return {"status": "error", "message": "application_id and status are required"}

        doc = _get_app(application_id)
        
        if doc.status in ["Rejected", "Approved"]:
            return {"status": "error", "message": f"Cannot change status. Loan application is already {doc.status}"}

        doc.status = status
        doc.save(ignore_permissions=True)
        frappe.db.commit()

        return {
            "status": "success",
            "message": f"Loan application status updated to {status}"
        }

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Update Loan Status Error")
        return {"status": "error", "message": str(e)}
