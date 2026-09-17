#!/usr/bin/env python3
"""
generate_openapi_spec.py

Builds openapi_v1.yaml: a real OpenAPI 3.0.3 document for the A2C REST
facade (the design in A2C_API_REST_Routing_Map.md), not the legacy RPC
bridge in the team's original openapi.json.

Request-body schemas are adapted from the team's own original schemas
(loaded straight from their openapi.json -- that part of their spec was
already solid) with path-parameter fields stripped out, since those
fields move into the URL under the REST design.

Response schemas are typed per operation -- fixing the biggest finding
from the architecture review (every response used to resolve to a bare
`data: object`). Confidence varies by source:
  - "confirmed"  -> field-for-field from a documented example response
                    in the workflow guide or consent testing guide.
  - "inferred"   -> no documented response example exists for this call
                    (Catalog Discovery, Farmer Applications, CRM Leads,
                    a few Loan Underwriting reads); shape is a reasonable
                    projection from the matching request schema plus
                    standard audit fields (id/creation/modified), and is
                    flagged in the schema description as such.
Both are marked with the vendor extension `x-schema-confidence` so the
distinction survives in the generated file, not just in this script.
"""

import json
import sys
from pathlib import Path

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
ORIGINAL_SPEC_PATH = REPO_ROOT / "docs" / "openapi.json"
INTERNAL_SPEC_OUTPUT = SCRIPT_DIR / "openapi_v1.yaml"
PUBLIC_SPEC_OUTPUT = SCRIPT_DIR / "openapi_v1.public.yaml"

with open(ORIGINAL_SPEC_PATH) as f:
	ORIGINAL = json.load(f)
ORIGINAL_SCHEMAS = ORIGINAL["components"]["schemas"]


# ---------------------------------------------------------------------------
# Small schema-builder helpers
# ---------------------------------------------------------------------------
def S(**kw):
	return {"type": "string", **kw}


def I(**kw):  # noqa: E743
	return {"type": "integer", **kw}


def N(**kw):
	return {"type": "number", **kw}


def B(**kw):
	return {"type": "boolean", **kw}


def ARR(items, **kw):
	return {"type": "array", "items": items, **kw}


def OBJ(props, required=None, description=None, confidence=None):
	d = {"type": "object", "properties": props}
	if required:
		d["required"] = required
	if description:
		d["description"] = description
	if confidence:
		d["x-schema-confidence"] = confidence
	return d


def REF(name):
	return {"$ref": f"#/components/schemas/{name}"}


def _fix_exclusive_bounds(d):
	"""Pydantic/JSON-Schema-2020-12 emits `exclusiveMinimum`/`exclusiveMaximum`
	as the numeric bound itself. OpenAPI 3.0 (JSON Schema draft-4 dialect)
	requires them as booleans paired with `minimum`/`maximum`. Convert."""
	if not isinstance(d, dict):
		return d
	if isinstance(d.get("exclusiveMinimum"), (int, float)) and not isinstance(
		d.get("exclusiveMinimum"), bool
	):
		d["minimum"] = d.pop("exclusiveMinimum")
		d["exclusiveMinimum"] = True
	if isinstance(d.get("exclusiveMaximum"), (int, float)) and not isinstance(
		d.get("exclusiveMaximum"), bool
	):
		d["maximum"] = d.pop("exclusiveMaximum")
		d["exclusiveMaximum"] = True
	return d


def _fix_regex_pattern(d):
	"""OpenAPI/JSON Schema `pattern` must be an ECMA-262 regex. Pydantic can
	emit Python-only inline flags like `(?i)`, which ECMA-262 doesn't support.
	Expand a leading `(?i)` into per-letter case-insensitive alternation."""
	if isinstance(d, dict) and isinstance(d.get("pattern"), str) and "(?i)" in d["pattern"]:
		pre, _, rest = d["pattern"].partition("(?i)")
		expanded = "".join(f"[{c.lower()}{c.upper()}]" if c.isalpha() else c for c in rest)
		d = dict(d)
		d["pattern"] = pre + expanded
	return d


def clean_original(prop_def):
	"""Convert the team's `anyOf: [{...}, {type: null}]` nullable pattern
	(valid JSON Schema, but not idiomatic OpenAPI 3.0) into `nullable: true`
	on the real type -- one of the polish items from the architecture review.
	Also fixes the draft-2020-12 numeric exclusiveMinimum/Maximum pattern into
	the boolean form OpenAPI 3.0 requires."""
	if isinstance(prop_def, dict) and "anyOf" in prop_def:
		variants = [v for v in prop_def["anyOf"] if v.get("type") != "null"]
		if len(variants) == 1:
			out = dict(variants[0])
			out["nullable"] = True
			if "type" not in out and not out.get("$ref"):
				out["type"] = "string"
			return _fix_regex_pattern(_fix_exclusive_bounds(out))
		return {"type": "string", "nullable": True}
	return (
		_fix_regex_pattern(_fix_exclusive_bounds(dict(prop_def))) if isinstance(prop_def, dict) else prop_def
	)


def adapt_request_schema(original_name, strip_fields=None, new_required=None, rename_props=None):
	"""Pull a request schema from the team's original spec, strip fields that
	become path parameters under the REST design, and clean up nullability."""
	strip_fields = set(strip_fields or [])
	src = ORIGINAL_SCHEMAS[original_name]
	props = {}
	for pname, pdef in src.get("properties", {}).items():
		if pname in strip_fields:
			continue
		outname = (rename_props or {}).get(pname, pname)
		props[outname] = clean_original(pdef)
	required = [r for r in src.get("required", []) if r not in strip_fields]
	if new_required is not None:
		required = new_required
	return OBJ(props, required=required or None)


# Nested object schemas that the original spec's request schemas $ref into
# (e.g. SingleProductSchema.product_meta -> ProductMetaSchema). adapt_request_schema
# copies those $refs verbatim, so the referenced schemas must also be carried
# over into this document's components -- otherwise the $ref dangles.
SUBSCHEMAS = {}


def adapt_full_schema(original_name, outname=None):
	"""Copy an original schema (all properties, no stripping) with nullability
	cleaned up, for schemas that are only ever referenced as a nested object
	(never used directly as a request or response body)."""
	src = ORIGINAL_SCHEMAS[original_name]
	props = {pname: clean_original(pdef) for pname, pdef in src.get("properties", {}).items()}
	SUBSCHEMAS[outname or original_name] = OBJ(props, required=src.get("required") or None)


adapt_full_schema("FarmerInfoSchema")
adapt_full_schema("ConsentInfoSchema")
adapt_full_schema("ProductMetaSchema")
adapt_full_schema("SingleProductSchema")


MSG = OBJ({"message": S()}, required=["message"])

# ---------------------------------------------------------------------------
# Response DATA schemas (the `data` field inside the envelope), confirmed
# from documented examples unless noted.
# ---------------------------------------------------------------------------
DATA_SCHEMAS = {}


def data(name, schema):
	DATA_SCHEMAS[name] = schema
	return name


data("MessageData", MSG)

# --- Domain 01: Identity & Access ---
user_summary_props = {
	"email": S(),
	"full_name": S(),
	"roles": ARR(S()),
	"user_type": S(nullable=True),
	"bank": S(nullable=True),
	"bank_id": S(nullable=True),
	"bank_code": S(nullable=True),
	"bank_name": S(nullable=True),
	"bank_status": S(nullable=True),
}
data("UserSummary", OBJ(user_summary_props))
data(
	"LoginData",
	OBJ(
		{
			"token": S(description="Short-lived JWT access token"),
			"refresh_token": S(description="Long-lived refresh token; 1 day, or 30 with remember_me"),
			"user": REF("UserSummary"),
		},
		required=["token", "refresh_token", "user"],
	),
)
data("TokenPairData", OBJ({"token": S(), "refresh_token": S()}, required=["token", "refresh_token"]))
data("IdentityData", OBJ(user_summary_props))
data(
	"ProfileData",
	OBJ(
		{
			"email": S(),
			"full_name": S(),
			"phone_number": S(nullable=True),
			"language": S(nullable=True, enum=["en", "am", "om"]),
			"user_image": S(nullable=True),
			"gender": S(nullable=True),
		}
	),
)

# --- Domain 02: Bank Onboarding & Administration ---
data(
	"BankRegisteredData",
	OBJ({"message": S(), "bank_code": S(), "bank_id": S()}, required=["message", "bank_code", "bank_id"]),
)
bank_profile_props = {
	"bank_id": S(),
	"bank_code": S(),
	"bank_name": S(),
	"brand_name": S(nullable=True),
	"entity_type": S(),
	"status": S(),
	"website": S(nullable=True),
	"logo": S(nullable=True),
	"registered_street": S(),
	"registered_region": S(),
	"registered_country": S(),
	"registered_postal_code": S(),
	"registered_email": S(),
	"registered_phone": S(),
	"registered_kebele_village": S(nullable=True),
	"registered_woreda_district": S(nullable=True),
	"registered_zone": S(nullable=True),
}
data(
	"BankProfileData",
	OBJ(
		bank_profile_props,
		description="Fields drawn from the register/update request schemas; the workflow guide does not show a get_bank_profile example response.",
		confidence="inferred",
	),
)
data("FileUploadData", OBJ({"message": S(), "file_url": S()}, required=["message", "file_url"]))
team_member = OBJ(
	{
		"name": S(description="Email, used as the user's document name"),
		"email": S(),
		"first_name": S(),
		"enabled": I(enum=[0, 1]),
		"last_active": S(format="date-time", nullable=True),
		"role": S(),
		"must_change_password": B(),
	}
)
data("TeamMember", team_member)
data("TeamListData", OBJ({"users": ARR(REF("TeamMember"))}, required=["users"]))
data(
	"DashboardStatsData",
	OBJ(
		{
			"stats": OBJ(
				{
					"total_products": I(),
					"active_products": I(),
					"total_applications": I(),
					"pending_applications": I(),
					"approved_applications": I(),
					"total_approved_amount": N(),
				}
			)
		},
		required=["stats"],
	),
)

