# Disclaimer / Haftungsausschluss

_English version below / deutsche Version unten. This is not legal
advice - if you have doubts about your specific use case or
jurisdiction, consult a lawyer._

---

## English

### No warranty

This software is provided "AS IS", without warranty of any kind, in
accordance with Sections 7 and 8 of the [Apache License 2.0](./LICENSE)
that governs it. In particular, there is no warranty of merchantability,
fitness for a particular purpose, or non-infringement, and the
maintainer is not liable for any damages arising from its use, to the
maximum extent permitted by applicable law.

### This is not a certified security/alarm product

Dashboard Stream Cam is a convenience tool for piping a dashboard into
NVR software. It is **not** a certified life-safety, alarm, or security
product, has not undergone any third-party security audit, and must not
be relied upon as the sole means of detecting an emergency, intrusion,
or safety-critical event. If you need certified surveillance or
life-safety equipment, use purpose-built, certified hardware instead.

### Not affiliated with any named brand

This project is **independent, community software** and is **not
affiliated with, endorsed by, sponsored by, or officially connected to**
the Open Home Foundation / Home Assistant, Ubiquiti Inc. (UniFi /
UniFi Protect), ONVIF Inc., Google LLC (Chromium), or any other named
project or trademark holder listed in [NOTICE.md](./NOTICE.md). All
product and company names are used solely to describe interoperability.

### Your responsibility when the dashboard shows real camera footage

This app does not itself contain a camera or capture any real-world
optical footage - it renders whatever Home Assistant dashboard you
choose and re-streams that rendering. **However**, if the dashboard you
choose to stream embeds real camera feeds (e.g. a "picture glance" or
camera card pointing at an actual physical camera), that real footage is
now being carried through this app's stream and will typically be
**recorded** by whatever NVR software (e.g. UniFi Protect) you connect
it to - it becomes a genuine video recording, subject to the same legal
obligations as any other camera recording in your jurisdiction. This
commonly includes (illustrative, not exhaustive, and jurisdiction
-dependent):

