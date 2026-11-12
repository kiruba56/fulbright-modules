import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import cint, now_datetime, time_diff_in_seconds

from fulbright_modules.auth.async_email import start_immediately


def _email_link_limit() -> int:
	return 10


def _password_reset_limit() -> int:
	from frappe.core.doctype.user.user import get_password_reset_limit

	return get_password_reset_limit()


def _app_url() -> str:
	app_url = (frappe.conf.get("fulbright_app_url") or "http://localhost:3000").rstrip("/")
	if not app_url.startswith(("http://", "https://")):
		frappe.throw(_("fulbright_app_url must start with http:// or https://"))
	return app_url


def _set_password_link(key: str, *, from_reset: bool = False) -> str:
	base = f"{_app_url()}/set-password?key={key}"
	return f"{base}&from=reset" if from_reset else base


def _sha256_hash(value: str) -> str:
	from frappe.utils import sha256_hash

	return sha256_hash(value)


def _issue_reset_key(user) -> str:
	"""Create a reset key without sending Frappe's desk email."""
	key = frappe.generate_hash()
	user.db_set("reset_password_key", _sha256_hash(key))
	user.db_set("last_reset_password_key_generated_on", now_datetime())
	return key


def _user_from_reset_key(key: str) -> str | None:
	key = (key or "").strip()
	if not key:
		return None
	return frappe.db.get_value("User", {"reset_password_key": _sha256_hash(key)})


def _reset_key_expired(user_name: str) -> bool:
	generated_on = frappe.db.get_value(
		"User", user_name, "last_reset_password_key_generated_on"
	)
	if not generated_on:
		return True
	hours = cint(frappe.get_system_settings("reset_password_link_expiry") or 24)
	return time_diff_in_seconds(now_datetime(), generated_on) > hours * 60 * 60


@frappe.whitelist(allow_guest=True, methods=["POST"])
def check_password_reset_key(key: str):
	"""Return whether a Fulbright /set-password key is still usable (does not consume it)."""
	key = (key or "").strip()
	if not key:
		return {"valid": False, "reason": "missing"}

	user = _user_from_reset_key(key)
	if not user:
		return {"valid": False, "reason": "used"}

	if not frappe.db.get_value("User", user, "enabled"):
		return {"valid": False, "reason": "disabled"}

	if _reset_key_expired(user):
		return {"valid": False, "reason": "expired"}

	return {"valid": True}


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

	link = f"{_app_url()}/login/link?key={key}"
	start_immediately(lambda: deliver_login_link(email, link, expiry))


def deliver_login_link(email: str, link: str, expiry: int):
	frappe.sendmail(
		recipients=email,
		subject=_("Sign in to Fulbright"),
		message=_(
			"Use this link to sign in. It expires in {0} minutes.<br><br><a href='{1}'>Sign in</a>"
		).format(expiry, link),
		now=True,
		with_container=True,
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


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=_password_reset_limit, seconds=60 * 60)
def send_password_reset_invite(email: str):
	"""Admin: email a Fulbright /set-password reset link (clear errors)."""
	email = (email or "").strip()
	if not email:
		frappe.throw(_("Email is required"))

	if frappe.session.user in (None, "Guest"):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	if not frappe.db.exists("User", email):
		frappe.throw(_("User {0} does not exist").format(email))

	user = frappe.get_doc("User", email)
	if user.name == "Administrator" or not user.enabled:
		frappe.throw(_("This account can't be reset."))

	try:
		user.validate_reset_password()
	except frappe.ValidationError:
		frappe.throw(_("Password reset is not allowed for this account."))

	account = user.name
	start_immediately(lambda: deliver_password_reset(account))
	return "queued"


def deliver_password_reset(email: str):
	user = frappe.get_doc("User", email)
	if user.name == "Administrator" or not user.enabled:
		return

	user.validate_reset_password()
	key = _issue_reset_key(user)
	link = _set_password_link(key, from_reset=True)
	frappe.sendmail(
		recipients=email,
		subject=_("Reset your Fulbright password"),
		message=_(
			"Hi {0}, use the link below to set a new password for your Fulbright account."
			"<br><br><b>Login ID:</b> {1}"
			"<br><br><a href='{2}'>Set password</a>"
			"<br><br>If you did not request this, you can ignore this email."
		).format(user.first_name or user.full_name or email, email, link),
		now=True,
		with_container=True,
	)


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=_password_reset_limit, seconds=60 * 60)
def send_welcome_invite(email: str):
	"""Email a Next.js /set-password invite after HR creates a User."""
	email = (email or "").strip()
	if not email:
		frappe.throw(_("Email is required"))

	if frappe.session.user in (None, "Guest"):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	if not frappe.db.exists("User", email):
		frappe.throw(_("User {0} does not exist").format(email))

	user = frappe.get_doc("User", email)
	if user.name == "Administrator" or not user.enabled:
		frappe.throw(_("This account can't be invited."))

	account = user.name
	start_immediately(lambda: deliver_welcome_invite(account))
	return "queued"


def deliver_welcome_invite(email: str):
	user = frappe.get_doc("User", email)
	if user.name == "Administrator" or not user.enabled:
		return

	key = _issue_reset_key(user)
	link = _set_password_link(key)
	name = user.first_name or user.full_name or email
	frappe.sendmail(
		recipients=email,
		subject=_("Welcome to Fulbright"),
		message=_(
			"Hi {0}, an account has been created for you on Fulbright."
			"<br><br>Before you can sign in, set your password."
			"<br><br><b>Login ID:</b> {1}"
			"<br><br><a href='{2}'>Set your password</a>"
			"<br><br>If you weren't expecting this account, you can ignore this email."
		).format(name, email, link),
		now=True,
		with_container=True,
	)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def set_password_with_key(key: str, new_password: str):
	"""Set password from a Fulbright invite/reset key and return an ERPNext session."""
	from frappe.core.doctype.user.user import update_password as stock_update_password

	key = (key or "").strip()
	new_password = new_password or ""
	if not key or not new_password:
		frappe.throw(_("Reset key and password are required."))

	user = _user_from_reset_key(key)
	if not user:
		frappe.throw(
			_("That password link is invalid or has already been used."),
			frappe.AuthenticationError,
		)

	if _reset_key_expired(user):
		frappe.throw(
			_("That password link has expired. Ask an admin to send a new one."),
			frappe.AuthenticationError,
		)

	# Stock API validates strength, clears the key, and logs the user in.
	stock_update_password(new_password=new_password, key=key, logout_all_sessions=1)

	session_user = frappe.session.user
	if not session_user or session_user == "Guest":
		frappe.local.login_manager.login_as(user)
		session_user = user

	return {
		"email": session_user,
		"full_name": frappe.db.get_value("User", session_user, "full_name") or session_user,
		"sid": frappe.session.sid,
	}


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
