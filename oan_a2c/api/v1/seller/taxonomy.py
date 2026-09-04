import frappe
from frappe import _
from pydantic import BaseModel, Field

from oan_a2c.api.utils import handle_api_errors, success_response, validate_request


class SetTermsSchema(BaseModel):
	product_id: str = Field(..., min_length=1, max_length=140)
	term_ids: list[str]


class SetAttributesSchema(BaseModel):
	product_id: str = Field(..., min_length=1, max_length=140)
	attributes: dict[str, list[str]]


class CreateCategorySchema(BaseModel):
	term_name: str = Field(..., min_length=1, max_length=140)
	description: str | None = Field(None, max_length=2000)
	parent_category: str | None = Field(None, max_length=140)


class CreateTagSchema(BaseModel):
	term_name: str = Field(..., min_length=1, max_length=140)
	description: str | None = Field(None, max_length=2000)


class CreateTermSchema(BaseModel):
	term_name: str = Field(..., min_length=1, max_length=140)


@frappe.whitelist()
@handle_api_errors
def get_categories():
	categories = frappe.get_all("A2C Term Category", fields=["name as term_id", "parent_category"])
	for cat in categories:
		term = frappe.get_value("A2C Term Category", cat.term_id, "term")
		cat.term_name = frappe.get_value("A2C Term", term, "term_name") if term else cat.term_id
	return success_response(data={"categories": categories})


@frappe.whitelist()
@handle_api_errors
def get_tags():
	tags = frappe.get_all("A2C Term Tag", fields=["name as term_id"])
	for tag in tags:
		term = frappe.get_value("A2C Term Tag", tag.term_id, "term")
		tag.term_name = frappe.get_value("A2C Term", term, "term_name") if term else tag.term_id
	return success_response(data={"tags": tags})


@frappe.whitelist()
@handle_api_errors
def get_attributes():
	claimed = frappe.get_all("A2C Term Category", pluck="term") + frappe.get_all("A2C Term Tag", pluck="term")
	filters = [["name", "not in", claimed]] if claimed else []
	terms = frappe.get_all("A2C Term", filters=filters, fields=["name as term_id", "term_name", "slug"])
	return success_response(data={"attributes": terms})


@frappe.whitelist()
@validate_request(SetTermsSchema)
@handle_api_errors
def set_product_categories(product_id: str, term_ids: list):
	if not frappe.has_permission("A2C Loan Product", "write", product_id):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	target_ids = set(term_ids)

	# Bulk validate all category IDs in 1 query
	if target_ids:
		valid_ids = set(
			frappe.get_all("A2C Term Category", filters={"name": ["in", list(target_ids)]}, pluck="name")
		)
		missing = target_ids - valid_ids
		if missing:
			frappe.throw(
				_("Category '{0}' does not exist.").format(next(iter(missing))), frappe.ValidationError
			)

	product = frappe.get_doc("A2C Loan Product", product_id)

	# Fetch existing category relationships for this product (1 query).
	# get_list applies the A2C Term Relationship bank scope; product_id is already
	# write-authorized above.
	existing_rels = frappe.get_list(
		"A2C Term Relationship",
		filters={"loan_product": product_id, "term_type": "Category"},
		fields=["name", "term_category"],
	)

	existing_map = {rel.term_category: rel.name for rel in existing_rels}
	existing_ids = set(existing_map.keys())

	to_delete = existing_ids - target_ids
	to_insert = target_ids - existing_ids

	# Delete only removed categories (1 query)
	if to_delete:
		names_to_delete = [existing_map[cat] for cat in to_delete]
		frappe.db.delete("A2C Term Relationship", {"name": ["in", names_to_delete]})

	# Insert only newly added categories
	for term_id in to_insert:
		rel = frappe.new_doc("A2C Term Relationship")
		rel.loan_product = product_id
		rel.term_type = "Category"
		rel.term_category = term_id
		rel.bank = product.bank
		rel.insert(ignore_permissions=False)

	return success_response(data={"message": _("Categories updated")})


@frappe.whitelist()
@validate_request(SetTermsSchema)
@handle_api_errors
def set_product_tags(product_id: str, term_ids: list):
	if not frappe.has_permission("A2C Loan Product", "write", product_id):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	target_ids = set(term_ids)

	# Bulk validate all tag IDs in 1 query
	if target_ids:
		valid_ids = set(
			frappe.get_all("A2C Term Tag", filters={"name": ["in", list(target_ids)]}, pluck="name")
		)
		missing = target_ids - valid_ids
		if missing:
			frappe.throw(_("Tag '{0}' does not exist.").format(next(iter(missing))), frappe.ValidationError)

	product = frappe.get_doc("A2C Loan Product", product_id)

	# Fetch existing tag relationships for this product (1 query).
	# get_list applies the bank scope; product_id is already write-authorized above.
	existing_rels = frappe.get_list(
		"A2C Term Relationship",
		filters={"loan_product": product_id, "term_type": "Tag"},
		fields=["name", "term_tag"],
	)

	existing_map = {rel.term_tag: rel.name for rel in existing_rels}
	existing_ids = set(existing_map.keys())

	to_delete = existing_ids - target_ids
	to_insert = target_ids - existing_ids

	# Delete only removed tags (1 query)
	if to_delete:
		names_to_delete = [existing_map[tag] for tag in to_delete]
		frappe.db.delete("A2C Term Relationship", {"name": ["in", names_to_delete]})

	# Insert only newly added tags
	for term_id in to_insert:
		rel = frappe.new_doc("A2C Term Relationship")
		rel.loan_product = product_id
		rel.term_type = "Tag"
		rel.term_tag = term_id
		rel.bank = product.bank
		rel.insert(ignore_permissions=False)

	return success_response(data={"message": _("Tags updated")})


