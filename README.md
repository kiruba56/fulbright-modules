# Fulbright Modules

Shared Frappe app for Fulbright. One repo, one `apps.json` entry, one `install-app`. New features go in as modules under `fulbright_modules/`.

## Layout

```text
fulbright-modules/
  pyproject.toml
  README.md
  fulbright_modules/
    hooks.py
    install.py           # after_install / after_migrate custom fields
    modules.txt          # Auth, Gmail, Fulbright HR, …
    auth/
      api.py             # sign-in links, password reset, welcome invite, set-password
      welcome.py         # suppress stock Frappe desk welcome mail
      async_email.py     # background mail + OTP patch
    gmail/
      api.py             # connect / list / disconnect Gmail
      setup.py           # Email Account team-sharing fields
    fulbright_hr/
      setup.py           # Employee Fulbright unit / compensation fields
```

Module names in `modules.txt` must be unique across every installed app. Do not use `HR` — that belongs to HRMS, and a collision makes Frappe load HRMS DocTypes (e.g. Interview) from `fulbright_modules/hr/doctype/...`.

## Add a module

1. Add a unique module name to `fulbright_modules/modules.txt`.
2. Create `fulbright_modules/<scrubbed_module>/` with `__init__.py` and your code (e.g. `Fulbright HR` → `fulbright_hr/`).
3. Whitelist methods with `@frappe.whitelist`. Paths become `fulbright_modules.<module>.api.<method>`.
4. Push to `main`, rebuild the custom image, then restart the backend so the site picks up the new code.

## Install

In `erp-next/frappe_docker/apps.json`, list this repository next to ERPNext and HRMS:

```json
{
  "url": "https://github.com/kiruba56/fulbright-modules",
  "branch": "main"
}
```

`apps.json` installs from GitHub. Push changes to `main` before rebuilding, or the image will miss methods the Next.js app already calls.

From `erp-next/frappe_docker`, rebuild the image that Compose tags `custom:16`:

```bash
docker build \
  --load \
  --no-cache \
  --build-arg=FRAPPE_PATH=https://github.com/frappe/frappe \
  --build-arg=FRAPPE_BRANCH=version-16 \
  --secret=id=apps_json,src=apps.json \
  --tag=custom:16 \
  --file=images/layered/Containerfile .
```

Recreate the stack with the same compose files you used to start it, then install the app on the existing site. This does not delete site data:

```bash
docker compose \
  -f compose.yaml \
  -f overrides/compose.mariadb.yaml \
  -f overrides/compose.redis.yaml \
  -f overrides/compose.noproxy.yaml \
  -p erpnext \
  up -d

docker compose -p erpnext exec backend bench --site fulbright install-app fulbright_modules
docker compose -p erpnext exec backend bench --site fulbright set-config fulbright_app_url http://localhost:3000
docker compose -p erpnext restart backend
```

`fulbright_app_url` is the public address of the Next.js app. Locally that is `http://localhost:3000`. In production, set it to the real app URL before anyone requests a link or connects Gmail.

Confirm the app is installed:

```bash
docker compose -p erpnext exec backend bench --site fulbright list-apps
```

### One-time migration from `fulbright_auth`

Sites that still have the old app must uninstall it on the **current** image before rebuilding. A site that lists an app missing from the image fails `migrate`.

```bash
docker compose -p erpnext exec backend bench --site fulbright uninstall-app fulbright_auth --yes --no-backup
# rebuild custom:16 with the new apps.json, then up -d
docker compose -p erpnext exec backend bench --site fulbright install-app fulbright_modules
docker compose -p erpnext restart backend
```

Sign-in links stop working between the uninstall and the install. Deploy the Next.js method-path change at the same time.

Also rename the GitHub repository from `fulbright-auth-app` to `fulbright-modules` so the `apps.json` URL resolves.

## Auth

Emails a one-time sign-in link. The link opens the Fulbright Next.js app at `/login/link`, not the ERPNext desk.

Outgoing mail must already work. This method does not use the **Login with email link** switch in System Settings.

### Call it from Fulbright

The Next.js server, not the browser, posts the user's email:

```http
POST /api/method/fulbright_modules.auth.api.send_login_link
Content-Type: application/json

{ "email": "person@company.com" }
```

A missing account still returns success, so the response does not reveal who has an account. More than ten requests an hour from the same IP return HTTP 429.

The message contains a link like:

```text
http://localhost:3000/login/link?key=...
```

That page must call this URL from the server:

```http
POST /api/method/fulbright_modules.auth.api.redeem_login_key
Content-Type: application/json

{ "key": "..." }
```

The response `message` contains `email`, `full_name`, and `sid`. Save that `sid` in the Fulbright session, then redirect the browser to `/`. The key expires in about 10 minutes, or whatever **Login with email link expiry** is set to in System Settings. Each key works once.

