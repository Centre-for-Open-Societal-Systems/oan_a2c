# Copyright (c) 2026, OAN and contributors
# For license information, please see license.txt

import re

import frappe


def normalize_tin(raw: str) -> str:
	"""Normalize TIN - uppercase, strip spaces/dashes/punctuation to one canonical form"""
	if not raw:
		return ""
	# uppercase and strip non-alphanumeric characters
	normalized = re.sub(r"[^A-Z0-9]", "", str(raw).upper())
	return normalized
