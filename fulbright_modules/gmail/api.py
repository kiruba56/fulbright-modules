import base64
import hashlib
import secrets
from urllib.parse import urlencode

import frappe
import requests
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils.synchronization import filelock

DEFAULT_CONNECTED_APP_NAME = "Fulbright Gmail"
STATE_TTL_SECONDS = 10 * 60
DEFAULT_SCOPES = [
	"openid",
	"email",
	"https://www.googleapis.com/auth/gmail.modify",
	"https://www.googleapis.com/auth/gmail.send",
]


def _connect_limit() -> int:
	return 20


def _require_logged_in() -> str:
	user = frappe.session.user
	if not user or user == "Guest":
		frappe.throw(_("Sign in required."), frappe.AuthenticationError)
	return user


def _connected_app_name() -> str:
	return (frappe.conf.get("fulbright_gmail_connected_app") or DEFAULT_CONNECTED_APP_NAME).strip()


def _app_url() -> str:
	app_url = (frappe.conf.get("fulbright_app_url") or "http://localhost:3000").rstrip("/")
	if not app_url.startswith(("http://", "https://")):
		frappe.throw(_("fulbright_app_url must start with http:// or https://"))
	return app_url


def _redirect_uri() -> str:
	return f"{_app_url()}/settings/email/callback"


def _get_connected_app():
	name = _connected_app_name()
	if not name or not frappe.db.exists("Connected App", name):
		frappe.throw(
			_("Connected App {0} is not set up. Create it in ERPNext first.").format(
				frappe.bold(name or DEFAULT_CONNECTED_APP_NAME)
			)
		)
	return frappe.get_doc("Connected App", name)


def _scopes(app) -> list[str]:
	scopes = [row.scope for row in (app.scopes or []) if row.scope]
	return scopes or list(DEFAULT_SCOPES)


def _pkce_pair() -> tuple[str, str]:
	verifier = secrets.token_urlsafe(64)
	digest = hashlib.sha256(verifier.encode("ascii")).digest()
	challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
	return verifier, challenge


def _state_key(state: str) -> str:
	return f"fulbright_gmail_state:{state}"


def _token_key(account: str) -> str:
	return f"fulbright_gmail_token:{account}"


def _account_filters(user: str | None = None) -> dict:
	filters = {"connected_app": _connected_app_name(), "service": "GMail"}
	if user:
		filters["connected_user"] = user
	return filters


def _has_refresh_token(account_name: str) -> bool:
	doc = frappe.get_doc("Email Account", account_name)
	try:
		return bool(doc.get_password("password", raise_exception=False))
	except Exception:
		return False


def _owned_account(email: str, user: str):
	email = (email or "").strip()
	if not email:
		frappe.throw(_("Email is required"))

	name = frappe.db.get_value(
		"Email Account",
		{**_account_filters(user), "email_id": email},
		"name",
	)
	if not name:
		frappe.throw(_("That Gmail account is not connected."), frappe.PermissionError)
	return frappe.get_doc("Email Account", name)


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=_connect_limit, seconds=60 * 60)
def start_gmail_connect():
	"""Return a Google authorization URL. Cache state + PKCE verifier for 10 minutes."""
	user = _require_logged_in()
	app = _get_connected_app()
	client_id = app.client_id
	if not client_id:
		frappe.throw(_("Connected App {0} is missing a Client ID.").format(_connected_app_name()))

	authorization_uri = (app.authorization_uri or "").strip()
	if not authorization_uri:
		frappe.throw(_("Connected App {0} is missing an Authorization URI.").format(_connected_app_name()))

	verifier, challenge = _pkce_pair()
	state = secrets.token_urlsafe(24)
	frappe.cache.set_value(
		_state_key(state),
		{"user": user, "verifier": verifier},
		expires_in_sec=STATE_TTL_SECONDS,
	)

	params = {
		"client_id": client_id,
		"redirect_uri": _redirect_uri(),
		"response_type": "code",
		"scope": " ".join(_scopes(app)),
		"access_type": "offline",
		"prompt": "consent",
		"include_granted_scopes": "true",
		"state": state,
		"code_challenge": challenge,
		"code_challenge_method": "S256",
	}
	return {"auth_url": f"{authorization_uri}?{urlencode(params)}"}


