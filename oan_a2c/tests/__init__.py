import frappe

def before_tests():
    # Make frappe.db.commit a no-op during test runs to prevent data removal/mutation
    frappe.db.commit = lambda: None
