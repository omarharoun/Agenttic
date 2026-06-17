# Email for agenttic.io

Two independent pieces:

1. **App email verification** — built into the app (config-driven SMTP). Sends
   the verification link from `noreply@agenttic.io`. Ships *off* until an
   outbound relay is configured.
2. **Self-hosted mailbox** — a lightweight [Maddy](https://maddy.email) container
   that **receives** `support@agenttic.io` and serves it over IMAP. Opt-in
   compose profile `mail`.

## Hard constraint on this droplet (DigitalOcean)

Outbound SMTP is blocked: ports **25, 465 and 587 time out**; **2525 and 443
are open**. So:

- The server **cannot deliver mail directly** — verification email must go
  through a **relay/smarthost** reachable on an open port (2525) or an HTTP-API
  provider (443).
- **Inbound** 25 is *not* blocked, so the Maddy container can still receive
  `support@`.

## Outbound: configure a relay (required to actually send)

Set these in the host `.env` (`/opt/agenttic/.env`) — never commit them:

```
SMTP_HOST=smtp.sendgrid.net      # or smtp.mailgun.org, etc.
SMTP_PORT=2525                   # 587/465 are blocked on this box — use 2525
SMTP_USER=apikey                 # provider-specific (SendGrid uses "apikey")
SMTP_PASS=<relay-api-key>        # store as a secret; *_FILE supported
SMTP_FROM=noreply@agenttic.io
SMTP_STARTTLS=true
```

Then flip on verification in `config.prod.yaml` (or via env) and restart the
app:

```
email:
  enabled: true
  require_verification: true
```

Until `SMTP_HOST` is set the mailer runs in **console mode**: signup still
succeeds and the verification link is written to the app log (so nothing
breaks), but no email is delivered.

## DNS records (add at Cloudflare — all DNS-only / grey cloud)

Mail records must **not** be proxied by Cloudflare.

| Type  | Name                         | Value                                   | Proxy |
|-------|------------------------------|-----------------------------------------|-------|
| A     | `mail`                       | `64.23.179.172`                         | DNS-only |
| MX    | `@` (agenttic.io)            | `mail.agenttic.io` (priority `10`)      | DNS-only |
| TXT   | `@` (SPF)                    | `v=spf1 ip4:64.23.179.172 ~all`         | n/a |
| TXT   | `mail._domainkey`            | `v=DKIM1; k=rsa; p=<DKIM_PUBLIC_KEY>`   | n/a |
| TXT   | `_dmarc`                     | `v=DMARC1; p=none; rua=mailto:support@agenttic.io` | n/a |

- **DKIM public key** (selector `mail`, the matching private key lives only on
  the host at `/opt/agenttic/mail/dkim/mail.key`) — see the deploy report for
  the exact `p=` value.
- **SPF with a relay:** when you pick a relay, add its include, e.g.
  `v=spf1 ip4:64.23.179.172 include:sendgrid.net ~all`. The relay will also
  give you its own DKIM CNAMEs (domain authentication) — add those too; mail
  *sent through the relay* is signed by the relay, not by the key above.
- **PTR / reverse DNS:** set at the **VPS provider** (DigitalOcean → Droplet →
  rename the droplet to `mail.agenttic.io`; DO sets PTR from the droplet name).
  Not a Cloudflare record.

## Bring up the mailbox (after the A + MX records resolve)

ACME needs `mail.agenttic.io` to resolve first, so add DNS, then on the host:

```
cd /opt/agenttic
COMPOSE_PROFILES=postgres,redis,mail docker compose up -d mail
# create the support@ mailbox + its IMAP credentials:
docker compose exec mail maddyctl creds create support@agenttic.io
docker compose exec mail maddyctl imap-acct create support@agenttic.io
```

Store the password you set as a host secret (e.g. in a `pass`/vault entry or
`/opt/agenttic/mail/support.pass`, `chmod 600`) — it is the IMAP/SMTP login.

## Mailbox client settings (support@agenttic.io)

| Setting        | Value                         |
|----------------|-------------------------------|
| IMAP server    | `mail.agenttic.io`            |
| IMAP port      | `993`, SSL/TLS                |
| SMTP server    | `mail.agenttic.io`            |
| SMTP port      | `587`, STARTTLS               |
| Username       | `support@agenttic.io`         |
| Password       | the one set via `maddyctl creds create` |

- **Thunderbird / Apple Mail (macOS):** add account → manual setup → IMAP
  993 SSL, SMTP 587 STARTTLS, full-email username.
- **iOS:** Settings → Mail → Accounts → Add → Other → IMAP, same host/ports.
- **Android (Gmail/FairEmail):** Add account → Personal (IMAP), same settings.

> Sending *from* `support@` via the mailbox's submission (587) still can't reach
> the public internet directly (port-25 block); for replies that leave the
> network, point the client's SMTP at the same relay as the app, or reply from a
> relay-backed address.

## Roundcube webmail?

Skipped — it needs PHP + a web server and doesn't fit the ~458 MB box. Use a
desktop/mobile IMAP client (above) instead.
