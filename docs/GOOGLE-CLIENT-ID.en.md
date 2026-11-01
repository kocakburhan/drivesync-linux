[Türkçe](GOOGLE-CLIENT-ID.md) | [English](GOOGLE-CLIENT-ID.en.md)

# Create your own Google Drive client ID

DriveSync connects through rclone using an **OAuth Desktop app** client you create.
This is free: **no billing account or credit card is needed** for this Drive API usage.
Ignore Cloud Console's “Start free / Free trial” banner. Labels may vary with language/account;
this guide uses the Google Auth Platform UI. Workspace administrator policies can block this flow.

## 1. Create a project

1. Open [Google Cloud Console](https://console.cloud.google.com/) and sign into the account you intend to use.
2. Open the project selector in the top bar → **New project**.
3. Use a generic name such as `DriveSync Personal`; personal accounts can choose **No organization** if asked.
4. Click **Create**, wait, then select the new project in the top selector. Check the selected project throughout.

## 2. Enable Google Drive API

1. Open **APIs & Services → Library** from the navigation menu.
2. Search for `Google Drive API` and open Google's **Google Drive API** result.
3. Click **Enable**. If you see **Manage**, it is already enabled.

## 3. Start Google Auth Platform

1. Open **Google Auth Platform** from the navigation menu, or find it using the Console search bar.
   The older **APIs & Services → OAuth consent screen** entry may redirect here.
2. Click **Get started**.
3. **App Information**: enter a name such as `DriveSync Personal`, choose your own **User support email**, then **Next**.
4. **Audience**: choose **External**, then **Next**. Personal Gmail accounts cannot use Internal.
5. **Contact Information**: enter your own notification email address, then **Next**.
6. Read/accept the Google API Services User Data Policy checkbox, then **Continue → Create**.

Your addresses belong in your private Google project, never in this repository or bug reports.

## 4. Branding and publication URLs

Open **Google Auth Platform → Branding**. Check the name/support email. Complete the **App domain**
fields for publication: **Application home page**, **Application privacy policy link** and **Authorized domains**.
Use valid public URLs. Choose one of these options:

- **(a) The project's public pages**:
  - Home page: `https://kocakburhan.github.io/drivesync/`
  - Privacy policy: `https://kocakburhan.github.io/drivesync/gizlilik.html`
  - **Authorized domains → Add domain**: `kocakburhan.github.io` (no https or path).
- **(b) Host your own copy**: adapt the [privacy template](privacy-template.md), publish a home page
  and privacy policy on a site you control, then enter those HTTPS URLs and your authorized domain.
  Replace bracketed template fields with your own details; a local Markdown path is not a public URL.

Open the URLs in a browser first and confirm they are accessible and appropriate for your app.
If project pages are not published/accessible, use option (b). If Google requests domain ownership verification,
you cannot verify a domain you do not control; use your own site.
These fields do not complete Google's verification process. A logo is optional; unnecessary branding/logo
changes can trigger verification requirements. Click **Save**.

## 5. Set scopes and publish the app

1. Open **Data Access → Add or remove scopes**.
2. Select Google Drive API scope `https://www.googleapis.com/auth/drive`; if absent, add that URI manually.
   Click **Update → Save**.
3. Open **Audience**. **Publishing status** may initially say **Testing**.
4. Click **Publish app**, then **Confirm**. Check that the status becomes **In production**.

**Why publish?** For External apps in Testing, refresh tokens with full Drive scope expire after 7 days.
In production removes that weekly-expiry condition; revocation/security policies can still invalidate tokens.
Publishing is separate from Google verification. Your own unverified app may still show a warning and user limits;
broad distribution may require verification.

If you must temporarily remain in Testing, use **Audience → Test users → Add users** to add your own account.
This does not remove the 7-day limit. Each DriveSync user should create their own client.

**Why full Drive scope?** Two-way sync must read, change and delete files in existing chosen Drive folders.
`drive.file` only covers app-created files/files shared through a picker, so rclone uses `scope=drive` here.
Grant this permission only to your own trusted app. The permission technically extends beyond the chosen folders;
DriveSync restricts operations to configured folders at the application level.

## 6. Create a Desktop app client and download JSON

1. Open **Google Auth Platform → Clients → Create client**.
2. Set **Application type → Desktop app**. Do not choose Web application or Service account.
3. Enter a generic name such as `DriveSync Desktop`, then **Create**.
4. Click **Download JSON** in the creation dialog. If unavailable, open the new client from **Clients**
   and use its JSON download button/icon.
5. Keep the downloaded file private. It contains `installed`, `client_id` and `client_secret` fields.
   Never put it on GitHub/in a public folder or print its contents in Terminal.

Desktop clients need no web redirect URI entry; rclone handles the local browser authorization flow.

## 7. Authorize through DriveSync

Replace the filename with the actual downloaded file:

```bash
drivesync-cli setup-remote --client-json "$HOME/Downloads/client_secret_….json"
```

1. Select the correct Google account in the browser.
2. If **Google hasn't verified this app** appears, check that the app name is the one you created.
3. Only for your own app, select **Advanced → Go to [app name] (unsafe)**.
4. Review permissions and select **Continue / Allow**. If required, select the Drive permission checkbox/“Select all”.
5. When rclone's success page opens, return to Terminal. DriveSync verifies access using `rclone about gdrive:`;
   success says “Google Drive bağlantısı kuruldu ve erişim doğrulandı”.

If no browser opens, fix the default browser in your graphical session and retry; this helper uses local browser auth.
`--remote NAME` creates another rclone remote; set DriveSync config's `remote` field to `NAME:` as well.

Existing remotes are refused by default. For an expired token, fix the Audience publishing status first, then run:

```bash
drivesync-cli setup-remote --client-json "$HOME/Downloads/client_secret_….json" --replace
```

`--replace` first writes a private rclone.conf backup under `~/.config/drivesync/`.
The client file is stored there as `google-client.json` with mode 0600. Backups can include tokens
for other rclone remotes too: never share them. You can revoke access later from your Google Account's third-party connections.

Continue with [README — adding a folder and initial sync](../README.en.md#6-add-a-folder-and-start-initial-sync).
