# Avisa

En personlig avis som **poller nyheter i bakgrunnen**, kuraterer og **oversetter
forsidesakene til norsk** med LLM, og serverer en ferdig broadsheet på nett —
slik at dataene alltid er klare og ingenting prosesseres on-the-fly når du leser.

Inspirert av [openpaper](https://github.com/falense/openpaper/), men snudd om:
openpaper kjører hele pipelinen *live* i Claude Code hver gang. Her er pipelinen
en alltid-på bakgrunnstjeneste, og lesingen er øyeblikkelig.

## Slik henger det sammen

```
BAKGRUNN (scheduler, hvert POLL_MINUTES)                    WEB (umiddelbart)
  ingest → content → curate → content* → translate → build → forside / artikkel / flere
  (RSS/   (fulltekst   (LLM      (sikrer    (LLM →      (lagrer
   API/    for nye      mot       fulltekst  norsk,      utgave
   PW)     saker)       profil)   på forside) inkl. body) i DB)
```

- **ingest** — henter fra kildene i `sources.yaml`. Tre fetcher-typer som openpaper: RSS (feedparser), API (httpx), Playwright (Chromium for feedløse JS-sider). Deduperer på URL-hash.
- **content** — henter **full brødtekst** for nye saker: statisk uttrekk (httpx + trafilatura) først, Playwright-fallback for JS-tunge sider. Kappet til `CONTENT_FETCH_LIMIT` nyeste per kjør; forsidesakene garanteres fulltekst etter kuratering.
- **curate** — LLM rangerer ferske saker mot din redaksjonelle profil (`PREFERENCES`) og velger forsiden. Uten LLM-nøkkel: nyeste saker.
- **translate** — oversetter **kun de kuraterte** sakene til norsk bokmål, inkludert hele brødteksten. Cachet per artikkel (`translated_at`), så aldri dobbeltarbeid.
- **build** — fryser en `Edition`. Forsiden viser alltid nyeste utgave.

Web-rutene leser ferdig data:
- `GET /` — forsiden (nyeste utgave)
- `GET /article/{id}` — enkeltartikkel
- `GET /more` — flere saker (paginering over korpuset, umiddelbart)
- `POST /refresh` — «hent nytt nå»-knappen: trigger pipeline i bakgrunnen

## Kjøre lokalt

```bash
cd avisa
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # legg inn OPENROUTER_API_KEY hvis du vil ha kuratering + oversettelse
uvicorn app.main:app --reload
```

Åpne http://localhost:8000. Første gang bygges en utgave i bakgrunnen — last
siden på nytt etter noen sekunder.

Bruk gjerne styringsskriptet i stedet for å huske kommandoer:

```bash
./avisa setup     # interaktiv veiviser: tittel, profil, kilder
./avisa start     # start i bakgrunnen
./avisa stop      # stopp
./avisa logs      # følg loggen
```

> **Uten LLM funker alt**: appen faller tilbake på nyeste saker og hopper over
> oversettelse (beholder originaltekst), så du ser hele flyten med en gang.

### LLM lokalt — uten API-nøkkel

På localhost trenger du ikke OpenRouter. Med `LLM_PROVIDER=auto` (standard) bruker
appen din **innloggede `claude`-CLI-session** for kuratering og oversettelse hvis
ingen OpenRouter-nøkkel er satt. Da får du intelligent kuratering + norsk
oversettelse gratis via Claude Code-abonnementet ditt. På server (uten `claude`)
brukes OpenRouter.

## Kjøre på VPS (Hetzner/Fly/Railway o.l.)

```bash
cp .env.example .env        # fyll inn nøkkel + ev. modeller/profil
docker compose up -d --build
```

SQLite-databasen ligger i et navngitt volum (`avisa-data`), så data overlever
restart og redeploy. Sett en reverse proxy (Caddy/nginx) foran for HTTPS.

## Konfig (.env)

Merk: `PREFERENCES`, `PAPER_TITLE`, `FRONT_PAGE_SIZE` og `POLL_MINUTES` er bare
*startverdier*. Endrer du dem i innstillingene (web/veiviser), lagres de i DB og
overstyrer env.

| Variabel | Forklaring |
|----------|-----------|
| `ADMIN_PASSWORD` | Beskytter admin-flatene. Tom = ingen innlogging (alt åpent). |
| `SESSION_SECRET` | Cookie-signering (valgfri; faller tilbake på `ADMIN_PASSWORD`). |
| `LLM_PROVIDER` | `auto` (anbefalt) / `openrouter` / `claude_cli` / `none`. |
| `OPENROUTER_API_KEY` | OpenRouter-nøkkel. Tom + `auto` = bruk lokal Claude-session. |
| `CURATE_MODEL` / `TRANSLATE_MODEL` | OpenRouter-modell-ID, f.eks. `anthropic/claude-haiku-4.5`. |
| `CLAUDE_MODEL` | Modell for lokal claude-CLI (tom = CLI-standard). Sett til `haiku` for fart. |
| `TRANSLATE_CONCURRENCY` | Antall oversettelses-batch-kall samtidig (standard 4). |
| `TRANSLATE_BATCH_MAX` / `TRANSLATE_BATCH_CHARS` | Hvor mange artikler / tegn som pakkes per kall. |
| `POLL_MINUTES` | Startverdi: hvor ofte pollingen kjører. |
| `FRONT_PAGE_SIZE` | Startverdi: antall saker i avisa. |
| `CONTENT_FETCH_LIMIT` | Maks nye saker som fulltekst-hentes per kjør. |
| `USE_PLAYWRIGHT` | Browser-fallback for JS-tunge sider (`true`/`false`). |
| `PREFERENCES` | Startverdi: redaksjonell profil som styrer kurateringen. |
| `PAPER_TITLE` | Startverdi: tittel på avisa. |

## Konfigurere kilder og preferanser

Fire måter, alle lagrer til DB (overstyrer env):

- **Snakk med konfiguratoren** (krever LLM) — fritekstfeltet øverst på `/settings`.
  Skriv «legg til aftenposten.no og e24.no, fjern Hacker News, mer om klima, kall
  avisa Kveldsposten». LLM-en tolker det til konkrete handlinger (legg til/fjern/
  skru av kilder, juster profil, tittel, størrelse, poll-intervall) og utfører dem,
  og bygger avisa på nytt hvis noe relevant endret seg.
- **Veiviser** — `./avisa setup` stiller spørsmål om tittel, profil og kilder.
- **Innstillingsside** — `⚙ Innstillinger` på forsiden (`/settings`): rediger
  profil, tittel, forsidestørrelse og poll-intervall, og legg til / skru av /
  slett kilder. Endret intervall trer i kraft umiddelbart.
- **Tilbakemelding** — feltet «Til redaktøren» nederst på forsiden. Skriv f.eks.
  «mer klima, færre meningsinnlegg»; LLM-en justerer profilen og avisa bygges på
  nytt. Uten LLM legges tilbakemeldingen som et notat i profilen.

### Smart kilde-oppsett

Du trenger ikke vite feed-URL, type eller selector. Lim inn et navn eller en URL
(«nrk.no», «aftenposten.no») i innstillingene eller veiviseren, så:

1. finner appen RSS-feeden automatisk (leser `<link>`-tagger + vanlige stier),
2. lar LLM-en velge beste feed, navngi og seksjonere kilden — og til og med gjette
   en kjent feed-URL når siden ikke deklarerer en (f.eks. BBC), og
3. for **feedløse JS-sider**: renderer siden med Playwright, lar LLM-en foreslå en
   CSS-selector for artikkel-lenkene, og **validerer den** ved å kjøre den før
   kilden lagres (`kind: playwright`).

Uten LLM brukes den deterministiske RSS-oppdagingen alene.

### Live fremdrift

Når avisa bygges, viser forsiden hva som skjer i sanntid — hvilket steg (henter,
fulltekst, kuraterer, oversetter, bygger), teller (f.eks. «Oversetter 5/12») og
medgått tid. Førstegangsbygging viser et eget panel som oppdaterer seg selv og
laster avisa når den er klar. Fremdriften eksponeres på `GET /status` (JSON).

### Lesing

Forsiden har en **seksjonsmeny** (sticky) som hopper til Innenriks/Utenriks/
Teknologi osv., **bilder** på topp-, sekundær- og seksjonssaker der kilden har
dem, og artikkelsidene viser **kilde, dato, lesetid** og **forrige/neste**-bla
innenfor utgaven — så du kan lese deg gjennom avisa som en avis.

## Innlogging

Lesing (forsiden, artikler, «flere saker») er alltid åpen. Admin-flatene
(`/settings`, kilder, tilbakemelding, oppdater-knappen) krever innlogging når
`ADMIN_PASSWORD` er satt — da bouncer et forsøk uten gyldig cookie til `/login`.
Er `ADMIN_PASSWORD` tom, er alt åpent (greit lokalt / bak VPN). Cookien er et
signert HMAC-token (stdlib, ingen ekstra avhengigheter, ingen server-state).

## Legge til kilder

Rediger `sources.yaml` og slett `avisa.db` (eller volumet) for re-seed. Typer:

- `rss` — alle RSS/Atom-feeder.
- `api` — JSON-API (foreløpig Hacker News / Algolia).
- `playwright` — feedløse JS-sider. Krever `config.link_selector` (CSS-selector
  for artikkel-lenkene). Se eksempelet nederst i `sources.yaml`.

Uansett type hentes full brødtekst per sak i content-fasen.

## Kjente begrensninger i dette utkastet

- **Oversettelse re-kjøres ikke** automatisk hvis du legger til en nøkkel i
  etterkant: nullstill `translated_at` på de aktuelle radene for å oversette på nytt.
- **Fulltekst-cap**: kun `CONTENT_FETCH_LIMIT` nyeste nye saker fulltekst-hentes
  per kjør (forsidesakene garanteres uansett). Resten tas i påfølgende kjør.
- **Auth er én delt passord-cookie** uten utløp/rotasjon — nok for én bruker bak
  proxy/VPN, ikke flerbruker-håndtering.
- **`/more`** paginerer hele korpuset i minnet; helt fint på dette nivået, bør
  bli en SQL-paginering når basen vokser.
- **Lokalt krever Python 3.13** (ikke 3.14 — SQLModel-inkompatibilitet). Docker
  bruker Playwrights offisielle basebilde og er upåvirket.

Se `app/` for koden — hvert pipeline-steg er én liten fil under `app/pipeline/`.
