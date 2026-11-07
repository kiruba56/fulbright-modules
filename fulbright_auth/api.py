import frappe
from frappe import _
from frappe.rate_limiter import rate_limit

from fulbright_auth.async_email import start_immediately


def _email_link_limit() -> int:
	return 10


def _password_reset_limit() -> int:
	from frappe.core.doctype.user.user import get_password_reset_limit

	return get_password_reset_limit()


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=_email_link_limit, seconds=60 * 60)
def send_login_link(email: str):
	"""Check the account, then return. The sign-in email starts immediately."""
	email = (email or "").strip()
	if not email:
		frappe.throw(_("Email is required"))

	expiry = int(frappe.get_system_settings("login_with_email_link_expiry") or 10)
	if not frappe.db.exists("User", {"name": email, "enabled": 1}):
		return

	key = frappe.generate_hash()
	frappe.cache.set_value(
		f"one_time_login_key:{key}",
		email,
		expires_in_sec=expiry * 60,
	)

	app_url = (frappe.conf.get("fulbright_app_url") or "http://localhost:3000").rstrip("/")
	if not app_url.startswith(("http://", "https://")):
		frappe.throw(_("fulbright_app_url must start with http:// or https://"))

	link = f"{app_url}/login/link?key={key}"
	start_immediately(lambda: deliver_login_link(email, link, expiry))


def deliver_login_link(email: str, link: str, expiry: int):
	frappe.sendmail(
		recipients=email,
		subject=_("Sign in to Fulbright"),
		message=_(
			"Use this link to sign in. It expires in {0} minutes.<br><br><a href='{1}'>Sign in</a>"
		).format(expiry, link),
		now=True,
	)


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=_password_reset_limit, seconds=60 * 60)
def send_password_reset(email: str):
	"""Check the account, then return. The reset email starts immediately."""
	email = (email or "").strip()
	if not email:
		frappe.throw(_("Email is required"))

	if not frappe.db.exists("User", email):
		return "not found"

	user = frappe.get_doc("User", email)
	if user.name == "Administrator" or not user.enabled:
		return "disabled"

	try:
		user.validate_reset_password()
	except frappe.ValidationError:
		return "not allowed"

	account = user.name
	start_immediately(lambda: deliver_password_reset(account))


def deliver_password_reset(email: str):
	user = frappe.get_doc("User", email)
	if user.name == "Administrator" or not user.enabled:
		return

	user.validate_reset_password()
	user._reset_password(send_email=True)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def redeem_login_key(key: str):
	"""Turn a one-time key into an ERPNext session for the Fulbright server."""
	key = (key or "").strip()
	cache_key = f"one_time_login_key:{key}"
	email = frappe.cache.get_value(cache_key) if key else None
	if not email:
		frappe.throw(_("That sign-in link is invalid or expired."), frappe.AuthenticationError)

	frappe.cache.delete_value(cache_key)
	frappe.local.login_manager.login_as(email)
	user = frappe.session.user
	return {
		"email": user,
		"full_name": frappe.db.get_value("User", user, "full_name") or user,
		"sid": frappe.session.sid,
	}
