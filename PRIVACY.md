# Privacy Policy / Datenschutzerklärung

_English version below / deutsche Version unten._

---

## English

### Summary

Dashboard Stream Cam is self-hosted software that runs entirely inside
your own Home Assistant installation, on your own hardware. It has **no
telemetry, no analytics, no crash reporting, no update-check phone-home,
and no third-party backend of any kind**. The maintainer receives no
data from your running instance, period.

### What data the app handles, and where it goes

| Data | What happens to it |
| --- | --- |
| Your Home Assistant URL, dashboard choice, ONVIF device name, log level, etc. (app options) | Stored only in Supervisor's local options store (`/data/options.json`) on your own host. Never transmitted anywhere except used locally to configure the app's own processes. |
| Stream username/password | Stored the same way; used only to authenticate RTSP/ONVIF/snapshot requests handled locally by this app. |
| Optional Home Assistant Long-Lived Access Token | Stored the same way; sent only to the `ha_url` you configured (your own Home Assistant), and written into the headless browser's local storage inside this app's own container - nowhere else. |
| The rendered dashboard's contents (whatever entities/cards you chose to show) | Captured as video frames and re-encoded locally by ffmpeg; served only to RTSP/ONVIF/snapshot clients that authenticate with your configured credentials, on your own network. Not stored beyond a rolling snapshot JPEG (`/data/snapshot.jpg`, overwritten roughly every 10 seconds) and whatever your RTSP/NVR client (e.g. UniFi Protect) chooses to record on its own. |
| A randomly generated device identifier (`/data/device_uuid`) | Stored locally, used only to give the ONVIF device a stable identity across restarts. Not derived from and does not contain any personal or hardware-identifying information. |
| Build-time network access (fetching the base image, Alpine packages, the `mediamtx` binary, and Python dependencies from their respective upstream sources) | Happens once, when Supervisor builds the container image from this repository. Involves no data about you or your Home Assistant instance - it is a software build step, not a runtime call. |

No data is sent to the repository maintainer, to Anthropic (the AI
provider used to help write this repository - see
[AI_POLICY.md](./AI_POLICY.md)), or to any analytics/telemetry service,
at any point.

### Who is the data controller

If the dashboard you choose to stream displays personal data (e.g.
location trackers, presence, health/sensor data, or camera feeds
embedded via cards), **you, the operator of this Home Assistant
instance, are the sole controller of that data** for purposes of the EU
General Data Protection Regulation (GDPR/DSGVO) or any equivalent local
law - not the maintainer of this software. This is self-hosted software
that runs on infrastructure you control; the maintainer has no access to
your instance, your data, or your network. You are responsible for:

- deciding what is safe/appropriate to put on the streamed dashboard,
- who you give the stream credentials to,
- how long your NVR/recording software (e.g. UniFi Protect) retains any
  recording it makes of the stream, and complying with applicable law for
  that retention (see [DISCLAIMER.md](./DISCLAIMER.md) for more on this,
  especially if the dashboard includes real camera feeds).

### Cookies / local storage

The ingress web panel (dashboard picker) sets no cookies and uses no
tracking of any kind. The headless Chromium instance stores Home
Assistant's own frontend session data (if you use the Long-Lived Access
Token login method) in a browser profile inside the app's own container
(`/data/chromium-profile`) - this is local browser storage, not a
tracking mechanism, and is never read by anything outside the app.

### Changes

Material changes to this policy will be noted in
[CHANGELOG.md](./CHANGELOG.md).

### Contact

Questions about this policy: **marcel-hoess@live.de**.

---

## Deutsch

### Zusammenfassung

Dashboard Stream Cam ist selbst gehostete Software, die vollständig
innerhalb deiner eigenen Home-Assistant-Installation auf deiner eigenen
Hardware läuft. Es gibt **keine Telemetrie, keine Analyse-/Tracking-
Funktionen, keine Absturzberichte, keine "Nach-Hause-Telefonieren"-
Update-Prüfungen und kein Drittanbieter-Backend jeglicher Art**. Der
Maintainer erhält zu keinem Zeitpunkt Daten aus deiner laufenden
Instanz.

### Welche Daten die App verarbeitet und was damit passiert

