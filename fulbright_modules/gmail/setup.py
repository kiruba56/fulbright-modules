import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def ensure_custom_fields():
	"""Add Fulbright team-sharing flags on Email Account (idempotent)."""
	create_custom_fields(
		{
			"Email Account": [
				{
					"fieldname": "fulbright_team_view",
					"label": "Fulbright Team View",
					"fieldtype": "Check",
					"insert_after": "connected_user",
					"description": "Any logged-in ERPNext user can view this Fulbright Gmail inbox.",
				},
				{
					"fieldname": "fulbright_team_reply",
					"label": "Fulbright Team Reply",
					"fieldtype": "Check",
					"insert_after": "fulbright_team_view",
					"description": "Any logged-in ERPNext user can reply from this Fulbright Gmail inbox.",
				},
			]
		},
		update=True,
	)
	frappe.clear_cache(doctype="Email Account")
