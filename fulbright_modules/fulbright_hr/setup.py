import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def ensure_custom_fields():
	"""Fulbright Employee fields for unit, contract, and compensation (idempotent)."""
	create_custom_fields(
		{
			"Employee": [
				{
					"fieldname": "fulbright_hr_section",
					"label": "Fulbright HR",
					"fieldtype": "Section Break",
					"insert_after": "status",
					"collapsible": 1,
				},
				{
					"fieldname": "fulbright_unit",
					"label": "Fulbright Unit",
					"fieldtype": "Select",
					"options": "\nFulbright\nEdUSA",
					"insert_after": "fulbright_hr_section",
				},
				{
					"fieldname": "fulbright_employee_kind",
					"label": "Employee Kind",
					"fieldtype": "Select",
					"options": "\nStaff\nED",
					"insert_after": "fulbright_unit",
				},
				{
					"fieldname": "fulbright_attendance_protocol",
					"label": "Attendance Protocol",
					"fieldtype": "Data",
					"insert_after": "fulbright_employee_kind",
				},
				{
					"fieldname": "fulbright_contract_column",
					"fieldtype": "Column Break",
					"insert_after": "fulbright_attendance_protocol",
				},
				{
					"fieldname": "fulbright_contract_term",
					"label": "Contract Duration Term",
					"fieldtype": "Data",
					"insert_after": "fulbright_contract_column",
				},
				{
					"fieldname": "fulbright_contract_alert_days",
					"label": "Contract Expiry Alert (Days)",
					"fieldtype": "Int",
					"insert_after": "fulbright_contract_term",
					"default": "90",
				},
				{
					"fieldname": "fulbright_promotion_notes",
					"label": "Promotion / Career Notes",
					"fieldtype": "Small Text",
					"insert_after": "fulbright_contract_alert_days",
				},
				{
					"fieldname": "fulbright_compensation_section",
					"label": "Fulbright Compensation",
					"fieldtype": "Section Break",
					"insert_after": "fulbright_promotion_notes",
					"collapsible": 1,
				},
				{
					"fieldname": "fulbright_col_allowance",
					"label": "Cost of Living Allowance",
					"fieldtype": "Currency",
					"insert_after": "fulbright_compensation_section",
				},
				{
					"fieldname": "fulbright_housing_allowance",
					"label": "Housing Allowance",
					"fieldtype": "Currency",
					"insert_after": "fulbright_col_allowance",
				},
				{
					"fieldname": "fulbright_transport_allowance",
					"label": "Transport / Travel Allowance",
					"fieldtype": "Currency",
					"insert_after": "fulbright_housing_allowance",
				},
				{
					"fieldname": "fulbright_mobile_stipend",
					"label": "Mobile & Data Stipend",
					"fieldtype": "Currency",
					"insert_after": "fulbright_transport_allowance",
				},
				{
					"fieldname": "fulbright_comp_column",
					"fieldtype": "Column Break",
					"insert_after": "fulbright_mobile_stipend",
				},
				{
					"fieldname": "fulbright_ot_eligible",
					"label": "OT & Comp Time Eligible",
					"fieldtype": "Check",
					"insert_after": "fulbright_comp_column",
					"default": "1",
				},
				{
					"fieldname": "fulbright_max_bonus_base",
					"label": "Max Annual Bonus Base",
					"fieldtype": "Currency",
					"insert_after": "fulbright_ot_eligible",
				},
				{
					"fieldname": "fulbright_epf_scheme",
					"label": "EPF Scheme",
					"fieldtype": "Select",
					"options": "\nParticipant\nExempt",
					"insert_after": "fulbright_max_bonus_base",
					"default": "Participant",
				},
				{
					"fieldname": "fulbright_apit_mode",
					"label": "APIT Mode",
					"fieldtype": "Select",
					"options": "\nStandard\nExempt",
					"insert_after": "fulbright_epf_scheme",
					"default": "Standard",
				},
				{
					"fieldname": "fulbright_health_insurance_detail",
					"label": "Health & Medical Insurance",
					"fieldtype": "Data",
					"insert_after": "fulbright_apit_mode",
				},
				{
					"fieldname": "fulbright_bank_extra_section",
					"label": "Fulbright Bank Details",
					"fieldtype": "Section Break",
					"insert_after": "fulbright_health_insurance_detail",
					"collapsible": 1,
				},
				{
					"fieldname": "fulbright_bank_branch",
					"label": "Bank Branch",
					"fieldtype": "Data",
					"insert_after": "fulbright_bank_extra_section",
				},
				{
					"fieldname": "fulbright_bank_account_type",
					"label": "Bank Account Type",
					"fieldtype": "Data",
					"insert_after": "fulbright_bank_branch",
				},
				{
					"fieldname": "fulbright_salary_transfer_account",
					"label": "Salary Transfer Account",
					"fieldtype": "Data",
					"insert_after": "fulbright_bank_account_type",
					"description": "Accounting / QuickBooks cash-bank account label used for salary disbursement.",
				},
			]
		},
		update=True,
	)
	frappe.clear_cache(doctype="Employee")
