# Docker Image Watch

Automatischer Docker Container Update-Checker und Updater. Überwacht laufende Container auf Image-Updates und aktualisiert sie automatisch.

## Features

- **Automatische Update-Erkennung**: Prüft regelmäßig auf neue Image-Versionen
- **Automatische Container-Aktualisierung**: Aktualisiert Container mit neuen Images unter Beibehaltung der Konfiguration
- **Automatische Bereinigung**: Entfernt nicht mehr benötigte Images nach Updates
- **Flexible Planung**: Crontab-Syntax für Update-Intervalle
- **Sicher**: Läuft als Non-Root-Benutzer mit minimalen Berechtigungen
- **Leichtgewichtig**: Basiert auf Alpine Linux

## Schnellstart

### Mit Docker Compose (empfohlen)

1. Klone das Repository oder kopiere die Dateien
2. Kopiere `.env.example` zu `.env` und passe die Einstellungen an
3. Starte den Container:

```bash
docker-compose up -d
```

### Mit Docker

```bash
docker build -t docker-image-watch .

docker run -d \
  --name docker-image-watch \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  -e UPDATE_SCHEDULE="0 4 * * *" \
  -e RUN_ON_STARTUP=false \
  --restart unless-stopped \
  docker-image-watch
```

## Konfiguration

### Umgebungsvariablen

| Variable | Standard | Beschreibung |
|----------|----------|--------------|
| `UPDATE_SCHEDULE` | `0 4 * * *` | Cron-Schedule für Update-Prüfungen |
| `RUN_ON_STARTUP` | `false` | Update-Prüfung beim Start ausführen |
| `WEBHOOK_URL` | - | Webhook-URL für Benachrichtigungen |
| `WEBHOOK_FORMAT` | `auto` | Format: `auto`, `discord`, `slack`, `telegram`, `markdown`, `json` |
| `WEBHOOK_ON_UPDATE` | `true` | Webhook bei Updates senden |
| `WEBHOOK_ON_ERROR` | `true` | Webhook bei Fehlern senden |
| `WEBHOOK_ALWAYS` | `false` | Webhook immer senden (auch ohne Änderungen) |

### Webhook-Konfiguration

Docker Image Watch kann nach jedem Update-Zyklus Benachrichtigungen an verschiedene Dienste senden.

#### Discord

```bash
WEBHOOK_URL=https://discord.com/api/webhooks/123456789/abcdefg
WEBHOOK_FORMAT=auto  # wird automatisch erkannt
```

#### Slack

```bash
WEBHOOK_URL=https://hooks.slack.com/services/T00000000/B00000000/XXXXXXXX
WEBHOOK_FORMAT=auto
```

#### Telegram

```bash
WEBHOOK_URL=https://api.telegram.org/bot<BOT_TOKEN>/sendMessage?chat_id=<CHAT_ID>
WEBHOOK_FORMAT=telegram
```

#### Generic Webhook (z.B. ntfy, Gotify, etc.)

```bash
WEBHOOK_URL=https://ntfy.sh/my-topic
WEBHOOK_FORMAT=markdown  # sendet Markdown-formatierten Text
```

#### Nur bei Updates/Fehlern benachrichtigen

```bash
WEBHOOK_ON_UPDATE=true   # Bei Container-Updates
WEBHOOK_ON_ERROR=true    # Bei Fehlern
WEBHOOK_ALWAYS=false     # Nicht bei jedem Lauf
```

### Cron-Schedule Beispiele

| Schedule | Beschreibung |
|----------|--------------|
| `0 4 * * *` | Täglich um 4:00 Uhr |
| `0 */6 * * *` | Alle 6 Stunden |
| `0 0 * * 0` | Wöchentlich Sonntag um Mitternacht |
| `*/30 * * * *` | Alle 30 Minuten |
| `0 2 * * 1-5` | Wochentags um 2:00 Uhr |
| `0 3 1 * *` | Monatlich am 1. um 3:00 Uhr |

### Container von Updates ausschließen

Um einen Container von automatischen Updates auszuschließen, füge das Label hinzu:

```yaml
services:
  my-service:
    image: my-image:latest
    labels:
      - "docker-image-watch.disable=true"
```

Oder mit Docker:

```bash
docker run -d --label docker-image-watch.disable=true my-image
```

## Funktionsweise

1. **Prüfung**: Der Watcher prüft alle laufenden Container
2. **Pull**: Für jeden Container wird die neueste Image-Version heruntergeladen
3. **Vergleich**: Die Image-Digests werden verglichen
4. **Update**: Bei Unterschieden wird der Container neu erstellt
5. **Bereinigung**: Nicht mehr verwendete Images werden entfernt

## Sicherheitshinweise

- Der Container benötigt Zugriff auf den Docker Socket
- Der Socket wird schreibgeschützt gemountet (`:ro`)
- Der Container läuft als Non-Root-Benutzer
- Read-only Dateisystem aktiviert
- Ressourcenlimits sind konfiguriert

## CI/CD Pipeline

Das Projekt nutzt Gitea Actions für automatisierte Builds. Bei jedem Merge in den `main`-Branch wird automatisch:

1. Das Docker-Image gebaut
2. In die Gitea Container Registry gepusht
3. Mit folgenden Tags versehen:
   - `latest` - Immer die neueste Version
   - `<commit-sha>` - Spezifische Commit-Version
   - `<datum-zeit>` - Zeitstempel des Builds

### Image von der Registry pullen

```bash
# Ersetze <gitea-url> und <user/repo> mit deinen Werten
docker pull <gitea-url>/<user/repo>/docker-image-watch:latest
```

### Deployment auf Remote-Server

```bash
# 1. In Gitea Registry einloggen
docker login <gitea-url>

# 2. Image pullen
docker pull <gitea-url>/<user/repo>/docker-image-watch:latest

# 3. Container starten
docker run -d \
  --name docker-image-watch \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -e UPDATE_SCHEDULE="0 4 * * *" \
  -e WEBHOOK_URL="https://discord.com/api/webhooks/..." \
  --restart unless-stopped \
  <gitea-url>/<user/repo>/docker-image-watch:latest
```

### Gitea Actions Setup

1. Erstelle ein Personal Access Token in Gitea mit `write:package` Berechtigung
2. Füge das Token als Secret `GITEA_TOKEN` in den Repository-Settings hinzu
3. Aktiviere Gitea Actions für das Repository

## Logs anzeigen

```bash
# Alle Logs
docker logs docker-image-watch

# Live-Logs folgen
docker logs -f docker-image-watch

# Letzte 100 Zeilen
docker logs --tail 100 docker-image-watch
```

## Entwicklung

### Voraussetzungen

- Python 3.12+
- Docker

### Lokale Installation

```bash
# Virtual Environment erstellen
python -m venv venv
source venv/bin/activate

# Abhängigkeiten installieren
pip install -r requirements.txt

# Ausführen
python app/main.py
```

### Image bauen

```bash
docker build -t docker-image-watch:latest .
```

## Lizenz

MIT License
