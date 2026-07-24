import frappe

from oan_a2c.a2c_marketplace.stats_cache import _compute_from_db, compute_and_set, get_stats_for_bank
from oan_a2c.api.utils import bank_scoped, handle_api_errors, success_response


@frappe.whitelist()
@handle_api_errors
@bank_scoped(require_bank=False)
def get_stats(bank):
	if bank is None:
		# Unbound admin: sees all banks — always query DB since no single bank cache applies
		stats = _compute_from_db(None)
	else:
		stats = get_stats_for_bank(bank) or compute_and_set(bank)

	return success_response(data={"stats": stats})
