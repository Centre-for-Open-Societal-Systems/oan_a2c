import frappe

from oan_a2c.a2c_marketplace.permissions import bank_scoped
from oan_a2c.a2c_marketplace.stats_cache import get_dashboard_stats
from oan_a2c.api.utils import handle_api_errors, success_response


@frappe.whitelist()
@handle_api_errors
@bank_scoped(require_bank=False)
def get_stats(bank: str | None = None):
	# bank=None (unbound admin) => {"stats": totals, "by_bank": [...]};
	# a bank code => {"stats": <that bank>}. Both cache-first.
	return success_response(data=get_dashboard_stats(bank))
