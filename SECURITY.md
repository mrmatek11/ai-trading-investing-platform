# Bezpieczeństwo

GoldTape powstał głównie do własnych analiz. Jest używalny także dla kilku osób, ale **nie przeszedł niezależnego
audytu ani testów penetracyjnych** — poniżej jest to, co sprawdzono i co nie.

## Zanim wystawisz aplikację do internetu

1. **Włącz logowanie.** Bez niego (tryb jednego użytkownika) każdy, kto zna adres, widzi cały journal. Najprościej: logowanie
   przez Discord (`TAPE_DISCORD_CLIENT_ID`, `TAPE_DISCORD_CLIENT_SECRET`, `TAPE_SESSION_SECRET`, `TAPE_APP_URL`),
   alternatywnie OIDC (Clerk) albo stały `TAPE_API_TOKEN`. Z publicznym `TAPE_APP_URL` i bez żadnego z nich API odmawia startu.
2. **Użyj https** (np. Caddy przed nginx) i ustaw `TAPE_TRUST_PROXY` na liczbę proxy przed API (z Caddy: `2`).
3. **Ustaw `TAPE_SECRET_KEYS`** i zrób jego kopię w menedżerze haseł. Szyfruje klucze AI, tokeny brokerów i webhooki;
   bez niego są nie do odczytania.
4. Nie publikuj portu API (`8000`) ani bazy — compose wystawia tylko nginx, w produkcji tylko na `127.0.0.1`.
5. Nie commituj `.env`.

Instrukcja krok po kroku: [docs/URUCHOMIENIE.md](docs/URUCHOMIENIE.md).

## Co jest sprawdzone (własnymi testami)

- Dane jednego konta nie są widoczne dla innego (journal, statystyki, przegląd AI, połączenia, paper trading, tokeny MCP).
- Konto zawsze pochodzi z uwierzytelnienia, nigdy z parametrów żądania.
- Logowanie Discord: `state` przeciw podszyciu, sesja w ciasteczku HttpOnly/SameSite=Lax (Secure pod https),
  zmiany danych tylko z nagłówkiem Origin aplikacji (CSRF).
- Sekrety: AES-256-GCM z kluczem na sekret i rotacją kluczy; tokeny MT5 i MCP w bazie wyłącznie jako skróty SHA-256.
- Cały XML (import, IBKR, RSS) przez `defusedxml`; webhook Discord walidowany co do adresu; limit rozmiaru plików.
- Limit zapytań na publicznych endpointach (ingest MT5, MCP, logowanie).
- Zależności: `npm audit` i `pip-audit` bez znanych podatności w chwili sprawdzenia.
- Zabezpieczenia są testowane mutacyjnie: usunięcie kontroli izolacji, `state`, CSRF, walidacji liczb w odpowiedziach AI
  albo limitera powoduje czerwony test.

## Znane ograniczenia

- Wylogowanie usuwa ciasteczko tylko w tej przeglądarce; sesja (30 dni) nie jest unieważniana po stronie serwera.
- Brak logowania dwuskładnikowego.
- Limit zapytań liczony w pamięci procesu — przy kilku workerach serwera rzeczywisty limit jest odpowiednio większy.
- Haszowanie `sha1` w kodzie służy tylko do identyfikatorów (klucz pozycji, identyfikator zdarzenia newsa), nie do zabezpieczeń.
- Integracje z Telegramem, Discordem, OANDA, Twelve Data i DeepSeek były testowane na odpowiedziach o prawdziwym formacie,
  nie na żywych usługach.
- Dostawcy cen: darmowe i demo plany są do użytku własnego; pokazywanie cen innym wymaga licencji.

## Odpowiedzialność

Aplikacja to narzędzie analityczne, nie doradztwo inwestycyjne. Kto hostuje ją dla innych, odpowiada za ich dane,
licencje na dane rynkowe i za to, jak komunikuje wyniki (paper trading jest testem strategii, nie rekomendacją).

## Zgłaszanie błędów

Podatności zgłoś prywatnie przez **GitHub → Security → Report a vulnerability** (a nie publicznym issue).