Password reset and new-user welcome both email a Fulbright `/set-password?key=...` link (never ERPNext `/update-password`). Stock Frappe User welcome mail is suppressed via `auth/welcome.py`.

```http
POST /api/method/fulbright_modules.auth.api.send_password_reset
Content-Type: application/json

{ "email": "person@company.com" }
```

After HR creates an Employee with Create User + Send Welcome Email:

```http
POST /api/method/fulbright_modules.auth.api.send_welcome_invite
Cookie: sid=...
Content-Type: application/json

{ "email": "person@company.com" }
```

The set-password page posts:

```http
POST /api/method/fulbright_modules.auth.api.set_password_with_key
Content-Type: application/json

{ "key": "...", "new_password": "..." }
```

Response `message` is `{ email, full_name, sid }` — same shape as redeem login key. Fulbright creates its session cookie and redirects to `/`.

## Gmail

Lets a signed-in Fulbright user connect, list, reconnect, and remove multiple Gmail accounts. No new DocTypes. Google credentials live in one site **Connected App**. Each mailbox is one **Email Account** whose encrypted `password` field holds the refresh token. Access tokens stay in the Frappe cache.

Connect stays exclusive: only one Fulbright user owns a given Gmail address. The owner can share that inbox with **every logged-in ERPNext user** using two independent Check custom fields on Email Account:

- `fulbright_team_view` — anyone can view
- `fulbright_team_reply` — anyone can reply

Reply implies view: turning reply on also turns view on, and turning view off also turns reply off. `bench migrate` (or installing the app) creates the fields via `after_migrate` / `after_install`.

### One-time setup

1. In Google Cloud, create an OAuth client of type **Web application**. Add one redirect URI per environment:
   `https://<fulbright-app>/settings/email/callback` (local: `http://localhost:3000/settings/email/callback`).
2. Put the consent screen **In production**. In Testing status, Google expires refresh tokens after 7 days.
3. In ERPNext, create a **Connected App** for Google (Provider Name `Google` is enough; the document name can be anything):
   - Provider Name: `Google`
   - Client ID / Client Secret: from Google Cloud
   - Authorization URI: `https://accounts.google.com/o/oauth2/v2/auth`
   - Token URI: `https://oauth2.googleapis.com/token`
   - Revocation URI: `https://oauth2.googleapis.com/revoke`
   - OpenID Configuration / Userinfo URI: `https://openidconnect.googleapis.com/v1/userinfo`
   - Scopes (one row each): `openid`, `email`, `https://www.googleapis.com/auth/gmail.modify`, `https://www.googleapis.com/auth/gmail.send`
4. The Gmail module finds that app automatically: site config override → name `Fulbright Gmail` → the only Google Connected App. Only set `fulbright_gmail_connected_app` if you have more than one Google Connected App.
5. Keep the site `encryption_key` stable. Losing it makes every stored refresh token unreadable.

`gmail.modify` is a restricted scope. An Internal app is enough for a Google Workspace org. Consumer `@gmail.com` accounts need Google's restricted-scope verification before the consent screen works for everyone. Adding or changing scopes later means every user must reconnect.

### Call it from Fulbright

All methods require a logged-in user. The Next.js server sends `Cookie: sid=...` from the Fulbright session.

```http
POST /api/method/fulbright_modules.gmail.api.start_gmail_connect
```

Returns `{ "auth_url": "https://accounts.google.com/..." }`. Redirect the browser there.

Google redirects to `/settings/email/callback?code=...&state=...`. The Next.js route then calls:

```http
POST /api/method/fulbright_modules.gmail.api.finish_gmail_connect
Content-Type: application/json

{ "code": "...", "state": "..." }
```

Returns `{ "email": "person@gmail.com" }`.

```http
POST /api/method/fulbright_modules.gmail.api.list_gmail_accounts
```

Returns owned accounts plus team-shared accounts the caller can see:

`[{ "email", "status", "connected_at", "is_owner", "owner", "team_view", "team_reply" }]`.

`status` is `connected` or `needs_reconnect`. Tokens are never returned.

```http
POST /api/method/fulbright_modules.gmail.api.set_gmail_sharing
Content-Type: application/json

{ "email": "person@gmail.com", "team_view": 1, "team_reply": 0 }
```

Owner only. Normalizes flags (reply ⇒ view) and returns `{ "ok", "email", "team_view", "team_reply" }`.

```http
POST /api/method/fulbright_modules.gmail.api.disconnect_gmail
Content-Type: application/json

{ "email": "person@gmail.com" }
```

Owner only. Revokes the grant at Google (best effort) and deletes that Email Account.

`get_access_token(account, user=None, need="view")` is an internal helper for later read/send work. When `user` is set, it checks owner or team view/reply access first (`need` is `"view"` or `"reply"`). It refreshes under a lock, stores the access token in cache, and on `invalid_grant` clears the refresh token so the account shows as needing reconnect.
