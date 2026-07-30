# Authentication and Access Recommendations

## Current State

The application has no user authentication for the main UI (upload, dashboard, PO detail). Anyone who can reach the web server can:

- Upload files and trigger processing runs
- View all dashboard data, including row-level purchase-order details
- Download archived source files and generated workbooks

**Admin screens** (threshold profile management, activation/deactivation) are protected by a simple token-based login (`/admin/login`) that stores a `session["admin_logged_in"]` boolean in a signed cookie. This is not a real authentication system.

## Minimum Controls Required Before Country-Manager Access

Before any country manager uses the application (even in MVP), these minimum controls must be in place:

1. **Network-level access control**: Deploy behind a VPN or corporate firewall. Do not expose the application to the public internet without authentication.
2. **HTTPS/TLS**: All traffic must be encrypted. Use a reverse-proxy (Nginx, Apache, or a cloud load balancer) with a valid TLS certificate.
3. **Session security**: Set `SESSION_COOKIE_SECURE=True`, `SESSION_COOKIE_HTTPONLY=True`, and `SESSION_COOKIE_SAMESITE='Lax'` in production.

## MVP Authentication Approach (Temporary)

For a controlled MVP with known users, use HTTP Basic Auth or a shared access token at the reverse-proxy level:

### Option A: Nginx Basic Auth

```nginx
location / {
    auth_basic "Import Tracker";
    auth_basic_user_file /etc/nginx/.htpasswd;
    proxy_pass http://127.0.0.1:8000;
}
```

### Option B: Shared Access Token (via reverse-proxy)

The admin token mechanism can be repurposed: require a query-parameter token or `X-Access-Token` header checked at the proxy before forwarding to the Flask app.

### Option C: Flask middleware wrapper

Add a simple decorator to the Flask routes that checks for a pre-shared `APP_ACCESS_TOKEN` environment variable:

```python
@app.before_request
def require_access():
    if request.path.startswith(("/health", "/static")):
        return
    token = request.headers.get("X-Access-Token") or request.args.get("token")
    if token != os.environ.get("APP_ACCESS_TOKEN"):
        return jsonify({"error": "Unauthorized"}), 401
```

## Production Authentication (Recommended)

### If Syngenta has SSO (Okta, Azure AD, OneLogin)

Integrate with Flask via:

- **flask-oidc** or **flask-saml** for SAML/OIDC-compliant SSO
- **Authlib** for OAuth 2.0 / OIDC flows (supports Azure AD, Google Workspace, Okta)

The SSO IdP handles user identity, password policies, MFA, and session management. The Flask app only needs to verify the JWT/token and extract the user's role (country manager vs admin).

### If SSO is not available

Use Flask-Login with a local user store:

1. Create a `users` table in the database with hashed passwords (bcrypt)
2. Assign roles: `country_manager` (dashboard + upload), `admin` (threshold management)
3. Protect routes with `@login_required` and `@role_required("admin")` decorators
4. Provide a separate `/login` page for user authentication

### Minimum Role Model

| Role | Access |
|---|---|
| `country_manager` | Upload, Dashboard, PO Detail, Download |
| `admin` | All country_manager + Threshold profile management, activation |

## Recommendations Summary

| Stage | Auth mechanism | Effort | Security |
|---|---|---|---|
| Local dev only | None | 0 | None (localhost-only) |
| Controlled MVP | Network restriction + Basic Auth | Low | Low-Medium |
| Production | SSO integration | Medium-High | High |
| Production (no SSO) | Flask-Login + local users | Medium | Medium |

## What Must Be Done Before Any Public or Multi-User Deployment

1. [ ] Decide on auth approach (SSO vs local users vs proxy-level)
2. [ ] Implement selected auth mechanism
3. [ ] Protect ALL routes (upload, dashboard, data, download, admin)
4. [ ] Set `SESSION_COOKIE_SECURE=True` in production config
5. [ ] Configure HTTPS/TLS termination
6. [ ] Create at minimum one admin user account
7. [ ] Remove default `dev-key` secret from configuration
8. [ ] Test that unauthenticated requests are rejected
