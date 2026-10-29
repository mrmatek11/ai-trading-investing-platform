# Uruchomienie GoldTape — lokalnie albo na własnym serwerze

## Co wybrać

| | Na swoim komputerze | Na małym serwerze (VPS) |
|---|---|---|
| Koszt | 0 zł | rzędu 20–30 zł/mies. (np. Hetzner CX22 albo podobny) |
| Journal, import XTB/MT5, statystyki, przegląd AI | ✅ | ✅ |
| Paper trading i plan dnia na Telegram/Discord | tylko gdy komputer jest włączony w dni robocze od ok. 08:00 do 17:00 czasu Londynu | ✅ działa sam, 24/7 |
| Synchronizacja MT5 przez EA | EA musi widzieć adres komputera (ta sama sieć) | ✅ zwykły adres https |
| Dostęp z telefonu | tylko w domowej sieci | ✅ z każdego miejsca |

**Rekomendacja:** zacznij lokalnie, żeby wszystko poklikać. Paper trading i plan dnia mają sens dopiero na serwerze:
test potrzebuje 100 transakcji, a strategia handluje tylko po dniu NR7 — w badaniu ok. 32 transakcje rocznie,
czyli około 3 lat. Każdy dzień z wyłączonym komputerem to dzień pominięty w teście.

## Lokalnie (Windows, macOS, Linux)

1. Zainstaluj **Docker Desktop** i **Git**.
2. Pobierz repozytorium:
   ```bash
   git clone https://github.com/mrmatek11/ai-trading-investing-platform.git
   cd ai-trading-investing-platform/tape
   cp .env.example .env          # Windows PowerShell: copy .env.example .env
   ```
3. W pliku `.env` ustaw co najmniej:
   - `POSTGRES_PASSWORD` — dowolne długie hasło;
   - `TAPE_SECRET_KEYS` — klucz szyfrowania (zapisuje klucze AI, tokeny brokerów, webhooki). Wygeneruj:
     ```bash
     python -c "import os,base64;print('k1:'+base64.b64encode(os.urandom(32)).decode())"
     ```
     albo bez Pythona: `docker run --rm python:3.12-slim python -c "import os,base64;print('k1:'+base64.b64encode(os.urandom(32)).decode())"`.
4. Uruchom:
   ```bash
   docker compose up -d --build
   ```
   i otwórz **http://localhost:8080**. Bez logowania aplikacja działa w trybie jednego użytkownika.
5. Zatrzymanie: `docker compose down` (dane zostają w wolumenie bazy). Aktualizacja: `git pull` i znowu krok 4.

### Paper trading i ceny na żywo

1. Załóż darmowe konto demo **OANDA (fxTrade Practice)** i wygeneruj token API w ustawieniach konta.
2. W `.env`:
   ```
   TAPE_PRICE_PROVIDER=oanda
   OANDA_TOKEN=twój-token
   OANDA_ENV=practice
   ```
3. Uruchom razem z workerami cen, kalendarza i paper tradingu:
   ```bash
   docker compose --profile prices up -d --build
   ```
4. W aplikacji: **Paper trading → Uruchom**. Pierwszy dzień przebiegu to następny dzień handlowy.

### Plan dnia i brief na Telegram (opcjonalnie)

1. W Telegramie u **@BotFather**: `/newbot`, skopiuj token.
2. W `.env`: `TAPE_TELEGRAM_BOT_TOKEN=…` i `TAPE_TELEGRAM_BOT_USERNAME=nazwa_bota`.
3. `docker compose --profile prices up -d` → w aplikacji **Brief dnia → Połącz z Telegramem** → Start w Telegramie.
4. Przy przebiegu paper zaznacz **„Wysyłaj plan dnia na Telegram/Discord”**.

### Klucz AI (opcjonalnie)

W aplikacji: **Ustawienia → Twój klucz AI** (Claude albo DeepSeek). Klucz jest szyfrowany kluczem z `TAPE_SECRET_KEYS`.

## Na VPS tylko do paper tradingu (bez domeny i logowania)

Aplikacja słucha tylko na `127.0.0.1` serwera, więc z internetu jej nie widać. Ty wchodzisz przez tunel SSH,
a workery (ceny, paper, plan dnia na Telegram) działają same 24/7. Cały stos zajmuje ok. 0,5 GB RAM,
więc wystarczy VPS z 1 GB (z 2 GB jest zapas).

1. Na VPS (Ubuntu/Debian):
   ```bash
   curl -fsSL https://get.docker.com | sh
   git clone https://github.com/mrmatek11/ai-trading-investing-platform.git
   cd ai-trading-investing-platform/tape
   cp .env.example .env
   sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(openssl rand -hex 24)|; s|^TAPE_SECRET_KEYS=.*|TAPE_SECRET_KEYS=k1:$(openssl rand -base64 32)|" .env
   ```
2. W `.env` uzupełnij (`nano .env`), resztę zostaw pustą — zwłaszcza `TAPE_APP_URL`:
   ```
   TAPE_PRICE_PROVIDER=oanda
   OANDA_TOKEN=twój-token-z-OANDA-demo
   OANDA_ENV=practice
   TAPE_TELEGRAM_BOT_TOKEN=token-od-BotFather
   TAPE_TELEGRAM_BOT_USERNAME=nazwa_bota
   ```
   Zrób kopię `.env` u siebie (np. w menedżerze haseł): bez `TAPE_SECRET_KEYS` zapisane sekrety są nie do odczytania.
