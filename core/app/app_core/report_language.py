"""Translations for generated report graphics, independent of the UI language."""

ENGLISH = {
    'Erreichbarkeitsanalyse im Planungsraum': 'Accessibility analysis in the planning area',
    'Erreichbarkeitsanalyse im Planungs- und Einflussraum': 'Accessibility analysis in the planning and influence areas',
    'Planungs- und Einflussräume': 'Planning and influence areas',
    'Planungsraum': 'Planning area',
    'Einflussraum 1': 'Influence area 1',
    'Einflussraum 2': 'Influence area 2',
    'Verkehrszellen': 'Traffic analysis zones',
    'Einwohner je Rasterzelle': 'Population per raster cell',
    'Flächenklassen': 'Area classes',
    'Zentrale Orte': 'Central places',
    'Intensität (Modell 5)': 'Intensity (model 5)',
    'Zentralitäten': 'Centralities',
    'Außenraum': 'External area',
    'Gebietsgrenze': 'Area boundary',
    'Legende': 'Legend',
    'Metropole': 'Metropolis',
    'Oberzentrum': 'Higher-order centre',
    'Mittelzentrum': 'Middle-order centre',
    'Grundzentrum': 'Basic centre',
    'Gemeinde': 'Municipality',
    'unbewohnt': 'uninhabited',
    'Einw./Rasterzelle': 'inhabitants/raster cell',
    'Hintergrundkarte nicht verfügbar; neutraler Hintergrund.': 'Basemap unavailable; neutral background.',
    'Hintergrund: © OpenStreetMap-Mitwirkende': 'Basemap: © OpenStreetMap contributors',
    'Fachdaten:': 'Thematic data:',
    'Einwohnerwerte:': 'Population values:',
    'Luftlinienverbindungen im Außenraum (Stufen 0 und I)': 'Direct line connections in the external area (levels 0 and I)',
    'Luftlinienverbindungen im Planungs- und Einflussraum (Stufen I, II und III)': 'Direct line connections in the planning and influence areas (levels I, II and III)',
    'Kategorisiertes Straßennetz im Außenraum': 'Classified road network in the external area',
    'Kategorisiertes Straßennetz im Planungs- und Einflussraum': 'Classified road network in the planning and influence areas',
    'Kategorisiertes Straßennetz im Planungsraum': 'Classified road network in the planning area',
    'Kategorisiertes Straßennetz im Detailausschnitt der drei intensitätsstärksten innerörtlichen Zentralitäten': 'Classified road network in the detail view of the three intra-municipal centralities with the highest intensities',
    'Der Visum-Import in Schritt 5 wurde nicht durchgeführt oder nicht vollständig abgeschlossen. Die Visum-Grafiken zur Netzkategorisierung werden deshalb übersprungen.': 'The Visum import in step 5 was not performed or did not complete. Visum figures for network classification are therefore omitted.',
    'Ein fertiges Visum-Modell ist vorhanden. Der Export der Visum-Grafiken benötigt Windows mit einer installierten und lizenzierten PTV-Visum-Version.': 'A completed Visum model is available. Exporting Visum figures requires Windows with an installed and licensed version of PTV Visum.',
}


def report_text(text, language):
    if language != 'en':
        return text
    # Long phrases first, so shorter terms do not partially consume a translation.
    for german in sorted(ENGLISH, key=len, reverse=True):
        text = text.replace(german, ENGLISH[german])
    return text
