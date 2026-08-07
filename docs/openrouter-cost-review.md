# Kostnadsgjennomgang av OpenRouter

Kontrollert 7. august 2026. Priser er øyeblikksbilder i USD per én million input-/output-token.

## Konklusjon

Bytt først alle tre modellinnstillingene til `qwen/qwen3.5-flash-02-23`, mål kvalitet og faktisk `usage.cost`, og sett en hard budsjettgrense på API-nøkkelen. Modellen koster $0,065/$0,26 mot $1/$5 for Haiku 4.5 og $2/$10 for Sonnet 5. Ved samme tokenmengde betyr det omtrent 93–97 prosent lavere inferenskostnad.

Den oppgitte kostnaden på $1,20–$1,40 per dag tilsvarer $36–$42 per måned. En ren modellutskifting gir grovt $0,03–$0,09 per dag, eller $0,90–$2,70 per måned, avhengig av fordelingen mellom kuratering og oversettelse. Dette må bekreftes med faktisk bruksdata.

Lange artikkeltekster og lange oversettelsessvar gjør Sonnet 5-oversettelse til den sannsynlige hovedkostnaden i produksjon. Produksjonsforbruket kunne ikke hentes fra denne arbeidskopien.

## Hva appen gjør nå

- `.env` setter `CURATE_MODEL=anthropic/claude-haiku-4.5` og `TRANSLATE_MODEL=anthropic/claude-sonnet-5`. Overskriftsmodellen faller tilbake til Haiku 4.5.
- Full pipeline kjører normalt tre ganger daglig. Polling av kilder bruker ikke LLM.
- Hver utgave kuraterer opptil 60 kandidater i ett Haiku-kall.
- Den lokale runtime-konfigurasjonen har 18 saker, målspråk norsk og `translate_skip_langs=en`.
- Den lokale databasen er utdatert og representerer ikke nødvendigvis deploy. I dette øyeblikksbildet er alle 14 aktive kilder merket `en` eller `no`, så oversettelseskall skal normalt utebli. Ferdige oversettelser lagres dessuten i databasen.
- De 12 nyeste lokale utgavene inneholder 56 000–110 000 tegn hver, vanligvis rundt 80 000. Grovt 20 000 input- og 20 000 output-token på Sonnet 5 koster rundt $0,24 per oversatt utgave. Tre utgaver, kuratering og overskrifter gjør den oppgitte dagskostnaden plausibel dersom engelsk faktisk oversettes i produksjon.
- OpenRouter-svaret reduseres til `choices[0]`. Appen lagrer ikke `usage`, kostnad, tokenantall, cachetreff, valgt modell eller provider.
- Kallet angir én modell og ingen provider-regler. OpenRouter velger derfor provider etter standardruting, men appen har ingen fallback til en annen modell.

Den lokale repo- og databasetilstanden kan ikke forklare eller avkrefte $1,20–$1,40 per dag. Mulige tilleggskilder er mange manuelle rebuilds, feil eller ukjent kildespråk i deploy-databasen, eller at API-nøkkelen brukes av noe annet. Kontroller OpenRouter Activity gruppert på API-nøkkel og modell før kode endres.

## Aktuelle modeller

| Modell | Input | Output | Vurdering |
| --- | ---: | ---: | --- |
| `anthropic/claude-sonnet-5` | $2,00 | $10,00 | Nåværende kroppsoversettelse. Unødvendig dyr her. |
| `anthropic/claude-haiku-4.5` | $1,00 | $5,00 | Nåværende kuratering og overskrifter. |
| `qwen/qwen3.5-flash-02-23` | $0,065 | $0,26 | Anbefalt kostnadstest. Lansert i 2026, én provider. |
| `google/gemini-3.1-flash-lite` | $0,25 | $1,50 | GA-modell fra 2026. Flere providere og minimal reasoning som standard. |
| `qwen/qwen3.7-flash` | $0,03 | $0,13 under 32k input | Nyest og billigst, men reasoning er på som standard. Krever egen eval og eksplisitt avslag. |