# --- Domain 03: Bank Cataloging ---
data("ProductMeta", OBJ({"meta_key": S(), "meta_value": S()}, required=["meta_key", "meta_value"]))
product_summary_props = {
	"name": S(description="Product document id, e.g. PROD-2026-0001"),
	"product_name": S(),
	"slug": S(),
	"status": S(enum=["Pending Approval", "Active", "Rejected", "Archived"]),
	"bank": S(),
	"bank_name": S(nullable=True),
	"min_interest_rate": N(),
	"max_interest_rate": N(nullable=True),
	"min_amount": N(nullable=True),
	"max_amount": N(),
	"tenure_months": I(),
	"image": S(nullable=True),
	"categories": ARR(S()),
	"applications_count": I(),
	"creation": S(format="date-time"),
}
data("ProductSummary", OBJ(product_summary_props))
product_detail_props = dict(product_summary_props)
product_detail_props.update(
	{
		"description": S(nullable=True),
		"modified": S(format="date-time"),
		"is_saved": B(),
		"product_meta": ARR(REF("ProductMeta")),
		"tags": ARR(S()),
		"attributes": OBJ(
			{}, description="Map of attribute code to selected term values, e.g. crop_type: [wheat, maize]"
		),
	}
)
data("ProductDetail", OBJ(product_detail_props))
data("ProductListData", OBJ({"products": ARR(REF("ProductSummary"))}, required=["products"]))
data("ProductDetailData", OBJ({"product": REF("ProductDetail")}, required=["product"]))
data("ProductCreateData", OBJ({"message": S(), "product_ids": ARR(S())}, required=["message", "product_ids"]))
data(
	"ProductMutationData",
	OBJ({"message": S(), "product_id": S(), "status": S(nullable=True)}, required=["message", "product_id"]),
)
data(
	"ProductAuditEvent",
	OBJ(
		{
			"name": S(),
			"creation": S(format="date-time"),
			"event_type": S(),
			"from_status": S(nullable=True),
			"to_status": S(nullable=True),
			"event_title": S(),
			"event_description": S(),
			"reason": S(nullable=True),
			"performed_by": S(),
		}
	),
)
data("ProductAuditData", OBJ({"comment": ARR(REF("ProductAuditEvent"))}, required=["comment"]))
data(
	"TaxonomyTerm",
	OBJ(
		{"term_id": S(), "term_name": S(), "parent_category": S(nullable=True)},
		required=["term_id", "term_name"],
	),
)
data(
	"TagTerm",
	OBJ(
		{"term_id": S(), "term_name": S(), "description": S(nullable=True)}, required=["term_id", "term_name"]
	),
)
data(
	"AttributeTerm",
	OBJ({"term_id": S(), "term_name": S(), "values": ARR(S())}, required=["term_id", "term_name"]),
)
data("CategoryListData", OBJ({"categories": ARR(REF("TaxonomyTerm"))}, required=["categories"]))
data("TagListData", OBJ({"tags": ARR(REF("TagTerm"))}, required=["tags"]))
data(
	"AttributeListData",
	OBJ(
		{"attributes": ARR(REF("AttributeTerm"))},
		required=["attributes"],
		description="Attribute term example values not shown in source docs; array wrapper inferred to match categories/tags.",
		confidence="inferred",
	),
)
data(
	"TermCreatedData",
	OBJ(
		{"message": S(), "term_id": S()},
		required=["message", "term_id"],
		description="create_category/create_tag/create_attribute_term response not shown in source docs; modeled on the create_product pattern.",
		confidence="inferred",
	),
)
pipeline_stage_props = {
	"name": S(description="Stage document id, e.g. STG-0001"),
	"bank": S(),
	"stage_id": S(),
	"label": S(),
	"archetype_state": S(enum=["In Transition", "Completed", "Rejected"]),
	"sequence": I(),
	"external_code": S(nullable=True),
	"description": S(nullable=True),
	"application_count": I(),
	"creation": S(format="date-time"),
	"modified": S(format="date-time"),
}
data("PipelineStage", OBJ(pipeline_stage_props))
data(
	"PipelineStageListData",
	OBJ({"stages": ARR(REF("PipelineStage")), "bank": S()}, required=["stages", "bank"]),
)
data(
	"PipelineStageCreateData",
	OBJ(
		{
			"name": S(),
			"stage_id": S(),
			"label": S(),
			"archetype_state": S(),
			"sequence": I(),
			"external_code": S(nullable=True),
		},
		required=["name", "stage_id", "label", "archetype_state", "sequence"],
	),
)

# --- Domain 04: Catalog Discovery (not documented with response examples) ---
data(
	"CatalogProduct",
	OBJ(
		product_summary_props,
		description="Public storefront view of a loan product; assumed to mirror ProductSummary.",
		confidence="inferred",
	),
)
data(
	"CatalogListData",
	OBJ({"products": ARR(REF("CatalogProduct"))}, required=["products"], confidence="inferred"),
)
data(
	"BankStorefrontData",
	OBJ(
		{
			"bank_id": S(),
			"bank_name": S(),
			"brand_name": S(nullable=True),
			"logo": S(nullable=True),
			"website": S(nullable=True),
			"region": S(nullable=True),
			"description": S(nullable=True),
			"active_products_count": I(nullable=True),
		},
		required=["bank_id", "bank_name"],
		description="Not documented with a response example; inferred from bank profile fields.",
		confidence="inferred",
	),
)
data(
	"CatalogFacetsData",
	OBJ(
		{
			"categories": ARR(S()),
			"tags": ARR(S()),
			"regions": ARR(S()),
			"interest_rate_range": OBJ({"min": N(), "max": N()}),
			"amount_range": OBJ({"min": N(), "max": N()}),
		},
		description="Not documented with a response example; inferred from FarmerCatalogSchema's filter fields.",
		confidence="inferred",
	),
)
data(
	"SavedProductsData",
	OBJ({"products": ARR(REF("CatalogProduct"))}, required=["products"], confidence="inferred"),
)
data(
	"FarmerDashboardData",
	OBJ(
		{
			"applications_count": I(),
			"active_applications": I(),
			"saved_products_count": I(),
			"unread_notifications": I(),
		},
		description="Not documented with a response example; a reasonable projection of what a dashboard summary would surface.",
		confidence="inferred",
	),
)

# --- Domain 05: Applications (Farmer self-service) -- not documented ---
farmer_app_props = {
	"name": S(description="Application document id"),
	"loan_product": S(),
	"loan_product_name": S(nullable=True),
	"requested_amount": N(),
	"loan_reason": S(nullable=True),
	"consent_request": S(nullable=True),
	"status": S(enum=["Active", "In Transition"]),
	"creation": S(format="date-time"),
}
data("FarmerApplication", OBJ(farmer_app_props, confidence="inferred"))
data(
	"FarmerApplicationListData",
	OBJ({"applications": ARR(REF("FarmerApplication"))}, required=["applications"], confidence="inferred"),
)
data(
	"FarmerApplicationData",
	OBJ({"application": REF("FarmerApplication")}, required=["application"], confidence="inferred"),
)
data(
	"FarmerApplicationCreatedData",
	OBJ(
		{"message": S(), "application_id": S()}, required=["message", "application_id"], confidence="inferred"
	),
)

# --- Domain 06: CRM Leads & Field Ops -- not documented ---
lead_props = {
	"name": S(description="Lead document id"),
	"phone_number": S(),
	"first_name": S(nullable=True),
	"last_name": S(nullable=True),
	"email": S(nullable=True),
	"lead_source": S(nullable=True, enum=["Missed Call", "IVR", "SMS", "Agent Entry"]),
	"external_id": S(nullable=True),
	"status": S(),
	"assigned_to": S(nullable=True),
	"creation": S(format="date-time"),
}
data("Lead", OBJ(lead_props, confidence="inferred"))
data("LeadListData", OBJ({"leads": ARR(REF("Lead"))}, required=["leads"], confidence="inferred"))
data(
	"LeadSummaryData",
	OBJ({"total": I(), "by_status": OBJ({}, description="Status label -> count")}, confidence="inferred"),
)
data(
	"LeadMetadataData",
	OBJ({"statuses": ARR(S()), "lead_sources": ARR(S()), "loan_types": ARR(S())}, confidence="inferred"),
)
data("AssignableUser", OBJ({"email": S(), "full_name": S(), "role": S()}))
data(
	"AssignableUsersData",
	OBJ({"users": ARR(REF("AssignableUser"))}, required=["users"], confidence="inferred"),
)
data(
	"LeadCommentData",
	OBJ({"message": S(), "comment_id": S(nullable=True)}, required=["message"], confidence="inferred"),
)
data(
	"LeadTimelineEvent",
	OBJ(
		{
			"event_type": S(),
			"content": S(nullable=True),
			"performed_by": S(nullable=True),
			"creation": S(format="date-time"),
		}
	),
)
data(
	"LeadTimelineData",
	OBJ({"timeline": ARR(REF("LeadTimelineEvent"))}, required=["timeline"], confidence="inferred"),
)
data(
	"LeadCallLog",
	OBJ(
		{
			"call_id": S(),
			"direction": S(nullable=True),
			"duration_seconds": I(nullable=True),
			"creation": S(format="date-time"),
		}
	),
)
data(
	"LeadCallLogsData",
	OBJ({"call_logs": ARR(REF("LeadCallLog"))}, required=["call_logs"], confidence="inferred"),
)
data(
	"CreditInfo",
	OBJ(
		{
			"name": S(),
			"lead_id": S(),
			"loan_type": S(nullable=True),
			"loan_amount": N(),
			"purpose_message": S(),
			"loan_product": S(),
		}
	),
)
data(
	"LeadCreditInfoListData",
	OBJ({"credit_info": ARR(REF("CreditInfo"))}, required=["credit_info"], confidence="inferred"),
)
data(
	"LeadCreditInfoCreatedData",
	OBJ({"message": S(), "name": S()}, required=["message", "name"], confidence="inferred"),
)
data(
	"VisitSchedule",
	OBJ(
		{
			"name": S(),
			"lead_id": S(),
			"visit_date": S(format="date"),
			"visit_time": S(),
			"region": S(),
			"zone": S(),
			"woreda": S(),
			"kebele": S(),
			"meeting_location": S(nullable=True),
			"notes": S(nullable=True),
			"status": S(enum=["Scheduled", "Completed", "Cancelled", "Missed"]),
		}
	),
)
data(
	"VisitScheduleListData",
	OBJ({"visits": ARR(REF("VisitSchedule"))}, required=["visits"], confidence="inferred"),
)
data(
	"VisitScheduleCreatedData",
	OBJ({"message": S(), "name": S()}, required=["message", "name"], confidence="inferred"),
)

