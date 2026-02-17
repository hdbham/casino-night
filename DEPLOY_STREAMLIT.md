# Publish to Streamlit Community Cloud

## 1. Put your app on GitHub

Your app must be in a **GitHub** (or GitLab/Bitbucket) repository.

**If you don’t have a repo yet:**

```bash
cd "/Users/admin/Documents/Casino Night"
git init
git add app.py requirements.txt SETUP_GOOGLE.md sheet_template.csv draw_winners.py AppsScript_PushOnEdit.js .gitignore
git add assets/ 2>/dev/null || true
# Do NOT add .streamlit/secrets.toml (it's in .gitignore)
git status   # confirm no secrets.toml is staged
git commit -m "Casino Night Streamlit app"
```

Create a **new empty repository** on [GitHub](https://github.com/new) (e.g. `casino-night`). Then:

```bash
git remote add origin https://github.com/YOUR_USERNAME/casino-night.git
git branch -M main
git push -u origin main
```

(Replace `YOUR_USERNAME` and `casino-night` with your repo.)

---

## 2. Deploy on Streamlit Cloud

1. Go to **[share.streamlit.io](https://share.streamlit.io)** and sign in with GitHub.
2. Click **“New app”**.
3. Choose:
   - **Repository:** `YOUR_USERNAME/casino-night`
   - **Branch:** `main`
   - **Main file path:** `app.py`
4. Click **“Advanced settings”** and add your **Secrets** (same structure as `.streamlit/secrets.toml`).

---

## 3. Set secrets in the Cloud dashboard

In the app’s **“Secrets”** (or “Settings” → “Secrets”) paste the **contents** of your local `.streamlit/secrets.toml` — the same `[gcp_service_account]`, `[sheets]`, `operator_pin`, etc. Do **not** commit `secrets.toml` to Git; it’s in `.gitignore`.

Example shape (values are yours):

```toml
[gcp_service_account]
type = "service_account"
project_id = "your-project-id"
private_key = """
-----BEGIN PRIVATE KEY-----
... your key lines ...
-----END PRIVATE KEY-----
"""
client_email = "your-sa@project.iam.gserviceaccount.com"
token_uri = "https://oauth2.googleapis.com/token"

[sheets]
sheet_id = "your-google-sheet-id"
worksheet_name = "Casino Night"

operator_pin = "1234"
```

Save and redeploy if needed.

---

## 4. Deploy

Click **“Deploy!”**. Streamlit will install from `requirements.txt` and run `streamlit run app.py`. Your app URL will be like:

`https://YOUR_APP_NAME.streamlit.app`

---

## Notes

- **Google Sheet:** Ensure the Google Sheet is shared with the **service account email** (`client_email`) as Editor (or at least give it access to the sheet).
- **Operator PIN:** Can also be set in the Config sheet (see SETUP_GOOGLE.md) or in secrets as above.
- If the app fails, check the Cloud logs on share.streamlit.io for errors (e.g. missing secrets or Sheet permissions).
