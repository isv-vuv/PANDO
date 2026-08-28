import QtQuick
import QtQuick.Layouts
import QtQuick.Dialogs
import QtQuick.Templates as T
import Qcm.Material as MD

Item {
    id: root
    required property var host
    property var defaults: bridge.model2Defaults
    property var saved: bridge.processingSettings
    property bool noReference: saved.noReference || false

    function parameterMap() {
        return {
            minimum_population_level_0: parseInt(level0.text),
            minimum_population_level_1: parseInt(level1.text),
            minimum_population_level_2: parseInt(level2.text),
            population_tolerance: parseInt(popTolerance.text),
            distance_tolerance: parseInt(distanceTolerance.text),
            dual_centres_search_radius_km: parseInt(dualRadius.text),
            dual_centres_population_tolerance: parseInt(dualTolerance.text)
        };
    }

    FileDialog {
        id: referenceDialog
        nameFilters: ["GeoPackage (*.gpkg)"]
        onAccepted: referencePath.text = selectedFile.toString()
    }
    MD.Dialog {
        id: restartDialog
        title: "Alle Modelle neu berechnen"
        standardButtons: T.Dialog.Yes | T.Dialog.No
        contentItem: MD.Text {
            text: "Sollen Modell 1 bis Modell 6 und OSM Phase C wirklich neu berechnet werden?"
            wrapMode: Text.WordWrap
        }
        onAccepted: bridge.startPipeline(referencePath.text, referenceField.text, censusPath.text, root.noReference, root.parameterMap(), true)
    }

    Component.onCompleted: bridge.startPreprocessing()
    FileDialog {
        id: censusDialog
        nameFilters: ["Raster (*.tif *.tiff)"]
        onAccepted: censusPath.text = selectedFile.toString()
    }

    Flickable {
        anchors.fill: parent
        anchors.margins: 18
        clip: true
        contentHeight: content.implicitHeight

        ColumnLayout {
            id: content
            width: parent.width
            spacing: 14

            MD.Text {
                text: "Parameterprüfung und Verarbeitung"
                typescale: MD.Token.typescale.headline_small
            }

            MD.Pane {
                Layout.fillWidth: true
                ColumnLayout {
                    anchors.fill: parent
                    MD.Text {
                        text: "Automatisch abgeleitete Modelldaten"
                        typescale: MD.Token.typescale.title_medium
                    }
                    MD.Text {
                        text: "Projekt: " + bridge.projectPath
                        elide: Text.ElideMiddle
                    }
                    MD.Text {
                        Layout.fillWidth: true
                        text: bridge.pipelinePhase
                        wrapMode: Text.WordWrap
                    }
                }
            }

            MD.Pane {
                Layout.fillWidth: true
                ColumnLayout {
                    anchors.fill: parent
                    MD.Text {
                        text: "Externe Eingangsdaten"
                        typescale: MD.Token.typescale.title_medium
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        MD.TextField {
                            id: referencePath
                            Layout.fillWidth: true
                            placeholderText: "Referenz-Polygonlayer"
                            text: root.saved.referencePath || ""
                            enabled: !root.noReference
                        }
                        MD.Button {
                            text: "Auswählen"
                            enabled: !root.noReference
                            onClicked: referenceDialog.open()
                        }
                    }
                    MD.TextField {
                        id: referenceField
                        Layout.fillWidth: true
                        text: root.saved.referenceField || "POP"
                        placeholderText: "Einwohnerfeld"
                        enabled: !root.noReference
                    }
                    MD.CheckBox {
                        text: "Keine lokalen Referenzdaten verwenden"
                        checked: root.noReference
                        onToggled: root.noReference = checked
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        MD.TextField {
                            id: censusPath
                            Layout.fillWidth: true
                            placeholderText: "Alternatives Zensusraster (optional)"
                            text: root.saved.censusPath || ""
                        }
                        MD.Button {
                            text: "Auswählen"
                            onClicked: censusDialog.open()
                        }
                    }
                }
            }

            MD.Pane {
                Layout.fillWidth: true
                ColumnLayout {
                    anchors.fill: parent
                    MD.Text {
                        text: "Zentralität – Modell 2"
                        typescale: MD.Token.typescale.title_medium
                    }
                    GridLayout {
                        Layout.fillWidth: true
                        columns: 2
                        columnSpacing: 12
                        rowSpacing: 8
                        MD.Text {
                            text: "Mindestbevölkerung Level 0"
                        }
                        MD.TextField {
                            id: level0
                            text: String((root.saved.parameters || root.defaults).minimum_population_level_0 || 1)
                        }
                        MD.Text {
                            text: "Mindestbevölkerung Level 1"
                        }
                        MD.TextField {
                            id: level1
                            text: String((root.saved.parameters || root.defaults).minimum_population_level_1 || 1)
                        }
                        MD.Text {
                            text: "Mindestbevölkerung Level 2"
                        }
                        MD.TextField {
                            id: level2
                            text: String((root.saved.parameters || root.defaults).minimum_population_level_2 || 1)
                        }
                        MD.Text {
                            text: "Bevölkerungstoleranz (%)"
                        }
                        MD.TextField {
                            id: popTolerance
                            text: String((root.saved.parameters || root.defaults).population_tolerance || 0)
                        }
                        MD.Text {
                            text: "Distanztoleranz (%)"
                        }
                        MD.TextField {
                            id: distanceTolerance
                            text: String((root.saved.parameters || root.defaults).distance_tolerance || 0)
                        }
                        MD.Text {
                            text: "Suchradius Doppelzentren (km)"
                        }
                        MD.TextField {
                            id: dualRadius
                            text: String((root.saved.parameters || root.defaults).dual_centres_search_radius_km || 1)
                        }
                        MD.Text {
                            text: "Toleranz Doppelzentren (%)"
                        }
                        MD.TextField {
                            id: dualTolerance
                            text: String((root.saved.parameters || root.defaults).dual_centres_population_tolerance || 0)
                        }
                    }
                }
            }

            MD.Pane {
                Layout.fillWidth: true
                ColumnLayout {
                    anchors.fill: parent
                    RowLayout {
                        Layout.fillWidth: true
                        MD.Text {
                            Layout.fillWidth: true
                            text: bridge.pipelinePhase
                            typescale: MD.Token.typescale.title_medium
                        }
                        MD.Text {
                            text: bridge.elapsedText
                        }
                    }
                    Item {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 6
                        clip: true

                        MD.LinearIndicator {
                            anchors.fill: parent
                            from: 0
                            to: 100
                            value: bridge.pipelineProgress
                            indeterminate: false
                        }

                        Rectangle {
                            id: shimmerOverall
                            width: Math.max(80, parent.width * 0.3)
                            height: parent.height
                            anchors.verticalCenter: parent.verticalCenter
                            visible: bridge.pipelineRunning
                            opacity: 0.6
                            gradient: Gradient {
                                orientation: Gradient.Horizontal
                                GradientStop { position: 0.0; color: "transparent" }
                                GradientStop { position: 0.5; color: "#2196F3" }
                                GradientStop { position: 1.0; color: "transparent" }
                            }
                            NumberAnimation on x {
                                from: -shimmerOverall.width
                                to: shimmerOverall.parent ? shimmerOverall.parent.width : 500
                                duration: 1400
                                loops: Animation.Infinite
                                running: bridge.pipelineRunning
                            }
                        }
                    }

                    Item {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 6
                        clip: true

                        MD.LinearIndicator {
                            anchors.fill: parent
                            from: 0
                            to: 100
                            value: bridge.pipelinePhaseProgress
                            indeterminate: false
                        }

                        Rectangle {
                            id: shimmerPhase
                            width: Math.max(80, parent.width * 0.3)
                            height: parent.height
                            anchors.verticalCenter: parent.verticalCenter
                            visible: bridge.pipelineRunning
                            opacity: 0.6
                            gradient: Gradient {
                                orientation: Gradient.Horizontal
                                GradientStop { position: 0.0; color: "transparent" }
                                GradientStop { position: 0.5; color: "#2196F3" }
                                GradientStop { position: 1.0; color: "transparent" }
                            }
                            NumberAnimation on x {
                                from: -shimmerPhase.width
                                to: shimmerPhase.parent ? shimmerPhase.parent.width : 500
                                duration: 1200
                                loops: Animation.Infinite
                                running: bridge.pipelineRunning
                            }
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        MD.Button {
                            text: "Alle Modelle neu berechnen"
                            mdState.type: MD.Enum.BtOutlined
                            enabled: !bridge.pipelineRunning
                            onClicked: restartDialog.open()
                        }
                        MD.Button {
                            Layout.fillWidth: true
                            text: "Fortsetzen / starten"
                            enabled: !bridge.pipelineRunning
                            onClicked: bridge.startPipeline(referencePath.text, referenceField.text, censusPath.text, root.noReference, root.parameterMap(), false)
                        }
                        MD.Button {
                            text: "Stop"
                            enabled: bridge.pipelineRunning
                            onClicked: bridge.stopPipeline()
                        }
                    }
                }
            }

            MD.Pane {
                Layout.fillWidth: true
                Layout.preferredHeight: 280
                ColumnLayout {
                    anchors.fill: parent
                    MD.Text {
                        text: "Log"
                        typescale: MD.Token.typescale.title_medium
                    }
                    Flickable {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        contentHeight: logText.implicitHeight
                        contentWidth: width
                        MD.Text {
                            id: logText
                            width: parent.width
                            text: bridge.pipelineLog
                            wrapMode: Text.WrapAnywhere
                            typescale: MD.Token.typescale.body_small
                        }
                    }
                }
            }
        }
    }
}
