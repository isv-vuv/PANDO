#let report = json(bytes(sys.inputs.report))
#let munincipality = report.at("munincipality")
#let used_osm_area_with_date = report.at("used_osm_area_with_date")
#let gadm_source = report.at("gadm_source")
#let pop_source = report.at("pop_source")
#let min_population_MR = report.at("min_population_MR")
#let min_population_OZ = report.at("min_population_OZ")
#let min_population_MZ = report.at("min_population_MZ")
#let deviation_min_population = report.at("deviation_min_population")
#let deviation_min_distance = report.at("deviation_min_distance")
#let search_radius_DZ = report.at("search_radius_DZ")
#let deviation_min_DZ = report.at("deviation_min_DZ")
#let min_distance_3-4 = report.at("min_distance_3-4")
#let min_distance_3 = report.at("min_distance_3")
#let min_distance_4 = report.at("min_distance_4")
#let min_intensity_3 = report.at("min_intensity_3")
#let min_intensity_4 = report.at("min_intensity_4")
#let cell_size_pa_m = report.at("cell_size_pa_m")
#let cell_size_ia1_m = report.at("cell_size_ia1_m")
#let cell_size_ia2_m = report.at("cell_size_ia2_m")

= Projektbeschreibung

Dieser Bericht für den Ort #munincipality wurde mit der Software #link("https://github.com/isv-vuv/PANDO/")[PANDO] des #link("https://www.isv.uni-stuttgart.de/vuv/")[Lehrstuhls für Verkehrsplanung und Verkehrsleittechnik] erstellt. Für den Inhalt ist ausschließlich der Anwender der Software verantwortlich, der Lehrstuhl übernimmt keine Verantwortung für Inhalt und Korrektheit der Ergebnisse.

Dabei wurden die folgenden OpenStreetMap Daten verwendet:

#figure(
  table(
    columns: 2,
    table.header([Gebiet], [Datum heruntergeladen]),
    ..used_osm_area_with_date.map(
      zeile => zeile.map( wert => [#wert])
    ).flatten(),
  ), caption: [Verwendete OpenStreetMap Daten]
)

Die verwendeten GADM-Daten sind von #gadm_source und die verwendeten Einwohnerdaten von #pop_source.

== Planungsraum, Einflussraum und Außenraum

#figure(
  table(
    columns: 2,
    align: (left, right),
    table.header([Parameter], [Gewählte Kantenlänge]),
    [Kantenlänge Planungsraum], [#calc.round(cell_size_pa_m, digits: 2) m],
    [Kantenlänge Einflussraum 1], [#calc.round(cell_size_ia1_m, digits: 2) m],
    [Kantenlänge Einflussraum 2], [#calc.round(cell_size_ia2_m, digits: 2) m],
  ), caption: [Gewählte Kantenlängen der Verkehrszellen]
)

In den folgenden Grafiken sind der gewählte Planungsraum, Einflussraum 1 & 2 sowie der Außenraum dargestellt.

#figure(image("pictures/maps/01_planungsraeume.png"), caption: [Gewählter Planungsraum und Einflussräume 1 und 2])
#figure(image("pictures/maps/02_aussenraum.png"), caption: [Vollständiger Außenraum mit Planungsraum und Einflussräumen 1 und 2])

In der folgenden Grafik sind die generierten Verkehrszellen dargestellt:

#figure(image("pictures/maps/03_verkehrszellen_planung_einfluss.png"), caption: [Generierte Verkehrszellen im Planungs- und Einflussraum])
#figure(image("pictures/maps/04_verkehrszellen_aussenraum.png"), caption: [Generierte Verkehrszellen im Außenraum])

== Gewählte Parameter zur Bestimmung der Zentralitäten

#figure(
  table(
    columns: 2,
    align: (left, right),
    table.header([Parameter], [Wert]),
    [Mindestbevölkerung Metropolregion], [#min_population_MR EW],
    [Mindestbevölkerung Oberzentrum], [#min_population_OZ EW],
    [Mindestbevölkerung Mittelzentrum], [#min_population_MZ EW],
    [Zulässige Abweichung der Mindestbevölkerungszahl], [#deviation_min_population %],
    [Zulässige Abweichung der Mindestdistanz], [#deviation_min_distance %],
    [Suchradius Doppelzentren], [#search_radius_DZ km],
    [Zulässige Abweichung der Mindestbevölkerungszahl bei Doppelzentren], [#deviation_min_DZ %],
    [Mindestabstand zwischen Stufe 3 und 4 (innergemeindlich)], [#min_distance_3-4 m],
    [Mindestabstand zwischen Stufe 3], [#min_distance_3 m],
    [Mindestabstand zwischen Stufe 4], [#min_distance_4 m],
    [Mindest-Intensität für die Klassifizierung als Stufe 3], [#min_intensity_3],
    [Mindest-Intensität für die Klassifizierung als Stufe 4], [#min_intensity_4]
  ), caption: [Gewählte Parameter für die Bestimmung von Zentralen Orten und innergemeindlichen Zentralitäten]
)

Das Vorgehen zur Berechnung der Ergebnisse ist in @GenerischeVerkehrsnachfragemodelle beschrieben.

= Einwohnerverteilung

#figure(image("pictures/maps/05a_einwohner_detail.png"), caption: [Einwohnerverteilung im Detailausschnitt der drei intensitätsstärksten innerörtlichen Zentralitäten])
#figure(image("pictures/maps/05_einwohner_planung.png"), caption: [Einwohnerverteilung im Planungsraum])
#figure(image("pictures/maps/06_einwohner_planung_einfluss.png"), caption: [Einwohnerverteilung im Planungs- und Einflussraum])

= Zentrale Orte

#figure(image("pictures/maps/07_zentrale_orte.png"), caption: [Zentrale Orte im gesamten Außenraum])
#figure(image("pictures/maps/07a_intensitaet_detail.png"), caption: [Innerörtliche Intensitäten im Ausschnitt der drei intensitätsstärksten Zentralitäten im Planungsraum])
#figure(image("pictures/maps/08_intensitaet_planung.png"), caption: [Intensitätsschätzung und innergemeindliche Zentralitäten im Planungsraum])
#figure(image("pictures/maps/09_intensitaet_planung_einfluss.png"), caption: [Intensitätsschätzung und innergemeindliche Zentralitäten im Planungs- und Einflussraum])

= Netzkategorisierung

#let visum = report.at("visum", default: (status: "skipped", message: "Der Visum-Import in Schritt 5 wurde nicht durchgeführt. Die Visum-Grafiken zur Netzkategorisierung werden übersprungen.", figures: ()))
#if visum.status == "done" [
  Die folgenden Karten zeigen die Luftlinienverbindungen zwischen den zentralen Orten sowie das kategorisierte Straßennetz. Die Darstellungen und Legenden wurden mit den Grafikparametern des fertigen Visum-Modells erstellt.

  #for graphic in visum.figures [
    #figure(image(graphic.path), caption: [#graphic.caption])
  ]
] else [
  #visum.message
]

= Erreichbarkeit

#if visum.status == "done" [
  #for graphic in visum.at("accessibility_figures", default: ()) [
    #figure(image(graphic.path), caption: [#graphic.caption])
  ]
] else [
  #visum.message
]

= Umlegung