| Daten | Was damit geschieht |
| --- | --- |
| Deine Home-Assistant-URL, Dashboard-Auswahl, ONVIF-Gerätename, Log-Level usw. (App-Optionen) | Werden ausschließlich lokal im Options-Speicher des Supervisors (`/data/options.json`) auf deinem eigenen Host gespeichert. Keine Übertragung nach außen - Verwendung nur lokal zur Konfiguration der App-eigenen Prozesse. |
| Stream-Benutzername/-Passwort | Speicherung genauso; Verwendung ausschließlich zur lokalen Authentifizierung von RTSP-/ONVIF-/Snapshot-Anfragen, die von dieser App selbst verarbeitet werden. |
| Optionales Home-Assistant-Long-Lived-Access-Token | Speicherung genauso; wird ausschließlich an die von dir konfigurierte `ha_url` (deine eigene Home-Assistant-Instanz) gesendet und im lokalen Speicher des Headless-Browsers innerhalb des eigenen Containers dieser App abgelegt - sonst nirgendwohin. |
| Inhalt des gerenderten Dashboards (die von dir gewählten Entitäten/Karten) | Wird lokal als Videobild von ffmpeg erfasst und neu kodiert; wird ausschließlich an RTSP-/ONVIF-/Snapshot-Clients ausgeliefert, die sich mit deinen konfigurierten Zugangsdaten in deinem eigenen Netzwerk authentifizieren. Es erfolgt keine Speicherung über ein rollierendes Snapshot-JPEG hinaus (`/data/snapshot.jpg`, wird ca. alle 10 Sekunden überschrieben) sowie das, was dein RTSP-/NVR-Client (z. B. UniFi Protect) eigenständig aufzeichnet. |
| Eine zufällig erzeugte Geräte-ID (`/data/device_uuid`) | Lokale Speicherung, dient nur dazu, dem ONVIF-Gerät über Neustarts hinweg eine stabile Identität zu geben. Enthält keine personenbezogenen oder hardwarebezogenen Informationen und wird aus keinen solchen abgeleitet. |
| Netzwerkzugriffe zur Build-Zeit (Laden des Basis-Images, der Alpine-Pakete, der `mediamtx`-Binärdatei und der Python-Abhängigkeiten aus ihren jeweiligen Upstream-Quellen) | Erfolgt einmalig beim Bauen des Container-Images durch den Supervisor aus diesem Repository. Enthält keinerlei Daten über dich oder deine Home-Assistant-Instanz - es handelt sich um einen Software-Build-Schritt, keinen Laufzeitaufruf. |

Es werden zu keinem Zeitpunkt Daten an den Repository-Maintainer, an
Anthropic (den KI-Anbieter, der beim Erstellen dieses Repositories
unterstützt hat - siehe [AI_POLICY.md](./AI_POLICY.md)) oder an einen
Analyse-/Telemetriedienst übermittelt.

### Wer ist verantwortliche Stelle im Sinne der DSGVO

Wenn das von dir gestreamte Dashboard personenbezogene Daten anzeigt
(z. B. Standort-Tracker, Anwesenheits-, Gesundheits-/Sensordaten oder
über Karten eingebettete Kamerabilder), **bist du als Betreiber dieser
Home-Assistant-Instanz die alleinige verantwortliche Stelle** im Sinne
der Datenschutz-Grundverordnung (DSGVO) oder vergleichbarer lokaler
Gesetze - nicht der Maintainer dieser Software. Es handelt sich um
selbst gehostete Software, die auf von dir kontrollierter Infrastruktur
läuft; der Maintainer hat keinerlei Zugriff auf deine Instanz, deine
Daten oder dein Netzwerk. In deiner Verantwortung liegen insbesondere:

- die Entscheidung, was auf dem gestreamten Dashboard angezeigt werden
  darf,
- an wen du die Stream-Zugangsdaten weitergibst,
- wie lange deine NVR-/Aufzeichnungssoftware (z. B. UniFi Protect)
  Aufzeichnungen des Streams speichert, und die Einhaltung der dafür
  geltenden Gesetze (siehe [DISCLAIMER.md](./DISCLAIMER.md), insbesondere
  wenn das Dashboard echte Kamerabilder enthält).

### Cookies / lokaler Speicher

Das Ingress-Webpanel (Dashboard-Auswahl) setzt keine Cookies und
verwendet keinerlei Tracking. Die Headless-Chromium-Instanz speichert
die Frontend-Sitzungsdaten von Home Assistant (sofern du die
Long-Lived-Access-Token-Anmeldung nutzt) in einem Browser-Profil
innerhalb des App-eigenen Containers (`/data/chromium-profile`) - dies
ist lokaler Browser-Speicher, kein Tracking-Mechanismus, und wird von
nichts außerhalb der App gelesen.

### Änderungen

Wesentliche Änderungen dieser Erklärung werden im
[CHANGELOG.md](./CHANGELOG.md) vermerkt.

### Kontakt

Fragen zu dieser Erklärung: **marcel-hoess@live.de**.