@frappe.whitelist()
@validate_request(SetAttributesSchema)
@handle_api_errors
def set_product_attributes(product_id: str, attributes: dict):
	if not frappe.has_permission("A2C Loan Product", "write", product_id):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	target_pairs = set()
	all_term_ids = set()

	for taxonomy, term_ids in attributes.items():
		for term_id in term_ids:
			target_pairs.add((taxonomy, term_id))
			all_term_ids.add(term_id)

	# Bulk validate all term IDs in 1 query
	if all_term_ids:
		valid_term_ids = set(
			frappe.get_all("A2C Term", filters={"name": ["in", list(all_term_ids)]}, pluck="name")
		)
		missing = all_term_ids - valid_term_ids
		if missing:
			frappe.throw(_("Term '{0}' does not exist.").format(next(iter(missing))), frappe.ValidationError)

	product = frappe.get_doc("A2C Loan Product", product_id)

	# Fetch existing attribute lookups (1 query)
	# bank-scope-exempt — product_id already bank-validated by has_permission("A2C Loan Product", "write") above
	existing_lookups = frappe.get_all(
		"A2C Loan Product Attribute Lookup",
		filters={"loan_product": product_id},
		fields=["name", "taxonomy", "term_id"],
	)

	existing_map = {(l.taxonomy, l.term_id): l.name for l in existing_lookups}
	existing_pairs = set(existing_map.keys())

	to_delete = existing_pairs - target_pairs
	to_insert = target_pairs - existing_pairs

	# Delete only removed attribute lookups (1 query)
	if to_delete:
		names_to_delete = [existing_map[pair] for pair in to_delete]
		frappe.db.delete("A2C Loan Product Attribute Lookup", {"name": ["in", names_to_delete]})

	# Insert only newly added attribute lookups
	for taxonomy, term_id in to_insert:
		lookup = frappe.new_doc("A2C Loan Product Attribute Lookup")
		lookup.loan_product = product_id
		lookup.bank = product.bank
		lookup.taxonomy = taxonomy
		lookup.term_id = term_id
		lookup.accepting = 1
		lookup.insert(ignore_permissions=True)

	return success_response(data={"message": _("Attributes updated")})


def _get_or_create_term(term_name: str) -> str:
	from oan_a2c.a2c_marketplace.taxonomy import get_unique_term_id

	term_id = get_unique_term_id(term_name)
	if not frappe.db.exists("A2C Term", term_id):
		if not frappe.has_permission("A2C Term", "create"):
			frappe.throw(_("Not permitted to create A2C Term"), frappe.PermissionError)
		term = frappe.get_doc(
			{
				"doctype": "A2C Term",
				"term_id": term_id,
				"term_name": term_name,
				"slug": term_id,
			}
		)
		term.insert(ignore_permissions=True)
	return term_id


@frappe.whitelist()
@validate_request(CreateCategorySchema)
@handle_api_errors
def create_category(term_name: str, description: str | None = None, parent_category: str | None = None):
	if not frappe.has_permission("A2C Term Category", "create"):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	term_id = _get_or_create_term(term_name)

	if frappe.db.exists("A2C Term Category", term_id):
		frappe.throw(_("Category '{0}' already exists.").format(term_name), frappe.DuplicateEntryError)

	cat = frappe.get_doc(
		{
			"doctype": "A2C Term Category",
			"term": term_id,
			"description": description,
			"parent_category": parent_category,
		}
	)
	cat.insert(ignore_permissions=True)

	return success_response(data={"message": _("Category created"), "term_id": term_id})


@frappe.whitelist()
@validate_request(CreateTagSchema)
@handle_api_errors
def create_tag(term_name: str, description: str | None = None):
	if not frappe.has_permission("A2C Term Tag", "create"):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	term_id = _get_or_create_term(term_name)

	if frappe.db.exists("A2C Term Tag", term_id):
		frappe.throw(_("Tag '{0}' already exists.").format(term_name), frappe.DuplicateEntryError)

	tag = frappe.get_doc({"doctype": "A2C Term Tag", "term": term_id, "description": description})
	tag.insert(ignore_permissions=True)

	return success_response(data={"message": _("Tag created"), "term_id": term_id})


@frappe.whitelist()
@validate_request(CreateTermSchema)
@handle_api_errors
def create_attribute_term(term_name: str):
	if not frappe.has_permission("A2C Term", "create"):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	term_id = _get_or_create_term(term_name)
	return success_response(data={"message": _("Attribute term ready"), "term_id": term_id})
