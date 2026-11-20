# PANDO – ToDo & Bugfix-Liste

Sammlung bekannter Fehler, Sonderfälle und offener Verbesserungen zur späteren Behebung.

---

## 1. Doppelte Zentralitäten bei durch Gitterzellen überdeckten Alt-Bezirken

- **Symptom:** Dort, wo ein bestehender (größerer) Bezirk nahezu vollständig durch feine Gitterzellen überdeckt wird, wird die Zentralität des Alt-Bezirks teilweise nicht herabgestuft, obwohl eine Gitterzelle in unmittelbarer Nähe auf dieselbe Zentralität gesetzt wird. Da die Zentroid-Koordinaten des Alt-Bezirks an dieser Stelle verbleiben, erscheinen zwei Zentren derselben Stufe direkt übereinander/nebeneinander.
- **Vermutete Ursache:** Fehler beim Verschieben/Übertragen der Zentralität — vermutlich ein Sonderfall, wenn an der Zielstelle der neu zu platzierenden Zentralität keine Einwohner (`POP == 0`) vorhanden sind.
- **Zu prüfen:**
  - Zentralitäts-Übertragung / Herabstufung in den QGIS-Modellen bzw. `Model4_TierAssign` / `Model5_UrbanCentrality` / `Model6_ZoneAssembler` sowie in `core/app/app_core/centrality_update.py`.
  - Sicherstellen, dass Alt-Bezirke bei Übertragung ihrer Zentralität auf eine Gitterzelle zuverlässig herabgestuft werden und der `POP == 0`-Sonderfall sauber behandelt wird.
