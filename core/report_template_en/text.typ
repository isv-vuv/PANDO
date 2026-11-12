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

= Project description

This report for #munincipality was generated using #link("https://github.com/isv-vuv/PANDO/")[PANDO], developed by the #link("https://www.isv.uni-stuttgart.de/vuv/")[Chair of Transport Planning and Traffic Engineering]. The software user is solely responsible for its content. The chair accepts no responsibility for the content or accuracy of the results.

The following OpenStreetMap data were used:

#figure(
  table(
    columns: 2,
    table.header([Area], [Download date]),
    ..used_osm_area_with_date.map(
      zeile => zeile.map( wert => [#wert])
    ).flatten(),
  ), caption: [OpenStreetMap data used]
)

The GADM data were obtained from #gadm_source and the population data from #pop_source.

== Planning area, influence areas and external area

#figure(
  table(
    columns: 2,
    align: (left, right),
    table.header([Parameter], [Selected edge length]),
    [Planning area edge length], [#calc.round(cell_size_pa_m, digits: 2) m],
    [Edge length of influence area 1], [#calc.round(cell_size_ia1_m, digits: 2) m],
    [Edge length of influence area 2], [#calc.round(cell_size_ia2_m, digits: 2) m],
  ), caption: [Selected edge lengths of traffic analysis zones]
)

The following figures show the selected planning area, influence areas 1 and 2, and the external area.

#figure(image("pictures/maps/01_planungsraeume.png"), caption: [Selected planning area and influence areas 1 and 2])
#figure(image("pictures/maps/02_aussenraum.png"), caption: [Entire external area with planning area and influence areas 1 and 2])

The following figures show the generated traffic analysis zones:

#figure(image("pictures/maps/03_verkehrszellen_planung_einfluss.png"), caption: [Generated traffic analysis zones in the planning and influence areas])
#figure(image("pictures/maps/04_verkehrszellen_aussenraum.png"), caption: [Generated traffic analysis zones in the external area])

== Selected parameters for identifying centralities

#figure(
  table(
    columns: 2,
    align: (left, right),
    table.header([Parameter], [Value]),
    [Minimum population of metropolitan regions], [#min_population_MR inhabitants],
    [Minimum population of higher-order centres], [#min_population_OZ inhabitants],
    [Minimum population of middle-order centres], [#min_population_MZ inhabitants],
    [Permitted deviation from the minimum population], [#deviation_min_population %],
    [Permitted deviation from the minimum distance], [#deviation_min_distance %],
    [Search radius for dual centres], [#search_radius_DZ km],
    [Permitted deviation from the minimum population for dual centres], [#deviation_min_DZ %],
    [Minimum distance between levels 3 and 4 (intra-municipal)], [#min_distance_3-4 m],
    [Minimum distance between level 3 centralities], [#min_distance_3 m],
    [Minimum distance between level 4 centralities], [#min_distance_4 m],
    [Minimum intensity for classification as level 3], [#min_intensity_3],
    [Minimum intensity for classification as level 4], [#min_intensity_4]
  ), caption: [Selected parameters for identifying central places and intra-municipal centralities]
)

The methodology used to calculate the results is described in @GenerischeVerkehrsnachfragemodelle.

= Population distribution

#figure(image("pictures/maps/05a_einwohner_detail.png"), caption: [Population distribution in the detail view of the three intra-municipal centralities with the highest intensities])
#figure(image("pictures/maps/05_einwohner_planung.png"), caption: [Population distribution in the planning area])
#figure(image("pictures/maps/06_einwohner_planung_einfluss.png"), caption: [Population distribution in the planning and influence areas])

= Central places

#figure(image("pictures/maps/07_zentrale_orte.png"), caption: [Central places throughout the external area])
#figure(image("pictures/maps/07a_intensitaet_detail.png"), caption: [Intra-municipal intensities in the detail view of the three highest-intensity centralities in the planning area])
#figure(image("pictures/maps/08_intensitaet_planung.png"), caption: [Intensity estimates and intra-municipal centralities in the planning area])
#figure(image("pictures/maps/09_intensitaet_planung_einfluss.png"), caption: [Intensity estimates and intra-municipal centralities in the planning and influence areas])

= Network classification

#let visum = report.at("visum", default: (status: "skipped", message: "The Visum import in step 5 was not performed. Visum figures for network classification are omitted.", figures: ()))
#if visum.status == "done" [
  The following maps show direct line connections between central places and the classified road network. The maps and legends were generated using the graphic parameters of the completed Visum model.

  #for graphic in visum.figures [
    #figure(image(graphic.path), caption: [#graphic.caption])
  ]
] else [
  #visum.message
]

= Accessibility

#if visum.status == "done" [
  #for graphic in visum.at("accessibility_figures", default: ()) [
    #figure(image(graphic.path), caption: [#graphic.caption])
  ]
] else [
  #visum.message
]

= Traffic assignment