- restrictions on filming public streets, sidewalks, or a neighbor's
  private property (in Germany, see the extensive case law under §§ 823,
  1004 BGB and the general personality right / "allgemeines
  Persönlichkeitsrecht"),
- an obligation to inform people who may be recorded (GDPR/DSGVO
  Art. 13, and in Germany also potential Hinweispflichten under state
  data protection law for camera surveillance),
- limits on how long recordings may be retained and who may access them,
- criminal-law limits on filming private/intimate spaces without consent
  (e.g. in Germany, StGB § 201a).

**You, as the operator, are solely responsible for ensuring any camera
footage you route through this app complies with the law that applies
to you.** The maintainer has no visibility into, and no control over,
what you choose to display on your dashboard or how your NVR software
handles it.

### Security is a shared responsibility

See [SECURITY.md](./SECURITY.md) for the specific trade-offs this app
makes (host networking, credential model, disabled Chromium sandbox).
Using this app means accepting those trade-offs on your own network. The
maintainer is not liable for the consequences of misconfiguration,
credential leakage, or exposing this app's ports beyond your intended
network boundary.

### AI-assisted, not independently audited

As disclosed in [AI_POLICY.md](./AI_POLICY.md), this repository's
initial code and documentation were produced with AI assistance and have
not been independently security-audited or tested against a live
UniFi Protect deployment as part of that process. Review the code and
test in your own environment before relying on it, especially for
anything security- or safety-critical.

---

## Deutsch

### Kein Gewährleistungs- oder Garantieanspruch

Diese Software wird "wie besehen" ("AS IS"), ohne jegliche Gewährleistung
bereitgestellt, gemäß den Abschnitten 7 und 8 der
[Apache License 2.0](./LICENSE), unter der sie steht. Es besteht
insbesondere keine Gewähr für Marktgängigkeit, Eignung für einen
bestimmten Zweck oder Freiheit von Rechten Dritter. Der Maintainer haftet
im gesetzlich zulässigen Höchstmaß nicht für Schäden, die aus der
Nutzung der Software entstehen.

### Kein zertifiziertes Sicherheits-/Alarmprodukt

Dashboard Stream Cam ist ein Komfort-Werkzeug, um ein Dashboard in
NVR-Software einzuspeisen. Es handelt sich **nicht** um ein zertifiziertes
Sicherheits-, Alarm- oder Lebensschutzprodukt, es wurde keinem
unabhängigen Sicherheitsaudit unterzogen und darf nicht als alleiniges
Mittel zur Erkennung von Notfällen, Einbrüchen oder
sicherheitskritischen Ereignissen verwendet werden. Für zertifizierte
Überwachungs- oder Lebensschutztechnik sind dafür vorgesehene,
zertifizierte Geräte zu verwenden.

### Keine Verbindung zu genannten Marken

Dieses Projekt ist **unabhängige Community-Software** und steht in
**keiner Verbindung zu, wird nicht unterstützt, gesponsert oder
offiziell bereitgestellt von** der Open Home Foundation / Home Assistant,
der Ubiquiti Inc. (UniFi / UniFi Protect), der ONVIF Inc., der Google LLC
(Chromium) oder anderen in [NOTICE.md](./NOTICE.md) genannten Projekten
oder Markeninhabern. Alle Produkt- und Firmennamen dienen ausschließlich
der Beschreibung der Interoperabilität.

### Deine Verantwortung, wenn das Dashboard echte Kamerabilder zeigt

Diese App enthält selbst keine Kamera und erfasst keine realen optischen
Aufnahmen - sie rendert das von dir gewählte Home-Assistant-Dashboard
und streamt dieses Rendering weiter. **Wenn** das von dir gestreamte
Dashboard jedoch echte Kamerabilder einbindet (z. B. eine
"Bild-Glance"- oder Kamera-Karte, die auf eine tatsächliche physische
Kamera zeigt), wird dieses reale Bildmaterial über den Stream dieser App
geleitet und in der Regel von der angeschlossenen NVR-Software (z. B.
UniFi Protect) **aufgezeichnet** - es entsteht eine echte
Videoaufzeichnung, für die dieselben rechtlichen Pflichten gelten wie
für jede andere Kameraaufzeichnung in deiner Rechtsordnung. Dazu gehören
beispielhaft und nicht abschließend (und abhängig von deiner
Rechtsordnung):

- Einschränkungen bei der Filmung öffentlicher Straßen, Gehwege oder des
  privaten Grundstücks von Nachbarn (in Deutschland u. a. nach §§ 823,
  1004 BGB sowie dem allgemeinen Persönlichkeitsrecht),
- eine Informationspflicht gegenüber möglicherweise aufgezeichneten
  Personen (Art. 13 DSGVO, in Deutschland ggf. zusätzliche
  Hinweispflichten nach landesrechtlichen Datenschutzvorgaben für
  Videoüberwachung),
- Begrenzungen der Speicherdauer von Aufzeichnungen und des
  Zugriffskreises,
- strafrechtliche Grenzen bei der Filmung privater/höchstpersönlicher
  Lebensbereiche ohne Einwilligung (in Deutschland z. B. § 201a StGB).

**Du als Betreiber bist allein dafür verantwortlich sicherzustellen,
dass jegliches Kamerabildmaterial, das du über diese App leitest, den
für dich geltenden gesetzlichen Vorgaben entspricht.** Der Maintainer hat
keinerlei Einblick in und keine Kontrolle darüber, was du auf deinem
Dashboard anzeigst oder wie deine NVR-Software damit umgeht.

### Sicherheit ist eine geteilte Verantwortung

Siehe [SECURITY.md](./SECURITY.md) für die konkreten Abwägungen dieser
App (Host-Networking, Zugangsdaten-Modell, deaktivierte
Chromium-Sandbox). Die Nutzung dieser App bedeutet, diese Abwägungen im
eigenen Netzwerk zu akzeptieren. Der Maintainer haftet nicht für die
Folgen von Fehlkonfiguration, dem Verlust von Zugangsdaten oder einer
über die vorgesehenen Netzwerkgrenzen hinausgehenden Freigabe der Ports
dieser App.

### KI-unterstützt, nicht unabhängig geprüft

Wie in [AI_POLICY.md](./AI_POLICY.md) offengelegt, wurden der
ursprüngliche Code und die Dokumentation dieses Repositories mit
KI-Unterstützung erstellt und im Rahmen dieser Erstellung weder
unabhängig sicherheitsgeprüft noch gegen eine produktive
UniFi-Protect-Umgebung getestet. Prüfe den Code und teste ihn in deiner
eigenen Umgebung, bevor du dich darauf verlässt - besonders bei
sicherheitsrelevanten Aspekten.
