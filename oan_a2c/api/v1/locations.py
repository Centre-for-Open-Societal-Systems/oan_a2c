import frappe

from oan_a2c.api.utils import handle_api_errors, success_response


@frappe.whitelist(allow_guest=True)
@handle_api_errors
def get_regions():
	regions = frappe.get_all("A2C Region", fields=["name", "region_name", "region_code"])
	data = [{"value": r.name, "label": r.region_name, "code": r.region_code} for r in regions]
	return success_response(data=data)


@frappe.whitelist(allow_guest=True)
@handle_api_errors
def get_zones(region=None):
	filters = {}
	if region:
		filters["region"] = region

	zones = frappe.get_all("A2C Zone", filters=filters, fields=["name", "zone_name", "zone_code", "region"])
	data = [{"value": z.name, "label": z.zone_name, "code": z.zone_code, "region": z.region} for z in zones]
	return success_response(data=data)


@frappe.whitelist(allow_guest=True)
@handle_api_errors
def get_woredas(zone=None):
	filters = {}
	if zone:
		filters["zone"] = zone

	woredas = frappe.get_all(
		"A2C Woreda", filters=filters, fields=["name", "woreda_name", "woreda_code", "zone", "region"]
	)
	data = [
		{"value": w.name, "label": w.woreda_name, "code": w.woreda_code, "zone": w.zone, "region": w.region}
		for w in woredas
	]
	return success_response(data=data)


@frappe.whitelist(allow_guest=True)
@handle_api_errors
def get_kebeles(woreda=None):
	filters = {}
	if woreda:
		filters["woreda"] = woreda

	kebeles = frappe.get_all(
		"A2C Kebele",
		filters=filters,
		fields=["name", "kebele_name", "kebele_code", "woreda", "zone", "region"],
	)
	data = [
		{
			"value": k.name,
			"label": k.kebele_name,
			"code": k.kebele_code,
			"woreda": k.woreda,
			"zone": k.zone,
			"region": k.region,
		}
		for k in kebeles
	]
	return success_response(data=data)