@frappe.whitelist(methods=["POST"])
def finish_gmail_connect(code: str | None = None, state: str | None = None):
	"""Exchange the Google code, then create or update the Email Account for this mailbox."""
	user = _require_logged_in()
	code = (code or "").strip()
	state = (state or "").strip()
	if not code or not state:
		frappe.throw(_("Missing OAuth code or state."))

	note = frappe.cache.get_value(_state_key(state))
	frappe.cache.delete_value(_state_key(state))
	if not note or note.get("user") != user:
		frappe.throw(_("That connect link expired. Try again."))

	app = _get_connected_app()
	token = _exchange_code(app, code, note["verifier"])
	email = _email_from_token(app, token)
	if not email:
		frappe.throw(_("Google did not return an email address."))

	existing_owner = frappe.db.get_value(
		"Email Account",
		{"email_id": email, "connected_app": _connected_app_name()},
		"connected_user",
	)
	if existing_owner and existing_owner != user:
		frappe.throw(_("That Gmail account is already connected to another user."))

	refresh_token = token.get("refresh_token")
	account_name = frappe.db.get_value(
		"Email Account",
		{**_account_filters(user), "email_id": email},
		"name",
	)

	if not refresh_token:
		if account_name and _has_refresh_token(account_name):
			refresh_token = frappe.get_doc("Email Account", account_name).get_password(
				"password", raise_exception=False
			)
		else:
			frappe.throw(
				_(
					"Google did not return a refresh token. Remove Fulbright access in your "
					"Google Account and try again."
				)
			)

	if account_name:
		account = frappe.get_doc("Email Account", account_name)
		account.password = refresh_token
		account.connected_user = user
		account.connected_app = _connected_app_name()
		account.service = "GMail"
		account.auth_method = "Basic"
		account.enable_incoming = 0
		account.enable_outgoing = 0
		account.default_incoming = 0
		account.default_outgoing = 0
		account.awaiting_password = 0
		account.save(ignore_permissions=True)
	else:
		account = frappe.get_doc(
			{
				"doctype": "Email Account",
				"email_id": email,
				"email_account_name": email,
				"service": "GMail",
				"auth_method": "Basic",
				"password": refresh_token,
				"connected_user": user,
				"connected_app": _connected_app_name(),
				"enable_incoming": 0,
				"enable_outgoing": 0,
				"default_incoming": 0,
				"default_outgoing": 0,
				"awaiting_password": 0,
			}
		)
		account.insert(ignore_permissions=True)

	_cache_access_token(account.name, token)
	return {"email": email}


@frappe.whitelist(methods=["POST"])
def list_gmail_accounts():
	"""Return this user's Fulbright Gmail accounts. Never returns tokens."""
	user = _require_logged_in()
	rows = frappe.get_all(
		"Email Account",
		filters=_account_filters(user),
		fields=["name", "email_id", "creation", "modified"],
		order_by="creation desc",
	)
	accounts = []
	for row in rows:
		accounts.append(
			{
				"email": row.email_id,
				"status": "connected" if _has_refresh_token(row.name) else "needs_reconnect",
				"connected_at": str(row.creation) if row.creation else None,
			}
		)
	return accounts


@frappe.whitelist(methods=["POST"])
def disconnect_gmail(email: str | None = None):
	"""Revoke the Google grant (best effort) and delete the Email Account."""
	user = _require_logged_in()
	account = _owned_account(email, user)
	refresh_token = account.get_password("password", raise_exception=False)
	if refresh_token:
		_revoke_token(refresh_token)
	frappe.cache.delete_value(_token_key(account.name))
	frappe.delete_doc("Email Account", account.name, ignore_permissions=True, force=True)
	return {"ok": True, "email": email}