3. Start (po restarcie serwera wstaje sam):
   ```bash
   docker compose --profile prices up -d --build
   docker compose --profile prices ps                 # wszystko „Up”, api „healthy”
   docker compose logs --tail 5 prices paper          # ceny: liczby świec, nie „błąd: …”
   ```
4. Na swoim komputerze (Windows PowerShell, macOS, Linux) otwórz tunel i zostaw okno otwarte:
   ```bash
   ssh -N -L 8080:127.0.0.1:8080 root@ADRES_IP_VPS
   ```
   i wejdź na **http://localhost:8080**. Tunel jest potrzebny tylko do oglądania — test działa bez niego.

   **Masz Tailscale na VPS?** Zamiast tunelu, jednorazowo na serwerze:
   ```bash
   sudo tailscale serve --bg 8080
   ```
   i wchodzisz z każdego urządzenia w swojej sieci Tailscale (też z telefonu) na `https://<nazwa-vps>.<tailnet>.ts.net`.
   Adres `http://100.x.y.z:8080` nie zadziała — port słucha tylko na `127.0.0.1`. Linki do aplikacji w wiadomościach
   na Telegramie: `TAPE_APP_URL=https://<nazwa-vps>.<tailnet>.ts.net` i `TAPE_ALLOW_OPEN=1` (dostęp chroni Tailscale).
5. W aplikacji: **Brief dnia → Połącz z Telegramem** (Start w Telegramie), potem **Paper trading → Uruchom**
   z zaznaczonym **„Wysyłaj plan dnia na Telegram/Discord”**. Pierwszy dzień przebiegu to następny dzień handlowy.
6. Kopia bazy co noc — `crontab -e` i dopisz (w crontabie `%` trzeba poprzedzić `\`):
   ```
   0 3 * * * mkdir -p ~/backup && cd ~/ai-trading-investing-platform/tape && docker compose exec -T db pg_dump -U tape tape | gzip > ~/backup/goldtape-$(date +\%F).sql.gz
   ```

**Aktualizacje w trakcie testu:** `git pull && docker compose --profile prices up -d --build`. Jeśli zmiana dotknie
silnika strategii (`tape/backend/tape/paper/engine.py`), przebieg zatrzyma się jako „mismatch” i test zacznie się od
zera — to celowe. Przed `git pull` sprawdź: `git fetch && git diff --stat HEAD origin/main -- backend/tape/paper/engine.py`
(pusto = bezpiecznie).

Gdy zechcesz wejść z telefonu albo pod własną domeną — dalej, „Na serwerze (VPS)”.

## Na serwerze (VPS)

1. Najmniejszy serwer z Ubuntu (2 vCPU, 4 GB RAM wystarczy) i domena, np. `goldtape.twojadomena.pl`
   wskazująca na jego adres IP.
2. Zainstaluj Dockera (`curl -fsSL https://get.docker.com | sh`), potem kroki 2–3 z części lokalnej
   i ustawienia OANDA / Telegram.
3. W `.env` dodatkowo:
   ```
   TAPE_APP_URL=https://goldtape.twojadomena.pl
   TAPE_TRUST_PROXY=2
   ```
   (`2`, bo przed nginx z aplikacji stoi jeszcze Caddy z certyfikatem — inaczej limit zapytań traktowałby
   wszystkich klientów jak jednego).
4. **Logowanie jest obowiązkowe, gdy aplikacja jest w internecie.** Najprościej logowanie przez Discord:
   `TAPE_DISCORD_CLIENT_ID`, `TAPE_DISCORD_CLIENT_SECRET`, `TAPE_SESSION_SECRET` (opis w `tape/README.md`).
   Bez tego każdy, kto zna adres, widziałby Twój journal.
   Aplikacja pilnuje tego sama: z publicznym `TAPE_APP_URL` i bez logowania nie wystartuje (komunikat wskaże, co ustawić).
5. Certyfikat https — Caddy przed aplikacją (`/etc/caddy/Caddyfile`):
   ```
   goldtape.twojadomena.pl {
       reverse_proxy 127.0.0.1:8080
   }
   ```
   Port aplikacji słucha tylko na `127.0.0.1`, więc z internetu da się wejść wyłącznie przez Caddy.
6. `docker compose --profile prices up -d --build` — działa po restarcie serwera sam (`restart: unless-stopped`).
7. Kopia zapasowa bazy (np. raz na dobę z crona):
   ```bash
   docker compose exec -T db pg_dump -U tape tape | gzip > goldtape-$(date +%F).sql.gz
   ```

## Ważne

- Plik `.env` zawiera sekrety — nie commituj go (jest w `.gitignore`) i nie zgub `TAPE_SECRET_KEYS`:
  bez niego zapisane klucze AI i tokeny brokerów są nie do odczytania.
- Darmowe/demo dane cen są do użytku własnego. Pokazywanie ich innym płacącym użytkownikom wymaga licencji dostawcy.
- Paper trading to test strategii, nie rekomendacja inwestycyjna.
