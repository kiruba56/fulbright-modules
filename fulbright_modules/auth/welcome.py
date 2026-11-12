"""Keep User onboarding inside the Fulbright Next.js app."""

import frappe


def suppress_stock_welcome_mail(doc, method=None):
	"""Block Frappe's desk welcome / update-password email.

	Employee create_user still inserts a User; Fulbright sends its own
	invite to /set-password when HR opts in.
	"""
	doc.flags.no_welcome_mail = True
	# Field is checked on insert in some Frappe versions.
	if hasattr(doc, "send_welcome_email"):
		doc.send_welcome_email = 0