# --- Domain 07: Loan Underwriting ---
data(
	"LoanApplicationSummary",
	OBJ(
		{
			"name": S(),
			"farmer_name": S(),
			"phone_number": S(),
			"loan_product": S(),
			"loan_amount": N(),
			"status": S(),
			"stage_id": S(),
			"stage_label": S(),
			"current_step": I(),
			"assigned_loan_officer": S(nullable=True),
			"region": S(nullable=True),
			"woreda": S(nullable=True),
			"kebele": S(nullable=True),
			"creation": S(format="date-time"),
		}
	),
)
data("LoanApplicationListData", OBJ({"loans": ARR(REF("LoanApplicationSummary"))}, required=["loans"]))
data(
	"LoanSummaryData",
	OBJ(
		{
			"total_applications": I(),
			"in_transition": I(),
			"completed": I(),
			"rejected": I(),
			"total_disbursed_amount": N(),
		},
		required=["total_applications", "in_transition", "completed", "rejected", "total_disbursed_amount"],
	),
)
data(
	"LoanMetadataData",
	OBJ(
		{"statuses": ARR(S())},
		description="Dropdown status options; exact field name not shown in source docs.",
		confidence="inferred",
	),
)
data(
	"LoanApplicationCreatedData",
	OBJ(
		{"message": S(), "application_id": S()}, required=["message", "application_id"], confidence="inferred"
	),
)
full_profile_props = {
	"application_id": S(),
	"lead_id": S(),
	"first_name": S(),
	"last_name": S(),
	"region": S(nullable=True),
	"woreda": S(nullable=True),
	"kebele": S(nullable=True),
	"language": S(nullable=True),
	"phone_number": S(),
	"id_type": S(nullable=True),
	"id_number": S(nullable=True),
	"farmer_id": S(nullable=True),
	"consent_id": S(nullable=True),
	"loan_type": S(nullable=True),
	"loan_product": S(),
	"loan_product_name": S(nullable=True),
	"loan_amount": N(),
	"loan_reason": S(nullable=True),
	"status": S(),
	"stage_id": S(),
	"sequence": I(),
	"is_terminal": B(),
	"is_successful": B(),
	"current_step": I(),
	"loan_officer": S(nullable=True),
	"creation": S(format="date-time"),
	"date_of_birth": S(format="date", nullable=True),
	"gender": S(nullable=True),
	"marital_status": S(nullable=True),
	"size_of_family": I(nullable=True),
	"number_of_children": I(nullable=True),
	"no_of_females_family": I(nullable=True),
	"no_of_males_family": I(nullable=True),
	"source_of_income": S(nullable=True),
	"education_level": S(nullable=True),
	"family_member_owns_land_independently": B(nullable=True),
	"total_farmland_size_as_landowner": N(nullable=True),
	"total_farmland_size_as_crop_sharing": N(nullable=True),
	"total_farmland_size_as_rented": N(nullable=True),
	"farmland_size_hectares": N(nullable=True),
	"land_ownership_status": S(nullable=True),
	"soil_fertility_minerals": S(nullable=True),
	"moisture_levels": S(nullable=True),
	"certification_id": S(nullable=True),
	"certification_photo_url": S(nullable=True),
}
data(
	"LoanApplicationFullProfileData",
	OBJ(
		full_profile_props,
		description="Flat object -- there are no nested personal/farm_details/crops groupings.",
	),
)
data(
	"LoanApplicationBasicProfileData",
	OBJ(
		{
			"lead_id": S(),
			"email": S(nullable=True),
			"region": S(nullable=True),
			"woreda": S(nullable=True),
			"kebele": S(nullable=True),
		},
		required=["lead_id"],
		description="Not documented with a response example; mirrors update_basic_profile's request fields.",
		confidence="inferred",
	),
)
data(
	"SupportingDocument",
	OBJ(
		{"file_id": S(), "filename": S(), "file_url": S(nullable=True), "uploaded_at": S(format="date-time")}
	),
)
data(
	"SupportingDocumentListData",
	OBJ({"documents": ARR(REF("SupportingDocument"))}, required=["documents"], confidence="inferred"),
)
data(
	"SupportingDocumentUploadData",
	OBJ(
		{"message": S(), "files": ARR(REF("SupportingDocument"))},
		required=["message", "files"],
		confidence="inferred",
	),
)

# --- Domain 08: Consent Management ---
data(
	"ConsentFarmerRecord",
	OBJ(
		{
			"id": I(),
			"name": S(),
			"farmer_id": S(nullable=True),
			"phone": S(nullable=True),
			"reg_ids": ARR(S()),
			"profile_image_url": S(nullable=True),
			"otp_identifier": S(nullable=True),
			"otp_identifier_type": S(nullable=True),
			"otp_identifier_source": S(nullable=True),
			"otp_available": B(),
		},
		required=["id", "name"],
	),
)
data("FarmerSearchData", OBJ({"farmers": ARR(REF("ConsentFarmerRecord"))}, required=["farmers"]))
data("ConsentReason", OBJ({"id": I(), "name": S(), "description": S(nullable=True)}, required=["id", "name"]))
data("AllowedField", OBJ({"id": I(), "name": S(), "code": S()}, required=["id", "name", "code"]))
data(
	"OtpRequestData",
	OBJ(
		{
			"transaction_id": S(),
			"masked_mobile": S(nullable=True),
			"masked_email": S(nullable=True),
			"identifier_type": S(nullable=True),
			"identifier_source": S(nullable=True),
		},
		required=["transaction_id"],
	),
)
data(
	"OtpVerifyData",
	OBJ(
		{
			"transaction_id": S(),
			"masked_mobile": S(nullable=True),
			"verified_at": S(format="date-time"),
		},
		required=["transaction_id", "verified_at"],
	),
)
data(
	"ConsentSubmitData",
	OBJ(
		{
			"consent_id": I(),
			"status": S(),
			"auto_approved": B(),
			"auto_approval_failed": B(),
			"auto_approve_method": S(nullable=True),
			"error_details": S(nullable=True),
		},
		required=["consent_id", "status"],
	),
)
data("ConsentWebhookAckData", OBJ({"message": S()}, required=["message"], confidence="inferred"))

# --- Domain 09: Notifications ---
data(
	"NotificationItem",
	OBJ(
		{
			"id": S(),
			"subject": S(),
			"email_content": S(nullable=True),
			"document_type": S(nullable=True),
			"document_name": S(nullable=True),
			"read": I(enum=[0, 1]),
			"creation": S(format="date-time"),
		}
	),
)
data(
	"NotificationListData",
	OBJ(
		{"unread_count": I(), "notifications": ARR(REF("NotificationItem"))},
		required=["unread_count", "notifications"],
	),
)
data("NotificationUpdateData", OBJ({"updated": I()}, required=["updated"]))
data("NotificationDeleteData", OBJ({"deleted": I()}, required=["deleted"]))

# --- Domain 10: Inbound Webhooks ---
data(
	"LeadInboundData",
	OBJ({"message": S(), "lead_id": S()}, required=["message", "lead_id"], confidence="inferred"),
)

# ---------------------------------------------------------------------------
# Request body schemas: adapted from the team's originals, path-param fields
# stripped since they move into the URL under the REST design.
# ---------------------------------------------------------------------------
REQ = {}
REQ["RegisterRequest"] = adapt_request_schema("RegisterUserSchema")
REQ["LoginRequest"] = adapt_request_schema("LoginSchema")
REQ["TokenRefreshRequest"] = adapt_request_schema("RefreshTokenSchema")
REQ["LogoutRequest"] = adapt_request_schema("LogoutSchema")
REQ["ForgotPasswordRequest"] = adapt_request_schema("ForgotPasswordSchema")
REQ["ResetPasswordRequest"] = adapt_request_schema("ResetPasswordSchema")
REQ["InitialPasswordRequest"] = adapt_request_schema("SetInitialPasswordSchema")
REQ["ChangePasswordRequest"] = adapt_request_schema("ChangePasswordSchema")
REQ["UpdateProfileRequest"] = adapt_request_schema("UpdateProfileSchema")

REQ["RegisterBankRequest"] = adapt_request_schema("RegisterBankSchema")
REQ["UpdateBankProfileRequest"] = adapt_request_schema("UpdateBankProfileSchema")
REQ["UpdateBankStatusRequest"] = adapt_request_schema("UpdateBankStatusSchema", strip_fields=["bank_code"])
REQ["UploadKycRequest"] = adapt_request_schema("UploadKycSchema")
REQ["UploadImageRequest"] = adapt_request_schema("UploadImageSchema")
REQ["SaveOrgContactsRequest"] = adapt_request_schema("SaveOrgContactsSchema")
REQ["InviteTeamMemberRequest"] = adapt_request_schema("InviteTeamMemberSchema")
REQ["UpdateTeamMemberRequest"] = adapt_request_schema("UpdateUserSchema", strip_fields=["email"])
REQ["ResetMemberPasswordRequest"] = adapt_request_schema("ResetMemberPasswordSchema", strip_fields=["email"])

