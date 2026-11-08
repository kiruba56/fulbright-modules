import base64
import hashlib
import json
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


def _resolve_connected_app_name() -> str:
	"""Pick the Google Connected App without requiring a bench set-config in normal setups.

	Order:
	1. site config `fulbright_gmail_connected_app` (explicit override)
	2. Connected App named `Fulbright Gmail`
	3. The only Connected App whose provider is Google (or Google auth URI)
	"""
	configured = (frappe.conf.get("fulbright_gmail_connected_app") or "").strip()
	if configured:
		if not frappe.db.exists("Connected App", configured):
			frappe.throw(
				_("Connected App {0} from site config was not found.").format(frappe.bold(configured))
			)
		return configured

	if frappe.db.exists("Connected App", DEFAULT_CONNECTED_APP_NAME):
		return DEFAULT_CONNECTED_APP_NAME

	google_apps: list[str] = []
	for app in frappe.get_all(
		"Connected App",
		fields=["name", "provider_name", "authorization_uri"],
	):
		provider = (app.provider_name or "").strip().lower()
		auth_uri = (app.authorization_uri or "").lower()
		if provider == "google" or "accounts.google.com" in auth_uri:
			google_apps.append(app.name)

	if len(google_apps) == 1:
		return google_apps[0]

	if len(google_apps) > 1:
		frappe.throw(
			_(
				"Multiple Google Connected Apps found ({0}). Rename one to {1} or set "
				"fulbright_gmail_connected_app in site config."
			).format(", ".join(google_apps), frappe.bold(DEFAULT_CONNECTED_APP_NAME))
		)

	frappe.throw(
		_(
			"No Google Connected App found. Create one with Provider Name {0}, "
			"or name it {1}."
		).format(frappe.bold("Google"), frappe.bold(DEFAULT_CONNECTED_APP_NAME))
	)


def _app_url() -> str:
	app_url = (frappe.conf.get("fulbright_app_url") or "http://localhost:3000").rstrip("/")
	if not app_url.startswith(("http://", "https://")):
		frappe.throw(_("fulbright_app_url must start with http:// or https://"))
	return app_url


def _redirect_uri() -> str:
	return f"{_app_url()}/settings/email/callback"


def _get_connected_app():
	return frappe.get_doc("Connected App", _resolve_connected_app_name())


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
	filters = {"connected_app": _resolve_connected_app_name(), "service": "GMail"}
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


def _normalize_sharing(team_view, team_reply) -> tuple[int, int]:
	"""Reply implies view; clearing view also clears reply."""
	view = 1 if team_view else 0
	reply = 1 if team_reply else 0
	if reply:
		view = 1
	if not view:
		reply = 0
	return view, reply


def _owner_label(connected_user: str | None) -> str | None:
	if not connected_user:
		return None
	full_name = frappe.db.get_value("User", connected_user, "full_name")
	if full_name:
		return full_name
	return connected_user


def _accessible_account(email: str, user: str, need: str = "view"):
	"""Return a Fulbright Gmail Email Account the user may view or reply with."""
	email = (email or "").strip()
	if not email:
		frappe.throw(_("Email is required"))
	if need not in ("view", "reply"):
		frappe.throw(_("Invalid access need."))

	row = frappe.db.get_value(
		"Email Account",
		{**_account_filters(), "email_id": email},
		[
			"name",
			"connected_user",
			"fulbright_team_view",
			"fulbright_team_reply",
		],
		as_dict=True,
	)
	if not row:
		frappe.throw(_("That Gmail account is not connected."), frappe.PermissionError)

	is_owner = row.connected_user == user
	team_view = int(row.fulbright_team_view or 0)
	team_reply = int(row.fulbright_team_reply or 0)

	if is_owner:
		return frappe.get_doc("Email Account", row.name)

	if need == "reply" and team_reply:
		return frappe.get_doc("Email Account", row.name)

	if need == "view" and (team_view or team_reply):
		return frappe.get_doc("Email Account", row.name)

	frappe.throw(_("You do not have access to that Gmail account."), frappe.PermissionError)


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=_connect_limit, seconds=60 * 60, user_based=True)
def start_gmail_connect():
	"""Return a Google authorization URL. Cache state + PKCE verifier for 10 minutes."""
	user = _require_logged_in()
	app = _get_connected_app()
	client_id = app.client_id
	if not client_id:
		frappe.throw(_("Connected App {0} is missing a Client ID.").format(_resolve_connected_app_name()))

	authorization_uri = (app.authorization_uri or "").strip()
	if not authorization_uri:
		frappe.throw(_("Connected App {0} is missing an Authorization URI.").format(_resolve_connected_app_name()))

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

	app_name = _resolve_connected_app_name()
	existing = frappe.db.get_value(
		"Email Account",
		email,
		["connected_app", "connected_user"],
		as_dict=True,
	) or frappe.db.get_value(
		"Email Account",
		{"email_id": email},
		["name", "connected_app", "connected_user"],
		as_dict=True,
	)
	if existing:
		if existing.connected_app != app_name:
			frappe.throw(
				_("{0} is already used by another Email Account in ERPNext.").format(email)
			)
		if existing.connected_user != user:
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
		account.connected_app = app_name
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
				"connected_app": _resolve_connected_app_name(),
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
	"""Return owned and team-shared Fulbright Gmail accounts. Never returns tokens."""
	user = _require_logged_in()
	base = _account_filters()
	fields = [
		"name",
		"email_id",
		"creation",
		"connected_user",
		"fulbright_team_view",
		"fulbright_team_reply",
	]

	owned = frappe.get_all(
		"Email Account",
		filters={**base, "connected_user": user},
		fields=fields,
		order_by="creation desc",
	)
	shared = frappe.get_all(
		"Email Account",
		filters=[
			["connected_app", "=", base["connected_app"]],
			["service", "=", "GMail"],
			["connected_user", "!=", user],
			["fulbright_team_view", "=", 1],
		],
		fields=fields,
		order_by="creation desc",
	)
	shared_reply = frappe.get_all(
		"Email Account",
		filters=[
			["connected_app", "=", base["connected_app"]],
			["service", "=", "GMail"],
			["connected_user", "!=", user],
			["fulbright_team_reply", "=", 1],
		],
		fields=fields,
		order_by="creation desc",
	)

	by_name: dict[str, dict] = {}
	for row in [*owned, *shared, *shared_reply]:
		by_name[row.name] = row

	accounts = []
	for row in sorted(
		by_name.values(),
		key=lambda item: item.creation or "",
		reverse=True,
	):
		team_view, team_reply = _normalize_sharing(
			row.fulbright_team_view, row.fulbright_team_reply
		)
		is_owner = row.connected_user == user
		accounts.append(
			{
				"email": row.email_id,
				"status": "connected" if _has_refresh_token(row.name) else "needs_reconnect",
				"connected_at": str(row.creation) if row.creation else None,
				"is_owner": is_owner,
				"owner": _owner_label(row.connected_user),
				"team_view": bool(team_view),
				"team_reply": bool(team_reply),
			}
		)
	return accounts


