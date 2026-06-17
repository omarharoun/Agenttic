# Email for agenttic.io

No mail server is self-hosted. Two separate concerns:

1. **Receiving** `support@agenttic.io` → **Cloudflare Email Routing** (managed in
   the Cloudflare dashboard; nothing to deploy on node1).
2. **Sending** signup verification from `noreply@agenttic.io` → the app's
   **config-driven SMTP** sender pointed at an external relay.

## Receiving — Cloudflare Email Routing

In the Cloudflare dashboard: **Email → Email Routing → enable**, then add a
custom address `support@agenttic.io` that forwards to your real inbox.
Cloudflare **automatically creates the required MX records and an SPF record**
for receiving, and verifies the destination address. No MX/IMAP/server on the
box. Nothing to do on node1.

## Sending — still needs an external relay

Important reality:

- **Cloudflare Email Routing only receives/forwards — it cannot send** the
  transactional verification emails to arbitrary new signups.
- Cloudflare **Email Workers** have a `send_email` binding, but it can **only
  deliver to pre-verified destination addresses** — it is not a general
  transactional sender, so it can't email a brand-new signup whose address
  isn't pre-verified.
- This droplet has outbound **25 / 465 / 587 blocked** (2525 and 443 are open).

So sending verification email requires an **external SMTP relay / transactional
provider** (SendGrid, Mailgun, Postmark, Resend, SES, …) — or DigitalOcean
unblocking port 25. The app is config-driven so it plugs into one the moment we
have creds.

Set in the host `.env` (`/opt/agenttic/.env`) — never commit:

```
SMTP_HOST=smtp.sendgrid.net      # or smtp.mailgun.org, etc.
SMTP_PORT=2525                   # 587/465 are blocked on this box — use 2525
SMTP_USER=apikey                 # provider-specific (SendGrid uses "apikey")
SMTP_PASS=<relay-api-key>        # secret; *_FILE supported
SMTP_FROM=noreply@agenttic.io
SMTP_STARTTLS=true
```

Then enable verification in `config.prod.yaml` (or env) and restart the app:

```
email:
  enabled: true
  require_verification: true
```

Until `SMTP_HOST` is set the mailer runs in **console mode**: signup still
succeeds and the verification link is written to the app log, but no email is
delivered. Verification stays *disabled* in prod until then, so signup is
unaffected.

For SPF/DKIM/DMARC alignment of *sent* mail, follow the relay's domain-
authentication steps (they provide the exact CNAME/TXT records to add at
Cloudflare). Cloudflare's auto-created SPF covers *receiving*; extend it with
the relay's `include:` when you wire sending.