REQ["CreateProductRequest"] = adapt_request_schema("CreateProductSchema")
REQ["UpdateProductRequest"] = adapt_request_schema("UpdateProductSchema", strip_fields=["product_id"])
REQ["SetProductStatusRequest"] = adapt_request_schema("SetProductStatusSchema", strip_fields=["product_id"])
REQ["SetProductCategoriesRequest"] = adapt_request_schema("SetTermsSchema", strip_fields=["product_id"])
REQ["SetProductTagsRequest"] = adapt_request_schema("SetTermsSchema", strip_fields=["product_id"])
REQ["SetProductAttributesRequest"] = adapt_request_schema("SetAttributesSchema", strip_fields=["product_id"])
REQ["CreateCategoryRequest"] = adapt_request_schema("CreateCategorySchema")
REQ["CreateTagRequest"] = adapt_request_schema("CreateTagSchema")
REQ["CreateAttributeTermRequest"] = adapt_request_schema("CreateTermSchema")
REQ["AddPipelineStageRequest"] = OBJ(
	{
		"label": S(),
		"archetype_state": S(enum=["In Transition", "Completed", "Rejected"]),
		"sequence": I(nullable=True),
		"external_code": S(nullable=True),
		"description": S(nullable=True),
	},
	required=["label", "archetype_state"],
)
REQ["SyncPipelineStagesRequest"] = OBJ(
	{
		"stages": ARR(
			OBJ(
				{
					"stage_id": S(nullable=True, description="Omit when adding a new stage"),
					"label": S(),
					"archetype_state": S(enum=["In Transition", "Completed", "Rejected"]),
					"sequence": I(),
				},
				required=["label", "archetype_state", "sequence"],
			)
		),
	},
	required=["stages"],
)

REQ["CreateApplicationRequest"] = adapt_request_schema("CreateFarmerApplicationSchema")
REQ["UpdateApplicationRequest"] = adapt_request_schema(
	"UpdateFarmerApplicationSchema", strip_fields=["application_id"]
)

REQ["CreateLeadRequest"] = adapt_request_schema("CreateLeadSchema")
REQ["UpdateLeadStatusRequest"] = adapt_request_schema("UpdateLeadStatusSchema", strip_fields=["lead_id"])
REQ["AssignLeadRequest"] = adapt_request_schema("AssignLeadSchema", strip_fields=["lead_id"])
REQ["AddLeadCommentRequest"] = adapt_request_schema("AddLeadCommentSchema", strip_fields=["lead_id"])
REQ["AddLeadCreditInfoRequest"] = adapt_request_schema("AddLeadCreditInfoSchema", strip_fields=["lead_id"])
REQ["ScheduleVisitRequest"] = adapt_request_schema("ScheduleVisitSchema")
REQ["UpdateVisitStatusRequest"] = adapt_request_schema(
	"UpdateVisitScheduleStatusSchema", strip_fields=["schedule_id"]
)

REQ["CreateLoanApplicationRequest"] = OBJ(
	{
		"lead_id": S(),
		"bank": S(),
	},
	required=["lead_id", "bank"],
	description="Converts a qualified lead into a bank-scoped loan application.",
)
REQ["UpdateBasicProfileRequest"] = adapt_request_schema("UpdateBasicProfileSchema", strip_fields=["lead_id"])
REQ["UpdateLoanStatusRequest"] = adapt_request_schema(
	"UpdateLoanStatusSchema", strip_fields=["application_id"]
)
REQ["UpdateLoanStepRequest"] = adapt_request_schema("UpdateLoanStepSchema", strip_fields=["application_id"])
REQ["AssignLoanOfficerRequest"] = adapt_request_schema(
	"AssignLoanOfficerSchema", strip_fields=["application_id"]
)
REQ["UploadSupportingDocumentsRequest"] = OBJ(
	{
		"files": ARR(
			OBJ(
				{"filename": S(), "filedata": S(description="Base64-encoded file content")},
				required=["filename", "filedata"],
			)
		),
	},
	required=["files"],
)

REQ["RequestOtpRequest"] = adapt_request_schema("RequestOTPSchema")
REQ["VerifyOtpRequest"] = adapt_request_schema("VerifyOTPSchema")
REQ["SubmitConsentRequest"] = adapt_request_schema("SubmitConsentSchema")
REQ["ReceiveConsentDataRequest"] = adapt_request_schema("ReceiveConsentDataSchema")

REQ["MarkNotificationsReadRequest"] = adapt_request_schema("MarkReadSchema")
REQ["ClearNotificationsRequest"] = adapt_request_schema("ClearSchema")

REQ["LeadInboundRequest"] = OBJ(
	{
		"phone_number": S(),
		"external_id": S(nullable=True),
		"source": S(nullable=True, enum=["Missed Call", "IVR"]),
	},
	required=["phone_number"],
	description="Not documented with a request example; inferred from the endpoint's idempotency contract (dedupe by external_id, then by phone_number).",
	confidence="inferred",
)


# ---------------------------------------------------------------------------
# Query parameter sets, adapted the same way as request bodies.
# ---------------------------------------------------------------------------
def query_params_from_schema(original_name, descriptions=None, rename=None):
	descriptions = descriptions or {}
	rename = rename or {}
	src = ORIGINAL_SCHEMAS[original_name]
	params = []
	for pname, pdef in src.get("properties", {}).items():
		outname = rename.get(pname, pname)
		schema = clean_original(pdef)
		params.append(
			{
				"name": outname,
				"in": "query",
				"required": outname in src.get("required", []),
				"schema": schema,
				"description": descriptions.get(pname, ""),
			}
		)
	return params


PAGE_PARAMS = [
	{
		"name": "page",
		"in": "query",
		"required": False,
		"schema": I(minimum=1, default=1),
		"description": "Page number.",
	},
	{
		"name": "page_size",
		"in": "query",
		"required": False,
		"schema": I(minimum=1, maximum=100, default=20),
		"description": "Items per page.",
	},
]

QP = {}
QP["ListLoanApplications"] = query_params_from_schema(
	"GetAllLoansSchema", rename={"loan_amount": "loan_amount"}
)
QP["ListProducts"] = [
	{
		"name": "status",
		"in": "query",
		"required": False,
		"schema": S(nullable=True),
		"description": "Filter by status.",
	},
	{
		"name": "search",
		"in": "query",
		"required": False,
		"schema": S(nullable=True),
		"description": "Search product name.",
	},
	{
		"name": "category",
		"in": "query",
		"required": False,
		"schema": S(nullable=True),
		"description": "Filter by category id.",
	},
	{
		"name": "tag",
		"in": "query",
		"required": False,
		"schema": S(nullable=True),
		"description": "Filter by tag id.",
	},
	{
		"name": "min_interest_rate",
		"in": "query",
		"required": False,
		"schema": N(nullable=True),
		"description": "",
	},
	{
		"name": "max_interest_rate",
		"in": "query",
		"required": False,
		"schema": N(nullable=True),
		"description": "",
	},
	{"name": "min_amount", "in": "query", "required": False, "schema": N(nullable=True), "description": ""},
	{"name": "max_amount", "in": "query", "required": False, "schema": N(nullable=True), "description": ""},
	{
		"name": "tenure_months",
		"in": "query",
		"required": False,
		"schema": I(nullable=True),
		"description": "Exact tenure match.",
	},
	*PAGE_PARAMS,
]
QP["CatalogList"] = query_params_from_schema("FarmerCatalogSchema")
QP["GetBankDetails"] = []  # bank id moves to path
QP["ListLeads"] = query_params_from_schema("GetLeadsSchema")
QP["ListVisitSchedules"] = query_params_from_schema("GetVisitSchedulesSchema")
QP["AssignableUsers"] = query_params_from_schema("GetAssignableUsersSchema")
QP["GetNotifications"] = query_params_from_schema("GetNotificationsSchema")
QP["ConsentFarmerSearch"] = [
	{
		"name": "fayda_id",
		"in": "query",
		"required": True,
		"schema": S(),
		"description": "Fayda / national ID to search by.",
	}
]
QP["SupportingDocumentDownload"] = [
	{
		"name": "view",
		"in": "query",
		"required": False,
		"schema": I(nullable=True),
		"description": "1 to render inline; omit to download.",
	}
]
QP["GetBasicProfile"] = [
	{
		"name": "include_consent_data",
		"in": "query",
		"required": False,
		"schema": I(nullable=True),
		"description": "",
	}
]


# ---------------------------------------------------------------------------
# The 94 routes.
# ---------------------------------------------------------------------------
def R(
	method,
	path,
	summary,
	tag,
	security,
	request=None,
	query=None,
	response=None,
	paginated=False,
	path_params=None,
	legacy="",
	status=200,
	binary=False,
):
	return dict(
		method=method,
		path=path,
		summary=summary,
		tag=tag,
		security=security,
		request=request,
		query=query or [],
		response=response,
		paginated=paginated,
		path_params=path_params or [],
		legacy=legacy,
		status=status,
		binary=binary,
	)