Prisene kommer fra OpenRouters [live Models API](https://openrouter.ai/api/v1/models). Se også modellsidene for [Haiku 4.5](https://openrouter.ai/anthropic/claude-haiku-4.5), [Gemini 3.1 Flash Lite](https://openrouter.ai/google/gemini-3.1-flash-lite-20260507) og [Qwen 3.5 Flash](https://openrouter.ai/qwen/qwen3.5-flash-02-23). Priser og rabatter kan endres.

Foreslått konfigurasjon for første test:

```dotenv
CURATE_MODEL=qwen/qwen3.5-flash-02-23
TRANSLATE_MODEL=qwen/qwen3.5-flash-02-23
TRANSLATE_HEADLINES_MODEL=qwen/qwen3.5-flash-02-23
```

Test minst 20–30 representative saker før permanent bytte: norsk språk, rangering, JSON/sentinel-format, avkorting og innholdstap. Bruk Gemini 3.1 Flash Lite dersom Qwen ikke holder kvalitetsmålet.

## Måling og kostnadsvern

OpenRouter inkluderer automatisk `usage` i hvert svar, med native input-, output-, reasoning- og cache-token, samt `cost`. Appen ignorerer dette i dag. [Usage Accounting](https://openrouter.ai/docs/cookbook/administration/usage-accounting) beskriver feltene. Første kodeendring bør logge minst:

- tidspunkt og pipeline-steg
- forespurt og faktisk modell
- `usage.prompt_tokens`, `usage.completion_tokens` og eventuelle reasoning-token
- `usage.cost`
- `cached_tokens` og `cache_write_tokens`

I mellomtiden gir OpenRouter Activity historikk filtrert på modell, provider og API-nøkkel. Opprett en egen nøkkel for Avisa. Sett daglig eller månedlig budsjett og modell-allowlist med [Guardrails](https://openrouter.ai/docs/guides/features/guardrails/overview). Da kan ikke en feil eller dyr fallback løpe fra budsjettet.

## Caching

[Prompt caching](https://openrouter.ai/docs/guides/best-practices/prompt-caching) gir rabatt på gjentatte, identiske prompt-prefikser. Anthropic krever cachekontroll og minst 4096 token for Haiku 4.5. Appens korte systemprompter og nye artikkeltekster per kall gir liten forventet gevinst. Gemini cacher automatisk, men også der er gjenbruket begrenset.

[Response caching](https://openrouter.ai/docs/guides/features/response-caching) er beta. Identiske requests kan gi gratis cachetreff med `X-OpenRouter-Cache: true`, men ferske utgaver er normalt ulike. Det kan beskytte mot eksakte dobbeltkjøringer, men er ikke hovedgrepet. Appens databasecache for ferdige oversettelser er viktigere.

## Ruting og fallbacks

OpenRouter ruter som standard mellom providere for samme modell med pris og oppetid i vurderingen. `provider.sort: "price"` eller suffikset `:floor` prioriterer absolutt laveste providerpris. `provider.max_price` kan sette en hard pristaksgrense. Se [Provider Routing](https://openrouter.ai/docs/guides/routing/provider-selection).

En `models`-liste gir fallback mellom modeller ved feil. Modellen som faktisk svarer faktureres. Se [Model Fallbacks](https://openrouter.ai/docs/guides/routing/model-fallbacks). For Avisa bør billig modell være primær og eventuell fallback også ha et pristak. En dyr frontiermodell som automatisk fallback gjør dagskostnaden uforutsigbar.

OpenRouter har også rabatterte batch- og flex-varianter for støttede modeller. De kan passe planlagte utgaver, men appen forventer i dag et synkront svar innen 120 sekunder. Test kompatibilitet og forsinkelse før bruk.

## Gebyrer

OpenRouter oppgir ingen markup på inferensprisene. Pay-as-you-go har 5,5 prosent gebyr ved kjøp av credits, minst $0,80. Direkte Anthropic fjerner dermed kjøpsgebyret, men ikke modellkostnaden, og løser ikke hovedproblemet. [OpenRouter FAQ](https://openrouter.ai/docs/faq) beskriver gebyrene.

BYOK-reglene beskrives ulikt på OpenRouters nåværende FAQ og prisside. Ved dette lave volumet er BYOK uansett ikke et viktigere grep enn billigere modell, måling og budsjettgrense.

## ChatGPT-abonnement som alternativ

Et ChatGPT-abonnement kan ikke brukes som vanlig API-kreditt. OpenAI fakturerer API-nøkler etter standard API-priser.

Det finnes likevel en teknisk abonnementsvei: Codex CLI kan logge inn med ChatGPT for abonnementsbruk, og `codex exec` støtter skriptbare, ikke-interaktive kjøringer. Plus inkluderer Codex CLI og GPT-5.6 Luna. Se OpenAIs offisielle dokumentasjon for [autentisering](https://learn.chatgpt.com/docs/auth), [priser og planstøtte](https://learn.chatgpt.com/docs/pricing) og [`codex exec`](https://learn.chatgpt.com/docs/developer-commands?surface=cli#codex-exec).

Dette er mulig som et personlig eksperiment, men svakt som produksjonsløsning:

- deploy-containeren må ha Codex CLI, en vedvarende personlig innlogging og fungerende tokenfornyelse
- avisjobbene deler ChatGPT/Codex-grensen med din vanlige bruk
- Codex er en agent, ikke en enkel oversettelses-API, og gir mer overhead og flere feilmåter
- OpenAI anbefaler API-nøkkel for programmatisk CI/CD-bruk, som igjen faktureres separat

For Avisa er en modell som koster cent per dag via OpenRouter mer robust enn å gjøre abonnementets personlige innlogging til produksjonsavhengighet. En Codex-provider kan eventuelt prøves som lokal fallback, ikke som første kostnadsgrep.

## Prioritert plan

1. Finn modellen og API-nøkkelen som står for kostnaden i OpenRouter Activity.
2. Gi Avisa egen nøkkel med budsjett og modell-allowlist.
3. Bytt de tre modellene til Qwen 3.5 Flash og restart deploy.
4. Kjør en kontrollert kvalitetsprøve. Test Gemini 3.1 Flash Lite som kvalitetsalternativ.
5. Logg `usage.cost` per pipeline-steg i appen.
6. Vurder Codex CLI med ChatGPT-innlogging kun som et lokalt eksperiment.
7. Reduser utgaver fra tre til to eller forsiden fra 18 til 12 bare hvis kostnaden fortsatt er for høy.
