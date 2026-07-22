# Copyright (c) 2026, OAN and contributors
# For license information, please see license.txt

import frappe


def refresh_product_lookups(doc, method=None):
	"""Sync A2C Loan Product properties to A2C Loan Product Lookup"""
	if not doc.name:
		return

	# Check if the lookup exists
	lookup_exists = frappe.db.exists("A2C Loan Product Lookup", {"loan_product": doc.name})

	if lookup_exists:
		lookup_doc = frappe.get_doc("A2C Loan Product Lookup", lookup_exists)
	else:
		lookup_doc = frappe.new_doc("A2C Loan Product Lookup")
		lookup_doc.loan_product = doc.name

	lookup_doc.bank = doc.bank

	# Map attributes from A2C Loan Product
	lookup_doc.accepting_applications = 1 if doc.status == "Active" else 0

	# Save, avoiding validation errors for standard fields if possible
	lookup_doc.flags.ignore_permissions = True
	lookup_doc.save(ignore_permissions=True)


def regenerate_lookups():
	"""Maintenance job to regenerate lookups for all products"""
	# Trusted maintenance job: rebuilds lookups across every bank by design. bank-scope-exempt
	products = frappe.get_all("A2C Loan Product", pluck="name")  # bank-scope-exempt
	for prod in products:
		doc = frappe.get_doc("A2C Loan Product", prod)
		refresh_product_lookups(doc)