ROUTES = [
	# --- 01 Identity & Access ---
	R(
		"post",
		"/v1/auth/register",
		"Register a new account",
		"Identity & Access",
		"public",
		"RegisterRequest",
		response="MessageData",
		legacy="oan_a2c.api.v1.auth.register_user",
	),
	R(
		"post",
		"/v1/auth/login",
		"Log in",
		"Identity & Access",
		"public",
		"LoginRequest",
		response="LoginData",
		legacy="oan_a2c.api.auth.login",
	),
	R(
		"post",
		"/v1/auth/token/refresh",
		"Refresh an access token",
		"Identity & Access",
		"public",
		"TokenRefreshRequest",
		response="TokenPairData",
		legacy="oan_a2c.api.auth.refresh",
	),
	R(
		"post",
		"/v1/auth/logout",
		"Log out",
		"Identity & Access",
		"bearer",
		"LogoutRequest",
		response="MessageData",
		legacy="oan_a2c.api.auth.logout",
	),
	R(
		"post",
		"/v1/auth/password/forgot",
		"Request a password-recovery code",
		"Identity & Access",
		"public",
		"ForgotPasswordRequest",
		response="MessageData",
		legacy="oan_a2c.api.auth.forgot_password",
	),
	R(
		"post",
		"/v1/auth/password/reset",
		"Reset password with a recovery code",
		"Identity & Access",
		"public",
		"ResetPasswordRequest",
		response="MessageData",
		legacy="oan_a2c.api.auth.reset_password",
	),
	R(
		"post",
		"/v1/auth/password/initial",
		"Set initial password (invited users)",
		"Identity & Access",
		"public",
		"InitialPasswordRequest",
		response="MessageData",
		legacy="oan_a2c.api.auth.set_initial_password",
	),
	R(
		"patch",
		"/v1/me/password",
		"Change the current user's password",
		"Identity & Access",
		"bearer",
		"ChangePasswordRequest",
		response="MessageData",
		legacy="oan_a2c.api.auth.change_password",
	),
	R(
		"get",
		"/v1/me",
		"Get the current user's identity",
		"Identity & Access",
		"bearer",
		response="IdentityData",
		legacy="oan_a2c.api.auth.get_me",
	),
	R(
		"get",
		"/v1/me/profile",
		"Get the current user's full profile",
		"Identity & Access",
		"bearer",
		response="ProfileData",
		legacy="oan_a2c.api.auth.get_user_profile",
	),
	R(
		"patch",
		"/v1/me/profile",
		"Update the current user's profile",
		"Identity & Access",
		"bearer",
		"UpdateProfileRequest",
		response="MessageData",
		legacy="oan_a2c.api.auth.update_profile",
	),
	# --- 02 Bank Onboarding & Administration ---
	R(
		"post",
		"/v1/banks",
		"Register a new bank",
		"Bank Onboarding & Administration",
		"bearer",
		"RegisterBankRequest",
		response="BankRegisteredData",
		legacy="oan_a2c.api.v1.seller.onboarding.register_bank",
	),
	R(
		"get",
		"/v1/banks/me",
		"Get the caller's bank profile",
		"Bank Onboarding & Administration",
		"bearer",
		response="BankProfileData",
		legacy="oan_a2c.api.v1.seller.onboarding.get_bank_profile",
	),
	R(
		"patch",
		"/v1/banks/me",
		"Update the caller's bank profile",
		"Bank Onboarding & Administration",
		"bearer",
		"UpdateBankProfileRequest",
		response="MessageData",
		legacy="oan_a2c.api.v1.seller.onboarding.update_bank_profile",
	),
	R(
		"patch",
		"/v1/banks/me/status",
		"Update the bank's onboarding status",
		"Bank Onboarding & Administration",
		"bearer",
		"UpdateBankStatusRequest",
		response="MessageData",
		legacy="oan_a2c.api.v1.seller.onboarding.update_bank_status",
	),
	R(
		"post",
		"/v1/banks/me/kyc-documents",
		"Upload the bank's KYC document",
		"Bank Onboarding & Administration",
		"bearer",
		"UploadKycRequest",
		response="FileUploadData",
		legacy="oan_a2c.api.v1.seller.onboarding.upload_kyc_document",
	),
	R(
		"post",
		"/v1/banks/me/logo",
		"Upload the bank's logo",
		"Bank Onboarding & Administration",
		"bearer",
		"UploadImageRequest",
		response="FileUploadData",
		legacy="oan_a2c.api.v1.seller.onboarding.upload_image",
	),
	R(
		"put",
		"/v1/banks/me/contacts",
		"Set GRO/OPS compliance contacts",
		"Bank Onboarding & Administration",
		"bearer",
		"SaveOrgContactsRequest",
		response="MessageData",
		legacy="oan_a2c.api.v1.seller.onboarding.save_org_contacts",
	),
	R(
		"get",
		"/v1/banks/me/team",
		"List the bank's team members",
		"Bank Onboarding & Administration",
		"bearer",
		response="TeamListData",
		legacy="oan_a2c.api.v1.seller.onboarding.list_users",
	),
	R(
		"post",
		"/v1/banks/me/team",
		"Invite a team member",
		"Bank Onboarding & Administration",
		"bearer",
		"InviteTeamMemberRequest",
		response="MessageData",
		legacy="oan_a2c.api.v1.seller.onboarding.invite_team_member",
	),
	R(
		"patch",
		"/v1/banks/me/team/{userId}",
		"Update a team member",
		"Bank Onboarding & Administration",
		"bearer",
		"UpdateTeamMemberRequest",
		response="MessageData",
		path_params=[("userId", "Team member's email address")],
		legacy="oan_a2c.api.v1.seller.onboarding.update_user",
	),
	R(
		"post",
		"/v1/banks/me/team/{userId}/password-reset",
		"Reset a team member's password",
		"Bank Onboarding & Administration",
		"bearer",
		"ResetMemberPasswordRequest",
		response="MessageData",
		path_params=[("userId", "Team member's email address")],
		legacy="oan_a2c.api.v1.seller.onboarding.reset_member_password",
	),
	R(
		"get",
		"/v1/banks/me/dashboard/stats",
		"Get the bank's dashboard metrics",
		"Bank Onboarding & Administration",
		"bearer",
		response="DashboardStatsData",
		legacy="oan_a2c.api.v1.seller.dashboard.get_stats",
	),
	# --- 03 Bank Cataloging ---
	R(
		"post",
		"/v1/banks/me/products",
		"Create one or more loan products",
		"Bank Cataloging",
		"bearer",
		"CreateProductRequest",
		response="ProductCreateData",
		legacy="oan_a2c.api.v1.seller.loan_products.create_product",
	),
	R(
		"get",
		"/v1/banks/me/products",
		"List the bank's loan products",
		"Bank Cataloging",
		"bearer",
		query="ListProducts",
		response="ProductListData",
		paginated=True,
		legacy="oan_a2c.api.v1.seller.loan_products.list_products",
	),
	R(
		"get",
		"/v1/banks/me/products/{id}",
		"Get a loan product",
		"Bank Cataloging",
		"bearer",
		response="ProductDetailData",
		path_params=[("id", "Product document id")],
		legacy="oan_a2c.api.v1.seller.loan_products.get_product",
	),
	R(
		"patch",
		"/v1/banks/me/products/{id}",
		"Update a loan product",
		"Bank Cataloging",
		"bearer",
		"UpdateProductRequest",
		response="MessageData",
		path_params=[("id", "Product document id")],
		legacy="oan_a2c.api.v1.seller.loan_products.update_product",
	),
	R(
		"patch",
		"/v1/banks/me/products/{id}/status",
		"Transition a product's status",
		"Bank Cataloging",
		"bearer",
		"SetProductStatusRequest",
		response="ProductMutationData",
		path_params=[("id", "Product document id")],
		legacy="oan_a2c.api.v1.seller.loan_products.set_product_status",
	),
	R(
		"get",
		"/v1/banks/me/products/{id}/audit-log",
		"Get a product's status-change history",
		"Bank Cataloging",
		"bearer",
		response="ProductAuditData",
		path_params=[("id", "Product document id")],
		legacy="oan_a2c.api.v1.seller.loan_products.get_product_comment",
	),
	R(
		"put",
		"/v1/banks/me/products/{id}/categories",
		"Set a product's categories",
		"Bank Cataloging",
		"bearer",
		"SetProductCategoriesRequest",
		response="MessageData",
		path_params=[("id", "Product document id")],
		legacy="oan_a2c.api.v1.seller.taxonomy.set_product_categories",
	),
	R(
		"put",
		"/v1/banks/me/products/{id}/tags",
		"Set a product's tags",
		"Bank Cataloging",
		"bearer",
		"SetProductTagsRequest",
		response="MessageData",
		path_params=[("id", "Product document id")],
		legacy="oan_a2c.api.v1.seller.taxonomy.set_product_tags",
	),
	R(
		"put",
		"/v1/banks/me/products/{id}/attributes",
		"Set a product's attributes",
		"Bank Cataloging",
		"bearer",
		"SetProductAttributesRequest",
		response="MessageData",
		path_params=[("id", "Product document id")],
		legacy="oan_a2c.api.v1.seller.taxonomy.set_product_attributes",
	),
	R(
		"get",
		"/v1/taxonomy/categories",
		"List marketplace categories",
		"Bank Cataloging",
		"bearer",
		response="CategoryListData",
		legacy="oan_a2c.api.v1.seller.taxonomy.get_categories",
	),
	R(
		"get",
		"/v1/taxonomy/tags",
		"List marketplace tags",
		"Bank Cataloging",
		"bearer",
		response="TagListData",
		legacy="oan_a2c.api.v1.seller.taxonomy.get_tags",
	),
	R(
		"get",
		"/v1/taxonomy/attributes",
		"List marketplace attributes",
		"Bank Cataloging",
		"bearer",
		response="AttributeListData",
		legacy="oan_a2c.api.v1.seller.taxonomy.get_attributes",
	),
	R(
		"post",
		"/v1/admin/taxonomy/categories",
		"Create a marketplace category",
		"Bank Cataloging",
		"bearer",
		"CreateCategoryRequest",
		response="TermCreatedData",
		legacy="oan_a2c.api.v1.seller.taxonomy.create_category",
	),
	R(
		"post",
		"/v1/admin/taxonomy/tags",
		"Create a marketplace tag",
		"Bank Cataloging",
		"bearer",
		"CreateTagRequest",
		response="TermCreatedData",
		legacy="oan_a2c.api.v1.seller.taxonomy.create_tag",
	),
	R(
		"post",
		"/v1/admin/taxonomy/attribute-terms",
		"Create a product attribute term",
		"Bank Cataloging",
		"bearer",
		"CreateAttributeTermRequest",
		response="TermCreatedData",
		legacy="oan_a2c.api.v1.seller.taxonomy.create_attribute_term",
	),
	R(
		"get",
		"/v1/banks/me/pipeline-stages",
		"List the bank's pipeline stages",
		"Bank Cataloging",
		"bearer",
		response="PipelineStageListData",
		legacy="oan_a2c.api.v1.seller.loan_stages.get_stages",
	),
	R(
		"post",
		"/v1/banks/me/pipeline-stages",
		"Add a pipeline stage",
		"Bank Cataloging",
		"bearer",
		"AddPipelineStageRequest",
		response="PipelineStageCreateData",
		legacy="oan_a2c.api.v1.seller.loan_stages.add_stage",
	),
	R(
		"put",
		"/v1/banks/me/pipeline-stages",
		"Reorder or replace the pipeline",
		"Bank Cataloging",
		"bearer",
		"SyncPipelineStagesRequest",
		response="PipelineStageListData",
		legacy="oan_a2c.api.v1.seller.loan_stages.sync_stages",
	),
	# --- 04 Catalog Discovery ---
	R(
		"get",
		"/v1/catalog/products",
		"Browse the marketplace catalog",
		"Catalog Discovery",
		"bearer",
		query="CatalogList",
		response="CatalogListData",
		paginated=True,
		legacy="oan_a2c.api.v1.farmer.catalog.list_catalog",
	),
	R(
		"get",
		"/v1/catalog/banks/{bankId}",
		"Get a bank's storefront detail",
		"Catalog Discovery",
		"bearer",
		response="BankStorefrontData",
		path_params=[("bankId", "Bank document id")],
		legacy="oan_a2c.api.v1.farmer.catalog.get_bank_details",
	),
	R(
		"get",
		"/v1/catalog/facets",
		"Get catalog filter options",
		"Catalog Discovery",
		"bearer",
		response="CatalogFacetsData",
		legacy="oan_a2c.api.v1.farmer.catalog.get_catalog_facets",
	),
	R(
		"get",
		"/v1/catalog/saved-products",
		"List the caller's saved products",
		"Catalog Discovery",
		"bearer",
		response="SavedProductsData",
		legacy="oan_a2c.api.v1.farmer.catalog.get_saved_products",
	),
	R(
		"put",
		"/v1/catalog/saved-products/{productId}",
		"Save a product",
		"Catalog Discovery",
		"bearer",
		response="MessageData",
		path_params=[("productId", "Product document id")],
		legacy="oan_a2c.api.v1.farmer.catalog.save_product",
	),
	R(
		"delete",
		"/v1/catalog/saved-products/{productId}",
		"Remove a saved product",
		"Catalog Discovery",
		"bearer",
		response="MessageData",
		path_params=[("productId", "Product document id")],
		legacy="oan_a2c.api.v1.farmer.catalog.unsave_product",
	),
	R(
		"get",
		"/v1/me/dashboard",
		"Get the farmer's dashboard summary",
		"Catalog Discovery",
		"bearer",
		response="FarmerDashboardData",
		legacy="oan_a2c.api.v1.farmer.dashboard.get_dashboard_summary",
	),
	# --- 05 Applications (Farmer Self-Service) ---
	R(
		"post",
		"/v1/applications",
		"Create a draft application",
		"Applications (Farmer Self-Service)",
		"bearer",
		"CreateApplicationRequest",
		response="FarmerApplicationCreatedData",
		legacy="oan_a2c.api.v1.farmer.applications.create_application",
	),
	R(
		"get",
		"/v1/applications",
		"List the farmer's applications",
		"Applications (Farmer Self-Service)",
		"bearer",
		response="FarmerApplicationListData",
		legacy="oan_a2c.api.v1.farmer.applications.list_applications",
	),
	R(
		"get",
		"/v1/applications/{id}",
		"Get an application",
		"Applications (Farmer Self-Service)",
		"bearer",
		response="FarmerApplicationData",
		path_params=[("id", "Application document id")],
		legacy="oan_a2c.api.v1.farmer.applications.get_application",
	),
	R(
		"patch",
		"/v1/applications/{id}",
		"Update a draft application",
		"Applications (Farmer Self-Service)",
		"bearer",
		"UpdateApplicationRequest",
		response="MessageData",
		path_params=[("id", "Application document id")],
		legacy="oan_a2c.api.v1.farmer.applications.update_application",
	),
	R(
		"post",
		"/v1/applications/{id}/submit",
		"Submit an application to the bank",
		"Applications (Farmer Self-Service)",
		"bearer",
		response="MessageData",
		path_params=[("id", "Application document id")],
		legacy="oan_a2c.api.v1.farmer.applications.submit_application",
	),
	# --- 06 CRM Leads & Field Ops ---
	R(
		"post",
		"/v1/leads",
		"Create a lead",
		"CRM - Leads & Field Ops",
		"bearer",
		"CreateLeadRequest",
		response="MessageData",
		legacy="oan_a2c.api.v1.leads.create_lead",
	),
	R(
		"get",
		"/v1/leads",
		"List and search leads",
		"CRM - Leads & Field Ops",
		"bearer",
		query="ListLeads",
		response="LeadListData",
		paginated=True,
		legacy="oan_a2c.api.v1.leads.get_leads",
	),
	R(
		"get",
		"/v1/leads/summary",
		"Get lead counts by status",
		"CRM - Leads & Field Ops",
		"bearer",
		response="LeadSummaryData",
		legacy="oan_a2c.api.v1.leads.get_lead_summary",
	),
	R(
		"get",
		"/v1/leads/metadata",
		"Get lead form dropdown options",
		"CRM - Leads & Field Ops",
		"bearer",
		response="LeadMetadataData",
		legacy="oan_a2c.api.v1.leads.get_lead_metadata",
	),
	R(
		"get",
		"/v1/leads/assignable-users",
		"List assignable agents",
		"CRM - Leads & Field Ops",
		"bearer",
		query="AssignableUsers",
		response="AssignableUsersData",
		legacy="oan_a2c.api.v1.leads.get_assignable_users",
	),
	R(
		"patch",
		"/v1/leads/{id}/status",
		"Update a lead's status",
		"CRM - Leads & Field Ops",
		"bearer",
		"UpdateLeadStatusRequest",
		response="MessageData",
		path_params=[("id", "Lead document id")],
		legacy="oan_a2c.api.v1.leads.update_lead_status",
	),
	R(
		"patch",
		"/v1/leads/{id}/assignment",
		"Assign a lead to an agent",
		"CRM - Leads & Field Ops",
		"bearer",
		"AssignLeadRequest",
		response="MessageData",
		path_params=[("id", "Lead document id")],
		legacy="oan_a2c.api.v1.leads.assign_lead",
	),
	R(
		"post",
		"/v1/leads/{id}/comments",
		"Add a comment to a lead",
		"CRM - Leads & Field Ops",
		"bearer",
		"AddLeadCommentRequest",
		response="LeadCommentData",
		path_params=[("id", "Lead document id")],
		legacy="oan_a2c.api.v1.leads.add_lead_comment",
	),
	R(
		"get",
		"/v1/leads/{id}/timeline",
		"Get a lead's activity timeline",
		"CRM - Leads & Field Ops",
		"bearer",
		response="LeadTimelineData",
		path_params=[("id", "Lead document id")],
		legacy="oan_a2c.api.v1.leads.get_lead_timeline",
	),
	R(
		"get",
		"/v1/leads/{id}/call-logs",
		"Get a lead's call history",
		"CRM - Leads & Field Ops",
		"bearer",
		response="LeadCallLogsData",
		path_params=[("id", "Lead document id")],
		legacy="oan_a2c.api.v1.leads.get_lead_call_logs",
	),
	R(
		"get",
		"/v1/leads/{id}/credit-info",
		"List credit information for a lead",
		"CRM - Leads & Field Ops",
		"bearer",
		response="LeadCreditInfoListData",
		path_params=[("id", "Lead document id")],
		legacy="oan_a2c.api.v1.leads.get_lead_credit_infos",
	),
	R(
		"post",
		"/v1/leads/{id}/credit-info",
		"Add credit information for a lead",
		"CRM - Leads & Field Ops",
		"bearer",
		"AddLeadCreditInfoRequest",
		response="LeadCreditInfoCreatedData",
		path_params=[("id", "Lead document id")],
		legacy="oan_a2c.api.v1.leads.add_lead_credit_info",
	),
	R(
		"get",
		"/v1/visit-schedules",
		"List scheduled visits",
		"CRM - Leads & Field Ops",
		"bearer",
		query="ListVisitSchedules",
		response="VisitScheduleListData",
		paginated=True,
		legacy="oan_a2c.api.v1.leads.get_visit_schedules",
	),
	R(
		"post",
		"/v1/visit-schedules",
		"Schedule a field visit",
		"CRM - Leads & Field Ops",
		"bearer",
		"ScheduleVisitRequest",
		response="VisitScheduleCreatedData",
		legacy="oan_a2c.api.v1.leads.schedule_visit",
	),
	R(
		"patch",
		"/v1/visit-schedules/{id}/status",
		"Update a visit's status",
		"CRM - Leads & Field Ops",
		"bearer",
		"UpdateVisitStatusRequest",
		response="MessageData",
		path_params=[("id", "Visit schedule document id")],
		legacy="oan_a2c.api.v1.leads.update_visit_schedule_status",
	),
	# --- 07 Loan Underwriting ---
	R(
		"post",
		"/v1/loan-applications",
		"Convert a lead into a loan application",
		"Loan Underwriting",
		"bearer",
		"CreateLoanApplicationRequest",
		response="LoanApplicationCreatedData",
		legacy="oan_a2c.api.v1.loan_applications.create_loan_application",
	),
	R(
		"get",
		"/v1/loan-applications",
		"List loan applications",
		"Loan Underwriting",
		"bearer",
		query="ListLoanApplications",
		response="LoanApplicationListData",
		paginated=True,
		legacy="oan_a2c.api.v1.loan_applications.get_all_loans",
	),
	R(
		"get",
		"/v1/loan-applications/summary",
		"Get loan totals by pipeline status",
		"Loan Underwriting",
		"bearer",
		response="LoanSummaryData",
		legacy="oan_a2c.api.v1.loan_applications.get_loan_summary",
	),
	R(
		"get",
		"/v1/loan-applications/metadata",
		"Get loan status dropdown options",
		"Loan Underwriting",
		"bearer",
		response="LoanMetadataData",
		legacy="oan_a2c.api.v1.loan_applications.get_loan_metadata",
	),
	R(
		"get",
		"/v1/loan-applications/{id}/full-profile",
		"Get the full underwriting profile",
		"Loan Underwriting",
		"bearer",
		response="LoanApplicationFullProfileData",
		path_params=[("id", "Application document id")],
		legacy="oan_a2c.api.v1.loan_applications.get_full_profile",
	),
	R(
		"get",
		"/v1/loan-applications/{id}/basic-profile",
		"Get the applicant's basic profile",
		"Loan Underwriting",
		"bearer",
		response="LoanApplicationBasicProfileData",
		path_params=[("id", "Application document id")],
		legacy="oan_a2c.api.v1.loan_applications.get_basic_profile",
	),
	R(
		"patch",
		"/v1/loan-applications/{id}/basic-profile",
		"Update the applicant's basic profile",
		"Loan Underwriting",
		"bearer",
		"UpdateBasicProfileRequest",
		response="MessageData",
		path_params=[("id", "Application document id")],
		legacy="oan_a2c.api.v1.loan_applications.update_basic_profile",
	),
	R(
		"patch",
		"/v1/loan-applications/{id}/status",
		"Move an application to a new pipeline stage",
		"Loan Underwriting",
		"bearer",
		"UpdateLoanStatusRequest",
		response="MessageData",
		path_params=[("id", "Application document id")],
		legacy="oan_a2c.api.v1.loan_applications.update_loan_status",
	),
	R(
		"patch",
		"/v1/loan-applications/{id}/step",
		"Advance an application's processing step",
		"Loan Underwriting",
		"bearer",
		"UpdateLoanStepRequest",
		response="MessageData",
		path_params=[("id", "Application document id")],
		legacy="oan_a2c.api.v1.loan_applications.update_loan_step",
	),
	R(
		"patch",
		"/v1/loan-applications/{id}/officer",
		"Assign a loan officer",
		"Loan Underwriting",
		"bearer",
		"AssignLoanOfficerRequest",
		response="MessageData",
		path_params=[("id", "Application document id")],
		legacy="oan_a2c.api.v1.loan_applications.assign_loan_officer",
	),
	R(
		"get",
		"/v1/loan-applications/{id}/documents",
		"List supporting documents",
		"Loan Underwriting",
		"bearer",
		response="SupportingDocumentListData",
		path_params=[("id", "Application document id")],
		legacy="oan_a2c.api.v1.loan_applications.get_supporting_documents",
	),
	R(
		"post",
		"/v1/loan-applications/{id}/documents",
		"Upload a supporting document",
		"Loan Underwriting",
		"bearer",
		"UploadSupportingDocumentsRequest",
		response="SupportingDocumentUploadData",
		path_params=[("id", "Application document id")],
		legacy="oan_a2c.api.v1.loan_applications.upload_supporting_documents",
	),
	R(
		"get",
		"/v1/loan-applications/{id}/documents/{docId}/content",
		"Download a supporting document",
		"Loan Underwriting",
		"bearer",
		path_params=[("id", "Application document id"), ("docId", "Document file id")],
		legacy="oan_a2c.api.v1.loan_applications.download_supporting_document",
		binary=True,
	),
	R(
		"delete",
		"/v1/loan-applications/{id}/documents/{docId}",
		"Delete a supporting document",
		"Loan Underwriting",
		"bearer",
		response="MessageData",
		path_params=[("id", "Application document id"), ("docId", "Document file id")],
		legacy="oan_a2c.api.v1.loan_applications.delete_supporting_document",
	),
	# --- 08 Consent Management ---
	R(
		"get",
		"/v1/consent/farmers",
		"Search for a farmer by national ID",
		"Consent Management",
		"bearer",
		query="ConsentFarmerSearch",
		response="FarmerSearchData",
		legacy="oan_a2c.api.v1.consent.consent.search_farmer",
	),
	R(
		"get",
		"/v1/consent/reasons",
		"List approved consent reasons",
		"Consent Management",
		"bearer",
		response="ConsentReasonListData",
		legacy="oan_a2c.api.v1.consent.consent.get_consent_reasons",
	),
	R(
		"get",
		"/v1/consent/allowed-fields",
		"List the partner's allowed data fields",
		"Consent Management",
		"bearer",
		response="AllowedFieldListData",
		legacy="oan_a2c.api.v1.consent.consent.get_consent_allowed_fields",
	),
	R(
		"get",
		"/v1/consent/partners/me/allowed-field-ids",
		"Get allowed field ids",
		"Consent Management",
		"bearer",
		response="AllowedFieldListData",
		legacy="oan_a2c.api.v1.consent.consent.get_partner_allowed_data_field_ids",
	),
	R(
		"post",
		"/v1/consent/otp",
		"Request a verification code",
		"Consent Management",
		"bearer",
		"RequestOtpRequest",
		response="OtpRequestData",
		legacy="oan_a2c.api.v1.consent.consent.request_otp",
	),
	R(
		"post",
		"/v1/consent/otp/verify",
		"Verify a one-time code",
		"Consent Management",
		"bearer",
		"VerifyOtpRequest",
		response="OtpVerifyData",
		legacy="oan_a2c.api.v1.consent.consent.verify_otp",
	),
	R(
		"post",
		"/v1/consent/requests",
		"Submit a signed consent request",
		"Consent Management",
		"bearer",
		"SubmitConsentRequest",
		response="ConsentSubmitData",
		legacy="oan_a2c.api.v1.consent.consent.submit_consent",
	),
	R(
		"post",
		"/v1/webhooks/consent-data",
		"Receive a consent decision",
		"Consent Management",
		"partner-key",
		"ReceiveConsentDataRequest",
		response="ConsentWebhookAckData",
		legacy="oan_a2c.api.v1.webhook_consent_data.receive_consent_data",
	),
	# --- 09 Notifications ---
	R(
		"get",
		"/v1/notifications",
		"List notifications",
		"Notifications",
		"bearer",
		query="GetNotifications",
		response="NotificationListData",
		paginated=True,
		legacy="oan_a2c.api.v1.notifications.get_notifications",
	),
	R(
		"patch",
		"/v1/notifications/read",
		"Mark notifications read",
		"Notifications",
		"bearer",
		"MarkNotificationsReadRequest",
		response="NotificationUpdateData",
		legacy="oan_a2c.api.v1.notifications.mark_read",
	),
	R(
		"delete",
		"/v1/notifications",
		"Delete notifications",
		"Notifications",
		"bearer",
		"ClearNotificationsRequest",
		response="NotificationDeleteData",
		legacy="oan_a2c.api.v1.notifications.clear",
	),
	# --- 10 Inbound Webhooks ---
	R(
		"post",
		"/v1/webhooks/leads",
		"Receive a lead referral",
		"Inbound Webhooks",
		"partner-key",
		"LeadInboundRequest",
		response="LeadInboundData",
		legacy="oan_a2c.api.v1.webhooks.lead_inbound",
	),
]
assert len(ROUTES) == 94, f"expected 94 routes, got {len(ROUTES)}"