@frappe.whitelist(methods=["POST"])
def set_gmail_sharing(
	email: str | None = None,
	team_view: int | bool | str | None = 0,
	team_reply: int | bool | str | None = 0,
):
	"""Owner-only: set team view / reply flags. Reply implies view."""
	user = _require_logged_in()
	account = _owned_account(email, user)
	view, reply = _normalize_sharing(
		frappe.utils.cint(team_view),
		frappe.utils.cint(team_reply),
	)
	account.fulbright_team_view = view
	account.fulbright_team_reply = reply
	account.save(ignore_permissions=True)
	return {
		"ok": True,
		"email": account.email_id,
		"team_view": bool(view),
		"team_reply": bool(reply),
	}


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


def get_access_token(
	account: str,
	user: str | None = None,
	need: str = "view",
) -> str:
	"""Return a usable access token for a Fulbright Gmail Email Account. Internal helper.

	When ``user`` is set, enforce owner or team view/reply access first.
	"""
	if not account:
		frappe.throw(_("Account is required"))

	if user:
		doc = frappe.get_doc("Email Account", account)
		_accessible_account(doc.email_id, user, need=need or "view")

	cached = frappe.cache.get_value(_token_key(account))
	if cached and cached.get("access_token"):
		return cached["access_token"]

	with filelock(f"fulbright_gmail_{account}"):
		cached = frappe.cache.get_value(_token_key(account))
		if cached and cached.get("access_token"):
			return cached["access_token"]

		doc = frappe.get_doc("Email Account", account)
		if doc.connected_app != _resolve_connected_app_name():
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


def _normalize_email(value) -> str | None:
	if isinstance(value, str) and value.strip():
		return value.strip().lower()
	return None


def _email_from_token(app, token: dict) -> str | None:
	"""Resolve the mailbox address from the OAuth token.

	Connected Apps often only grant https://mail.google.com/, which cannot call
	OpenID userinfo. Prefer userinfo when available, then id_token, then Gmail profile.
	"""
	access_token = token.get("access_token")
	if not access_token:
		return None

	userinfo_uri = (app.userinfo_uri or "https://openidconnect.googleapis.com/v1/userinfo").strip()
	response = requests.get(
		userinfo_uri,
		headers={"Authorization": f"Bearer {access_token}"},
		timeout=30,
	)
	if response.ok:
		email = _normalize_email(_json_or_empty(response).get("email"))
		if email:
			return email

	id_token = token.get("id_token")
	if isinstance(id_token, str) and id_token.count(".") == 2:
		try:
			payload_part = id_token.split(".")[1]
			padding = "=" * (-len(payload_part) % 4)
			claims = json.loads(base64.urlsafe_b64decode(payload_part + padding))
			email = _normalize_email(claims.get("email"))
			if email:
				return email
		except Exception:
			pass

	# Works with https://mail.google.com/ and gmail.* scopes.
	profile = requests.get(
		"https://gmail.googleapis.com/gmail/v1/users/me/profile",
		headers={"Authorization": f"Bearer {access_token}"},
		timeout=30,
	)
	if profile.ok:
		email = _normalize_email(_json_or_empty(profile).get("emailAddress"))
		if email:
			return email

	frappe.throw(_("Could not read the Google account email."))



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
