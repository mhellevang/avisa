# Sette opp Avisa i skyen — steg for steg

Følg denne ovenfra og ned. Den tar deg fra ingenting til en kjørende avis på
`https://avis.dittdomene.no` med auto-deploy: hver push til `main` ⇒ GitHub
Actions bygger nytt image ⇒ serveren puller og restarter.

Host: **Hetzner Cloud CX22** (2 vCPU, 4 GB RAM, EU). Se `deploy/README.md` for
hvorfor, kostnad og referanse.

Erstatt overalt:
- `SERVER_IP` → serverens offentlige IP
- `avis.dittdomene.no` → ditt eget (sub)domene

---

## 0. Det du trenger først
1. **OpenRouter-nøkkel** — lag på https://openrouter.ai/keys, legg på litt kreditt.
2. **Hetzner Cloud-konto** — https://console.hetzner.cloud
3. **SSH-nøkkel på laptopen** for root-innlogging. Sjekk: `ls ~/.ssh/*.pub`.
   Har du ingen: `ssh-keygen -t ed25519`.

---

## 1. Sett domenet og push koden til GitHub
Workflowen og bootstrap henter koden fra GitHub, så den må på `main` først.

a) Åpne `Caddyfile` og bytt domenet:
```
din-avis.eksempel.no {     →     avis.dittdomene.no {
```
b) Commit og push:
```sh
git add Caddyfile && git commit -m "Set production domain"
git push origin main
```
> Forventet: workflowen kjører nå. **Bygg-steget blir grønt** (image pushes til
> GHCR), men **deploy-steget blir rødt** — serveren finnes ikke ennå. Helt normalt;
> vi kjører den på nytt i steg 8.

---

## 2. Lag serveren (Hetzner)
- Ny server → **Ubuntu 24.04**, type **CX22**, EU-datasenter.
- Legg til SSH-nøkkelen din (laptop-pubkey) så du kan logge inn som root.
- Under **Firewalls**: lag en regel som kun tillater inngående **22, 80, 443**,
  og fest den til serveren.
- Noter serverens **offentlige IP** (`SERVER_IP`).

---

## 3. Kjør bootstrap
Installerer Docker, host-brannmur, `deploy`-bruker og kloner repoet til `/opt/avisa`.
Fra laptopen, i repo-mappa:
```sh
ssh root@SERVER_IP 'bash -s' < deploy/bootstrap.sh
```

---

## 4. Lag en egen deploy-nøkkel for CI og legg den på serveren
På laptopen:
```sh
ssh-keygen -t ed25519 -f ~/.ssh/avisa_deploy -N "" -C "github-actions-avisa"
ssh-copy-id -i ~/.ssh/avisa_deploy.pub deploy@SERVER_IP
```
Test: `ssh -i ~/.ssh/avisa_deploy deploy@SERVER_IP 'echo ok'` skal skrive `ok`.

---

## 5. Fyll inn `.env` på serveren
```sh
ssh deploy@SERVER_IP
cd /opt/avisa
cp .env.example .env
nano .env
```
Sett minst:
- `OPENROUTER_API_KEY=...`
- `ADMIN_PASSWORD=...` og `SESSION_SECRET=...` (lange tilfeldige strenger — påkrevd når den er offentlig)
- `PAPER_TITLE`, `PAPER_LANG`, `PREFERENCES` etter smak
- valgfritt `POLL_MINUTES=60` for å spare LLM-kost

Lagre (Ctrl+O, Enter, Ctrl+X), så `exit`.

---

## 6. Pek domenet mot serveren (Cloudflare)
I Cloudflare DNS:
- **A**-record: navn = `avis` (subdomenet ditt) → `SERVER_IP`
- Sky-ikon = **grått (DNS only)**, ikke oransje. Da ordner Caddy HTTPS selv.

> Vil du ha oransje sky (Cloudflare-proxy)? Se Option B i `deploy/README.md`.

---

## 7. Legg inn GitHub-secrets
GitHub → repoet → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Verdi |
|--------|-------|
| `DEPLOY_HOST` | `SERVER_IP` |
| `DEPLOY_USER` | `deploy` |
| `DEPLOY_SSH_KEY` | hele innholdet i `~/.ssh/avisa_deploy` (den **private** nøkkelen) |

Privatnøkkelen får du med `cat ~/.ssh/avisa_deploy` — kopier alt, inkl.
`-----BEGIN/END-----`-linjene.

---

## 8. Kjør deploy
GitHub → **Actions → Deploy → Run workflow** (eller push en liten endring til
`main`). Nå skal hele jobben bli grønn: imaget pulles på serveren, app + Caddy
starter, og Caddy henter TLS-sertifikat.

---

## 9. Verifiser
- `https://avis.dittdomene.no` → forsiden bygges (kan ta et minutt første gang).
- `https://avis.dittdomene.no/status` → JSON uten feil.
- `/settings` krever innlogging (passordet fra `.env`).

---

## 10. Nattlig backup (anbefalt)
```sh
ssh root@SERVER_IP 'crontab -l 2>/dev/null; echo "15 3 * * *  /opt/avisa/deploy/backup.sh >> /var/log/avisa-backup.log 2>&1"' | ssh root@SERVER_IP 'crontab -'
```

---

## Daglig bruk etterpå
- **Deploy ny versjon:** bare `git push origin main`. CI bygger og serveren puller.
- **Se logger:** `ssh deploy@SERVER_IP 'cd /opt/avisa && docker compose -f docker-compose.yml -f docker-compose.prod.yml logs -f'`
- **Restart:** `ssh deploy@SERVER_IP 'cd /opt/avisa && docker compose -f docker-compose.yml -f docker-compose.prod.yml restart'`
- **Endre innstillinger/kilder:** logg inn på `/settings` i nettleseren.
- **Justere LLM-kost:** øk `POLL_MINUTES` i `.env` på serveren, så `docker compose ... up -d`.

## Hvis noe feiler
- **Deploy-steget rødt:** sjekk at de tre secrets er riktige, og at
  `ssh -i ~/.ssh/avisa_deploy deploy@SERVER_IP` virker fra laptopen.
- **HTTPS funker ikke:** sjekk at A-recorden peker på riktig IP og er **grå** sky,
  og at brannmuren slipper inn 80 + 443.
- **Tom forside:** vent på første pipeline-kjøring, eller sjekk `/status` og
  loggene. Uten `OPENROUTER_API_KEY` kjører den i demo-modus (nyeste saker, ingen
  kuratering/oversetting).