data(
	"ConsentReasonListData",
	OBJ(
		{"reasons": ARR(REF("ConsentReason"))},
		required=["reasons"],
		description="The source examples show `data` as a bare array of reasons; wrapped here in a named object for a cleaner generated client. See x-legacy-response-shape.",
	),
)
DATA_SCHEMAS["ConsentReasonListData"]["x-legacy-response-shape"] = (
	"data is a bare JSON array in the current backend, not {reasons: [...]}"
)
data("AllowedFieldListData", OBJ({"fields": ARR(REF("AllowedField"))}, required=["fields"]))
DATA_SCHEMAS["AllowedFieldListData"]["x-legacy-response-shape"] = (
	"data is a bare JSON array in the current backend, not {fields: [...]}"
)

# ---------------------------------------------------------------------------
# Assemble the OpenAPI document
# ---------------------------------------------------------------------------
TAGS = [
	{
		"name": "Identity & Access",
		"description": "Registration, login, token lifecycle, and the caller's own profile.",
	},
	{
		"name": "Bank Onboarding & Administration",
		"description": "Bank registration, KYC, organizational profile, and team management.",
	},
	{
		"name": "Bank Cataloging",
		"description": "Loan product authoring, marketplace taxonomy, and pipeline configuration.",
	},
	{"name": "Catalog Discovery", "description": "Farmer-facing marketplace browsing."},
	{
		"name": "Applications (Farmer Self-Service)",
		"description": "The farmer's own loan application draft and submission.",
	},
	{
		"name": "CRM - Leads & Field Ops",
		"description": "Lead capture, qualification, and field-visit scheduling (Development Agent).",
	},
	{
		"name": "Loan Underwriting",
		"description": "Bank-scoped loan application review and pipeline movement.",
	},
	{
		"name": "Consent Management",
		"description": "Fayda national ID consent capture and the inbound registry webhook.",
	},
	{"name": "Notifications", "description": "In-app notifications for the signed-in user."},
	{"name": "Inbound Webhooks", "description": "Server-to-server receivers for external systems."},
]


