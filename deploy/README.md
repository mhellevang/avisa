# Deploy Avisa to a VPS

> **Setting it up for the first time?** Follow the step-by-step walkthrough in
> [`SETUP.md`](SETUP.md). This file is the reference (host choice, cost, Cloudflare
> options, CI internals).

Recommended host: **Hetzner Cloud `CX22`** (2 vCPU, 4 GB RAM, EU). The app needs
an always-on process (in-process scheduler) and a persistent disk (SQLite), so a
small always-on VM — not serverless — is the right fit.

## Steps

1. **Create the VM.** Hetzner `CX22`, Ubuntu 24.04, add your SSH key. In the
   Hetzner *Cloud Firewall*, allow inbound `22`, `80`, `443` only.
2. **Bootstrap.** From your laptop, in this repo:
   ```sh
   ssh root@SERVER_IP 'bash -s' < deploy/bootstrap.sh
   ```
   (Set `REPO_URL` first, or edit the default in `deploy/bootstrap.sh`.) Installs
   Docker, a host firewall, and clones the repo to `/opt/avisa`.
3. **Configure.** On the server, in `/opt/avisa`:
   ```sh
   cp .env.example .env && nano .env
   ```
   Required when public: `OPENROUTER_API_KEY`, `ADMIN_PASSWORD`, `SESSION_SECRET`.
   Tune `PAPER_TITLE`, `PAPER_LANG`, `PREFERENCES`, and raise `POLL_MINUTES` to cut
   LLM cost. Leave `DATABASE_URL` — compose overrides it to the volume.
4. **Set your domain** in `Caddyfile` (replace `din-avis.eksempel.no`). See the
   **Cloudflare** section below for the DNS record.
5. **First deploy.** Don't start it by hand — the CI workflow does the first start
   too (it builds the image, pushes it to GHCR, then pulls + starts on the server).
   Finish **Continuous deploy** below, then push to `main`. Caddy fetches a Let's
   Encrypt cert automatically on first start.
6. **Backup.** `crontab -e` as root:
   ```
   15 3 * * *  /opt/avisa/deploy/backup.sh >> /var/log/avisa-backup.log 2>&1
   ```

## Continuous deploy (GitHub Actions → GHCR → VPS pull)

`.github/workflows/deploy.yml` runs on every push to `main`:
1. Builds the image **in CI** and pushes it to GHCR (`ghcr.io/mhellevang/avisa`,
   tagged `latest` + the commit SHA).
2. SSHes into the server as `deploy`, runs `git pull` (compose/Caddyfile only),
   then `docker compose pull` + `up -d --no-build`.

The **VPS never builds — it only pulls.** App secrets stay on the server in `.env`
and never enter CI.

One-time setup:

1. **Make a dedicated deploy key** on your laptop (no passphrase):
   ```sh
   ssh-keygen -t ed25519 -f ~/.ssh/avisa_deploy -N "" -C "github-actions-avisa"
   ```
2. **Authorize it on the server** — append the *public* key to the deploy user:
   ```sh
   ssh-copy-id -i ~/.ssh/avisa_deploy.pub deploy@SERVER_IP
   # or paste ~/.ssh/avisa_deploy.pub into /home/deploy/.ssh/authorized_keys
   ```
3. **Add GitHub repo secrets** (Settings → Secrets and variables → Actions):
   | Secret | Value |
   |--------|-------|
   | `DEPLOY_HOST` | server IP or hostname |
   | `DEPLOY_USER` | `deploy` |
   | `DEPLOY_SSH_KEY` | contents of `~/.ssh/avisa_deploy` (the **private** key) |
   | `DEPLOY_PORT` | `22` (optional — omit to use 22) |
4. Push to `main` (or *Actions → Deploy → Run workflow*) and watch it deploy.

**GHCR auth:** the workflow pushes with the built-in `GITHUB_TOKEN` and forwards it
to the server only for the duration of the `docker login ghcr.io` → `pull` step, so
the image package can stay **private** — no manual "make package public" step, no
long-lived token on the box. (You *can* make the GHCR package public if you prefer
unauthenticated pulls; then the `docker login` line is harmless but unnecessary.)

**Rollback:** every build is also tagged with its commit SHA. To roll back, on the
server run `docker compose -f docker-compose.yml -f docker-compose.prod.yml pull`
after temporarily pointing the image tag at a known-good SHA, or just revert the
commit on `main` and let CI redeploy.

## Cloudflare DNS

Your domain is on Cloudflare. The only real decision is **proxy on or off** — it
changes how Caddy gets its TLS certificate.

### Option A — DNS-only (grey cloud) · recommended, simplest

In Cloudflare DNS, add an `A` record for your subdomain → server IP, and click the
cloud icon so it is **grey (DNS only)**, not orange. Then everything in this repo
works **as-is** — Caddy gets the cert over HTTP-01. Nothing else to change.

Trade-off: you lose Cloudflare's CDN/DDoS proxy and your origin IP is public. For
a personal app that's fine.

### Option B — Proxied (orange cloud) · hides origin IP, adds CDN/DDoS

If you want the orange cloud, Caddy can't use HTTP-01 (Cloudflare proxies 80/443),
so use the **DNS-01 challenge** with Cloudflare's API instead:

1. Create a scoped Cloudflare API token: *Zone → DNS → Edit* for your zone. Put it
   in `.env` as `CF_API_TOKEN=...`.
2. Build a Caddy image with the Cloudflare DNS plugin — add `deploy/Dockerfile.caddy`:
   ```dockerfile
   FROM caddy:2-builder AS build
   RUN xcaddy build --with github.com/caddy-dns/cloudflare
   FROM caddy:2
   COPY --from=build /usr/bin/caddy /usr/bin/caddy
   ```
   and point the `caddy` service in `docker-compose.prod.yml` at it (`build:
   { context: ., dockerfile: deploy/Dockerfile.caddy }` instead of `image: caddy:2`,
   and pass `CF_API_TOKEN` via `environment`).
3. In `Caddyfile`, tell Caddy to use DNS-01:
   ```
   din-avis.eksempel.no {
       reverse_proxy avisa:8000
       tls { dns cloudflare {env.CF_API_TOKEN} }
   }
   ```
4. Set Cloudflare SSL/TLS mode to **Full (strict)**.

Tell me if you want Option B and I'll wire up the files.
