# Self-host Avisa on TrueNAS SCALE (Cloudflare Tunnel)

Run the paper on your own TrueNAS SCALE box, reachable at
`https://avis.dittdomene.no` — no VPS rent, no open ports, no port-forwarding,
no Let's Encrypt. A **Cloudflare Tunnel** terminates HTTPS at Cloudflare's edge
and forwards traffic into the app over Docker's internal network; your home IP
is never exposed.

The stack is two containers — `avisa` (the app, pulled from GHCR) and
`cloudflared` (the tunnel). No Caddy, no reverse proxy. Defined in
`docker-compose.truenas.yml`.

> The database is just cached, reproducible news. A plain Docker volume is fine
> — no ZFS dataset or snapshots needed. If you lose it, the paper rebuilds itself
> over the next poll cycles.

Replace everywhere:
- `avis.dittdomene.no` → your own (sub)domain (must be a zone in Cloudflare)

---

## 0. What you need first
1. **OpenRouter key** — create at https://openrouter.ai/keys, add a little credit.
   (The local `claude` CLI trick from the README does not work on the NAS — use
   OpenRouter here.)
2. **A domain on Cloudflare** — the domain's nameservers must point to Cloudflare
   (free plan is enough).
3. **TrueNAS SCALE** with the Docker app layer (Electric Eel 24.10+ is native
   Docker; older releases work too).

---

## 1. Create the Cloudflare Tunnel
In the **Cloudflare Zero Trust** dashboard (one-time, free):

1. **Networks → Tunnels → Create a tunnel** → connector **Cloudflared** → name it
   (e.g. `avisa`) → **Save**.
2. On the next screen, **copy the tunnel token** (the long string after
   `--token` in the shown install command). This goes into `.env` as
   `CF_TUNNEL_TOKEN` — you do *not* run the shown command yourself; the
   `cloudflared` container uses the token.
3. **Public Hostnames → Add a public hostname:**
   - Subdomain/domain: `avis` . `dittdomene.no`
   - Service: **HTTP** → `avisa:8000`
   - Save. The DNS record is created automatically — nothing to set by hand.

---

## 2. Get the stack onto TrueNAS
Easiest path: install **Dockge** (or Portainer) from the TrueNAS **Apps**
catalog to manage compose stacks from a UI. Then create a new stack named
`avisa` and paste the contents of `docker-compose.truenas.yml`.

Alongside the compose file, create the `.env` in the same stack directory:

```env
# --- Cloudflare Tunnel ---
CF_TUNNEL_TOKEN=eyJ...        # the token copied in step 1

# --- LLM (required on the NAS) ---
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-...
CURATE_MODEL=anthropic/claude-haiku-4.5
TRANSLATE_MODEL=anthropic/claude-haiku-4.5

# --- Login (set these — the paper is public) ---
ADMIN_PASSWORD=<long random string>
SESSION_SECRET=<another long random string>

# --- Paper / pipeline (taste) ---
PAPER_TITLE=Kveldsposten
PAPER_LANG=no
POLL_MINUTES=60               # higher = fewer LLM calls = lower cost
FRONT_PAGE_SIZE=12
PREFERENCES=General news, technology, climate and science. Weight on analysis over opinion/sport.
```

See `.env.example` for every available variable.

> **Set `OPENROUTER_API_KEY` before the first deploy.** If the first edition builds
> without it, the pipeline runs in demo mode and stamps the curated articles as
> "translated" with their original (English) text — and never retries them, so the
> front page stays English even after you add the key. Recovering means wiping the DB
> volume and rebuilding. Set the key up front and the first edition is curated +
> translated correctly.

---

## 3. Deploy
In Dockge/Portainer: **Deploy / Up**. (CLI equivalent on the box:
`docker compose -f docker-compose.truenas.yml up -d`.)

The `avisa` image is pulled from GHCR — no building on the NAS.

---

## 4. Verify
- `https://avis.dittdomene.no` → the front page builds (the first edition can
  take a minute — reload).
- `https://avis.dittdomene.no/status` → JSON, no errors.
- `/settings` → prompts for the admin password.

If the page doesn't resolve, check the tunnel shows **Healthy** in the Cloudflare
dashboard and that the public hostname points at `http://avisa:8000`.

---

## Want it fully private?
Reading is open to anyone with the URL (the front page is just news). To lock the
*whole* site to yourself/family, add **Cloudflare Access** (Zero Trust → Access →
Applications) in front of the hostname and gate it by email — free for small
teams. Then even reading requires a Cloudflare login; the app's own
`ADMIN_PASSWORD` still guards the config surfaces underneath.

## Updating to a new version
`git push origin main` builds and pushes a fresh `ghcr.io/mhellevang/avisa:latest`
via GitHub Actions — but the box sits behind NAT, so **CI can't push the update to
it**. The stack handles this itself:

- **Automatic (default):** the bundled `watchtower` service polls GHCR hourly and
  recreates `avisa` whenever `:latest` changes, so a `git push` reaches the box on
  its own. It's scoped by the `com.centurylinklabs.watchtower.enable=true` label on
  `avisa` (plus `WATCHTOWER_LABEL_ENABLE=true`), so it only touches this stack — not
  every other container on the NAS.

  > **First deploy only:** Watchtower starts updating *after* the stack is up, so the
  > very first time you change the compose you still need a manual **Pull + Up** to
  > pick up the new `watchtower` service. After that it's hands-off.

  Trade-off: a bad push auto-deploys. Low stakes for a personal paper — but if you'd
  rather approve each update, delete the `watchtower` service and use the manual
  route below.
- **Manual:** in Dockge/Portainer hit **Pull + Up** (or
  `docker compose -f docker-compose.truenas.yml pull && ... up -d`).