def response_object(route):
	if route["binary"]:
		return {
			"description": "The raw file content.",
			"content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
		}
	envelope = {"allOf": [{"$ref": "#/components/schemas/StandardSuccessResponse"}]}
	if route["response"]:
		data_override = {
			"type": "object",
			"properties": {"data": {"$ref": f"#/components/schemas/{route['response']}"}},
		}
		if route["paginated"]:
			data_override["properties"]["pagination"] = {"$ref": "#/components/schemas/Pagination"}
		envelope["allOf"].append(data_override)
	return {"description": "Successful operation.", "content": {"application/json": {"schema": envelope}}}


ERROR_REFS = {
	"400": {"$ref": "#/components/responses/400ValidationError"},
	"401": {"$ref": "#/components/responses/401Unauthorized"},
	"403": {"$ref": "#/components/responses/403Forbidden"},
	"404": {"$ref": "#/components/responses/404NotFound"},
	"429": {"$ref": "#/components/responses/429RateLimited"},
	"500": {"$ref": "#/components/responses/500InternalError"},
}

paths = {}
for r in ROUTES:
	params = []
	for pname, pdesc in r["path_params"]:
		params.append(
			{
				"name": pname,
				"in": "path",
				"required": True,
				"schema": {"type": "string"},
				"description": pdesc,
			}
		)
	if r["query"]:
		params.extend(QP[r["query"]])

	op = {
		"summary": r["summary"],
		"tags": [r["tag"]],
		"operationId": r["method"]
		+ "_"
		+ r["path"].strip("/").replace("/", "_").replace("{", "").replace("}", ""),
		"x-legacy-rpc-method": r["legacy"],
		"parameters": params or None,
		"responses": {str(r["status"]): response_object(r), **ERROR_REFS},
	}
	if op["parameters"] is None:
		del op["parameters"]

	if r["security"] == "public":
		op["security"] = []
	elif r["security"] == "partner-key":
		op["security"] = [{"PartnerApiKeyAuth": []}]
	else:
		op["security"] = [{"BearerAuth": []}]

	if r["request"]:
		op["requestBody"] = {
			"required": True,
			"content": {"application/json": {"schema": {"$ref": f"#/components/schemas/{r['request']}"}}},
		}

	paths.setdefault(r["path"], {})[r["method"]] = op