def get_access_token(account: str) -> str:
	"""Return a usable access token for a Fulbright Gmail Email Account. Internal helper."""
	if not account:
		frappe.throw(_("Account is required"))

	cached = frappe.cache.get_value(_token_key(account))
	if cached and cached.get("access_token"):
		return cached["access_token"]

	with filelock(f"fulbright_gmail_{account}"):
		cached = frappe.cache.get_value(_token_key(account))
		if cached and cached.get("access_token"):
			return cached["access_token"]

		doc = frappe.get_doc("Email Account", account)
		if doc.connected_app != _connected_app_name():
			frappe.throw(_("Not a Fulbright Gmail account."))

		refresh_token = doc.get_password("password", raise_exception=False)
		if not refresh_token:
			frappe.throw(_("That Gmail account needs reconnecting."))

		app = _get_connected_app()
		try:
			token = _refresh_access_token(app, refresh_token)
		except Exception as exc:
			if "invalid_grant" in str(exc).lower():
				doc.password = ""
				doc.save(ignore_permissions=True)
				frappe.cache.delete_value(_token_key(account))
				frappe.throw(_("That Gmail account needs reconnecting."))
			raise

		if token.get("refresh_token") and token["refresh_token"] != refresh_token:
			doc.password = token["refresh_token"]
			doc.save(ignore_permissions=True)

		_cache_access_token(account, token)
		access_token = token.get("access_token")
		if not access_token:
			frappe.throw(_("Google did not return an access token."))
		return access_token


def _exchange_code(app, code: str, verifier: str) -> dict:
	client_secret = app.get_password("client_secret", raise_exception=False)
	data = {
		"client_id": app.client_id,
		"code": code,
		"code_verifier": verifier,
		"grant_type": "authorization_code",
		"redirect_uri": _redirect_uri(),
	}
	if client_secret:
		data["client_secret"] = client_secret

	token_uri = (app.token_uri or "https://oauth2.googleapis.com/token").strip()
	response = requests.post(token_uri, data=data, timeout=30)
	payload = _json_or_empty(response)
	if not response.ok:
		error = payload.get("error") or response.text or "token_exchange_failed"
		description = payload.get("error_description") or ""
		frappe.throw(_("Google token exchange failed: {0} {1}").format(error, description).strip())
	return payload


def _refresh_access_token(app, refresh_token: str) -> dict:
	client_secret = app.get_password("client_secret", raise_exception=False)
	data = {
		"client_id": app.client_id,
		"grant_type": "refresh_token",
		"refresh_token": refresh_token,
	}
	if client_secret:
		data["client_secret"] = client_secret

	token_uri = (app.token_uri or "https://oauth2.googleapis.com/token").strip()
	response = requests.post(token_uri, data=data, timeout=30)
	payload = _json_or_empty(response)
	if not response.ok:
		error = payload.get("error") or response.text or "token_refresh_failed"
		raise RuntimeError(error)
	return payload


def _email_from_token(app, token: dict) -> str | None:
	access_token = token.get("access_token")
	if not access_token:
		return None

	userinfo_uri = (app.userinfo_uri or "https://openidconnect.googleapis.com/v1/userinfo").strip()
	response = requests.get(
		userinfo_uri,
		headers={"Authorization": f"Bearer {access_token}"},
		timeout=30,
	)
	payload = _json_or_empty(response)
	if not response.ok:
		frappe.throw(_("Could not read the Google account email."))
	email = payload.get("email")
	return email.strip().lower() if isinstance(email, str) and email.strip() else None


def _cache_access_token(account: str, token: dict) -> None:
	access_token = token.get("access_token")
	if not access_token:
		return
	expires_in = int(token.get("expires_in") or 3600)
	# Refresh a minute early so callers rarely hit an expired token.
	ttl = max(expires_in - 60, 60)
	frappe.cache.set_value(
		_token_key(account),
		{"access_token": access_token},
		expires_in_sec=ttl,
	)


def _revoke_token(token: str) -> None:
	try:
		requests.post(
			"https://oauth2.googleapis.com/revoke",
			data={"token": token},
			headers={"Content-Type": "application/x-www-form-urlencoded"},
			timeout=15,
		)
	except Exception:
		frappe.log_error(title="Fulbright Gmail revoke failed")


def _json_or_empty(response) -> dict:
	try:
		payload = response.json()
		return payload if isinstance(payload, dict) else {}
	except Exception:
		return {}
