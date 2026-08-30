# Yours Momos Tracker — Updated Streamlit Cloud Edition

This version keeps the existing Neon PostgreSQL database and adds a redesigned daily dashboard, editing/deletion, multi-expense entry, categories, combined Daily/Weekly/Monthly reports, charts, CSV/Excel exports, business-day back-entry, Holiday/Missed status, responsive styling, and unrestricted Google sign-in.

## Google sign-in

Google sign-in uses Streamlit's native OIDC authentication. Any Google account that Google allows to authenticate can sign in; the app does not whitelist a specific email. New Google accounts are created as STAFF by default. The OWNER can link an existing user's Google email or change roles in Settings.

In Streamlit Cloud → App → Settings → Secrets, add:

```toml
[database]
url = "YOUR_NEON_CONNECTION_STRING"

[auth]
redirect_uri = "https://YOUR-APP-NAME.streamlit.app/oauth2callback"
cookie_secret = "A_LONG_RANDOM_SECRET"
client_id = "YOUR_GOOGLE_CLIENT_ID"
client_secret = "YOUR_GOOGLE_CLIENT_SECRET"
server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"
```

In Google Cloud, add the exact Streamlit URL ending in `/oauth2callback` to Authorized redirect URIs. To allow any Google account, the Google OAuth app must be published rather than limited to a test-user list.

## Deploy

Repository root should contain `app.py`, `requirements.txt`, and `schema.sql`. Main file path in Streamlit Cloud is `app.py`.

The app stores money in integer cents/paise in PostgreSQL and displays PKR (`Rs.`). Business dates use Asia/Karachi time and roll over at 4:00 AM.


## Login

Username/password login only. Google/OAuth is not required. Passwords remain bcrypt-hashed.