components_schemas = {
	"StandardSuccessResponse": OBJ(
		{
			"status": S(example="success"),
			"message": S(example="Success"),
			"data": OBJ(
				{}, description="Overridden per-operation via allOf; see the operation's response schema."
			),
			"meta": OBJ({}, description="Auxiliary metadata."),
			"pagination": REF("Pagination"),
			"request_id": S(format="uuid"),
		},
		required=["status", "message"],
	),
	"StandardErrorResponse": OBJ(
		{
			"status": S(example="error"),
			"message": S(example="Validation failed"),
			"code": S(
				enum=[
					"VALIDATION_ERROR",
					"AUTHENTICATION_ERROR",
					"PERMISSION_DENIED",
					"BANK_NOT_ACTIVE",
					"BANK_NOT_ONBOARDED",
					"PASSWORD_CHANGE_REQUIRED",
					"NOT_FOUND",
					"RATE_LIMITED",
					"INTERNAL_ERROR",
					"GENERIC_ERROR",
				]
			),
			"details": OBJ({}, description="Field-level validation errors or context."),
			"request_id": S(format="uuid"),
		},
		required=["status", "message", "code"],
	),
	"Pagination": OBJ(
		{
			"page": I(example=1),
			"page_size": I(example=20),
			"total": I(example=100),
			"total_pages": I(example=5),
			"has_next": B(example=True),
		},
		description="Present on paginated list responses; null otherwise.",
	),
}
components_schemas.update(REQ)
components_schemas.update(DATA_SCHEMAS)
components_schemas.update(SUBSCHEMAS)

components_responses = {
	"400ValidationError": {
		"description": "Request failed validation.",
		"content": {"application/json": {"schema": REF("StandardErrorResponse")}},
	},
	"401Unauthorized": {
		"description": "Missing or invalid credential.",
		"content": {"application/json": {"schema": REF("StandardErrorResponse")}},
	},
	"403Forbidden": {
		"description": "Access denied, insufficient permissions, or bank not active.",
		"content": {"application/json": {"schema": REF("StandardErrorResponse")}},
	},
	"404NotFound": {
		"description": "Resource not found.",
		"content": {"application/json": {"schema": REF("StandardErrorResponse")}},
	},
	"429RateLimited": {
		"description": "Rate limit exceeded.",
		"content": {"application/json": {"schema": REF("StandardErrorResponse")}},
	},
	"500InternalError": {
		"description": "Unexpected server error.",
		"content": {"application/json": {"schema": REF("StandardErrorResponse")}},
	},
}

doc = {
	"openapi": "3.0.3",
	"info": {
		"title": "OpenAgriNet Access to Credit (A2C) API",
		"version": "1.0.0",
		"description": (
			"REST API for the OpenAgriNet Access to Credit platform, covering bank onboarding, loan "
			"product cataloging, the farmer-facing marketplace, loan underwriting, Fayda national ID "
			"consent capture, and platform notifications.\n\n"
			"This document supersedes the team's original RPC-bridge specification "
			"(`POST /api/method/<dotted.path>` for every operation). Routes, verbs, and per-operation "
			"response schemas here reflect the REST facade design in `A2C_API_REST_Routing_Map.md`; "
			"the legacy method each operation maps to is preserved in the `x-legacy-rpc-method` "
			"extension on every operation for backend traceability during implementation.\n\n"
			"Response schemas are typed per operation. Where no documented example response existed "
			"in the source workflow guides, the schema is marked `x-schema-confidence: inferred` and "
			"should be confirmed against the live implementation before being treated as binding."
		),
		"contact": {"name": "OpenAgriNet Support", "email": "admin@openagrinet.org"},
		"license": {"name": "Apache 2.0", "url": "https://www.apache.org/licenses/LICENSE-2.0.html"},
	},
	"servers": [
		{"url": "https://api.a2c.openagrinet.org", "description": "Production"},
		{"url": "https://api.staging.a2c.openagrinet.org", "description": "Staging"},
	],
	"tags": TAGS,
	"security": [{"BearerAuth": []}],
	"paths": paths,
	"components": {
		"securitySchemes": {
			"BearerAuth": {
				"type": "http",
				"scheme": "bearer",
				"bearerFormat": "JWT",
				"description": "JWT access token issued by /v1/auth/login. The gateway validates signature and expiry only; role and bank-scope authorization are enforced by the platform.",
			},
			"PartnerApiKeyAuth": {
				"type": "apiKey",
				"in": "header",
				"name": "Authorization",
				"description": "Format: `token <api_key>:<api_secret>`. Used only by the two inbound webhook receivers; issued per partner and paired with IP allowlisting at the gateway.",
			},
		},
		"schemas": components_schemas,
		"responses": components_responses,
	},
}

with open(INTERNAL_SPEC_OUTPUT, "w") as f:
	f.write("# A2C API -- OpenAPI 3.0.3 (INTERNAL / engineering build artifact)\n")
	f.write("# Carries x-legacy-rpc-method + x-schema-confidence for the Kong/BFF build-out.\n")
	f.write(
		"# Generated from generate_openapi_spec.py -- do not hand-edit; change the generator and re-run.\n"
	)
	yaml.safe_dump(doc, f, sort_keys=False, default_flow_style=False, width=100, allow_unicode=True)

n_paths = len(paths)
n_ops = sum(len(v) for v in paths.values())
print(
	f"wrote {INTERNAL_SPEC_OUTPUT.name}: {n_paths} paths, {n_ops} operations, {len(components_schemas)} schemas",
	file=sys.stderr,
)

# ---------------------------------------------------------------------------
# Public variant: same 94 routes, same schemas -- with the two internal-only
# vendor extensions (x-legacy-rpc-method, x-schema-confidence) stripped, since
# they leak Frappe implementation details a third-party partner has no need
# of. Everything else (paths, verbs, request/response shapes, security,
# descriptions) is identical to the internal spec above.
# ---------------------------------------------------------------------------
STRIP_KEYS = {"x-legacy-rpc-method", "x-schema-confidence"}


def strip_extensions(o):
	if isinstance(o, dict):
		return {k: strip_extensions(v) for k, v in o.items() if k not in STRIP_KEYS}
	if isinstance(o, list):
		return [strip_extensions(v) for v in o]
	return o


public_doc = strip_extensions(doc)
public_doc["info"]["description"] = (
	"REST API for the OpenAgriNet Access to Credit platform, covering bank onboarding, loan "
	"product cataloging, the farmer-facing marketplace, loan underwriting, Fayda national ID "
	"consent capture, and platform notifications.\n\n"
	"This document supersedes the team's original RPC-bridge specification "
	"(`POST /api/method/<dotted.path>` for every operation). Routes, verbs, and per-operation "
	"response schemas here reflect the REST facade design in `A2C_API_REST_Routing_Map.md`.\n\n"
	"Response schemas are typed per operation from the platform's documented request/response "
	"examples; a small number of endpoints without a documented example carry a schema inferred "
	"from the corresponding request shape -- see each schema's own `description` field for a note "
	"where that applies."
)

with open(PUBLIC_SPEC_OUTPUT, "w") as f:
	f.write("# A2C API -- OpenAPI 3.0.3 (PUBLIC / partner-facing contract)\n")
	f.write("# Same 94 routes and schemas as openapi_v1.yaml, with internal-only vendor extensions\n")
	f.write(
		"# (x-legacy-rpc-method, x-schema-confidence) removed. Generated from generate_openapi_spec.py.\n"
	)
	yaml.safe_dump(public_doc, f, sort_keys=False, default_flow_style=False, width=100, allow_unicode=True)

print(
	f"wrote {PUBLIC_SPEC_OUTPUT.name}: {n_paths} paths, {n_ops} operations, {len(components_schemas)} schemas (extensions stripped)",
	file=sys.stderr,
)
