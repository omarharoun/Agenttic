# Email for agenttic.io

No mail server is self-hosted. Two separate concerns:

1. **Receiving** `support@agenttic.io` → **Cloudflare Email Routing** (managed in
   the Cloudflare dashboard; nothing on the box).
2. **Sending** signup verification from `noreply@agenttic.io` → **Resend** over
   its HTTPS API (port 443 is open here; SMTP ports 25/465/587 are blocked).

## Sending — Resend (HTTPS API)

The app posts to `https://api.resend.com/emails` with `RESEND_API_KEY`. No SMTP
ports needed. `email.provider: resend` is set in `config.prod.yaml`.

**Setup (you do this once):**

1. Create a Resend account → **API Keys** → create a key.
2. **Domains → Add Domain → `agenttic.io`.** Resend shows a set of DNS records
   to add (typically):
   - a **DKIM** `TXT` record at `resend._domainkey` (selector `resend`),
   - an **SPF** `TXT` (often on a `send` subdomain, e.g. `send.agenttic.io` →
     `v=spf1 include:amazonses.com ~all`), and
   - a **return-path / MX** record on that **`send` subdomain** (e.g.
     `send.agenttic.io  MX  feedback-smtp.us-east-1.amazonses.com`).
   Add them in **Cloudflare DNS as DNS-only (grey cloud)**. Wait for Resend to
   show the domain **Verified**.
3. Put the key in the host `.env` (`/opt/agenttic/.env`) — never commit:
   ```
   RESEND_API_KEY=re_xxxxxxxx
   ```
4. Flip verification on in `config.prod.yaml` (or env) and restart the app:
   ```
   email:
     enabled: true
     require_verification: true
   ```

Until `RESEND_API_KEY` is set the mailer runs in **console mode** (logs the
verification link, sends nothing) and verification stays disabled, so signup is
unaffected.

### Coexistence with Cloudflare Email Routing (receiving)

These do **not** conflict:

- Resend's records sit on the **`send` subdomain** (`send.agenttic.io` MX/SPF)
  and the `resend._domainkey` TXT — Resend does **not** claim the root `@` MX.
- Cloudflare Email Routing owns the **root `@` MX** (and its own SPF
  `include:_spf.mx.cloudflare.net`) for *receiving* `support@`.

So the root domain receives via Cloudflare while `send.agenttic.io` is used by
Resend for sending. If both want an SPF TXT on the same name, merge the
`include:`s into one record (only one SPF TXT per name is allowed).

## Receiving — Cloudflare Email Routing

Cloudflare dashboard → **Email → Email Routing → enable**, add
`support@agenttic.io` forwarding to your real inbox. Cloudflare creates the root
MX + SPF automatically and verifies the destination. Nothing to deploy on node1.

## Alternatives

`email.provider` also supports `smtp` (a relay on an open port — 2525 works
here, 587/465 are blocked) and `console`. Resend over HTTPS is the default and
the recommended path on this droplet.
