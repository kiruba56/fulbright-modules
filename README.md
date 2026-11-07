# Fulbright Auth

Frappe app that emails a one-time sign-in link. The link opens the Fulbright Next.js app at `/login/link`, not the ERPNext desk.

The method is `fulbright_auth.api.send_login_link`. It stores the same cache key ERPNext already accepts (`one_time_login_key:{key}`), so the Next.js app can redeem it with `frappe.www.login.login_via_key`.

Outgoing mail must already work. This method does not use the **Login with email link** switch in System Settings.

## Add it to the image

In `erp-next/frappe_docker/apps.json`, add this repository next to ERPNext and HRMS:

```json
{
  "url": "https://github.com/kiruba56/fulbright-auth-app",
  "branch": "main"
}
```

From `erp-next/frappe_docker`, rebuild the image that Compose already tags `custom:16`:

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

docker compose -p erpnext exec backend bench --site fulbright install-app fulbright_auth
docker compose -p erpnext exec backend bench --site fulbright set-config fulbright_app_url http://localhost:3000
docker compose -p erpnext restart backend
```

`fulbright_app_url` is the public address of the Next.js app. Locally that is `http://localhost:3000`. In production, set it to the real app URL before anyone requests a link.

Confirm the app is installed:

```bash
docker compose -p erpnext exec backend bench --site fulbright list-apps
```

## Call it from Fulbright

The Next.js server, not the browser, posts the user's email:

```http
POST /api/method/fulbright_auth.api.send_login_link
Content-Type: application/json

{ "email": "person@company.com" }
```

A missing account still returns success, so the response does not reveal who has an account. More than ten requests an hour from the same IP return HTTP 429. The Fulbright server sends these requests, so that limit is shared for the server.

The message contains a link like:

```text
http://localhost:3000/login/link?key=...
```

That page must call this URL from the server:

```http
POST /api/method/fulbright_auth.api.redeem_login_key
Content-Type: application/json

{ "key": "..." }
```

The response `message` contains `email`, `full_name`, and `sid`. Save that `sid` in the Fulbright session, then redirect the browser to `/`. The key expires in about 10 minutes, or whatever **Login with email link expiry** is set to in System Settings. Each key works once.
