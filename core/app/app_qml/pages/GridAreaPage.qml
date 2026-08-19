import QtQuick
import QtQuick.Layouts
import QtLocation
import QtPositioning
import Qcm.Material as MD

Item {
    id: root
    required property var host
    property string areaMode: "PA"
    property bool adjustMode: false

    function areaColor(area) {
        if (area === "PA")
            return "#55008080";
        if (area === "IA1")
            return "#55ff8c00";
        if (area === "IA2")
            return "#558a2be2";
        return "#00000000";
    }

    RowLayout {
        anchors.fill: parent
        anchors.margins: 20
        spacing: 16

        Flickable {
            Layout.preferredWidth: Math.min(390, root.width * 0.38)
            Layout.fillHeight: true
            clip: true
            contentHeight: controls.implicitHeight

            ColumnLayout {
                id: controls
                width: parent.width
                spacing: 14

                MD.Text {
                    text: host.tr("step3_title")
                    typescale: MD.Token.typescale.headline_small
                }
                MD.Text {
                    Layout.fillWidth: true
                    text: "CRS: " + (bridge.selectedLocation.latitude !== undefined ? "UTM / " + bridge.selectedLocation.latitude.toFixed(3) : "–")
                    wrapMode: Text.WordWrap
                }

                MD.TextField {
                    id: cellSize
                    Layout.fillWidth: true
                    text: "4500"
                    placeholderText: host.tr("step3_label_cell_size")
                    inputMethodHints: Qt.ImhDigitsOnly
                }
                MD.TextField {
                    id: radius
                    Layout.fillWidth: true
                    text: "30"
                    placeholderText: host.tr("step3_label_radius")
                    inputMethodHints: Qt.ImhFormattedNumbersOnly
                }
                MD.Button {
                    Layout.fillWidth: true
                    text: host.tr("step3_button_generate_grid")
                    icon.name: MD.Token.icon.grid_on
                    onClicked: {
                        let r = parseFloat(radius.text.replace(",", "."));
                        if (r >= 100) r = r / 1000.0;
                        bridge.generateGrid(parseInt(cellSize.text), r);
                    }
                }

                MD.Text {
                    text: host.tr("step3_label_selection_mode")
                    typescale: MD.Token.typescale.title_medium
                }
                RowLayout {
                    Repeater {
                        model: ["PA", "IA1", "IA2"]
                        MD.Button {
                            required property string modelData
                            text: modelData
                            mdState.type: root.areaMode === modelData ? MD.Enum.BtFilledTonal : MD.Enum.BtOutlined
                            onClicked: root.areaMode = modelData
                        }
                    }
                }
                Repeater {
                    model: [
                        {
                            key: "PA",
                            label: "PA"
                        },
                        {
                            key: "IA1",
                            label: "IA1"
                        },
                        {
                            key: "IA2",
                            label: "IA2"
                        }
                    ]
                    RowLayout {
                        required property var modelData
                        Rectangle {
                            width: 16
                            height: 16
                            radius: 4
                            color: root.areaColor(modelData.key)
                        }
                        MD.Text {
                            Layout.fillWidth: true
                            text: modelData.label
                        }
                        MD.Text {
                            text: bridge.selectedCounts[modelData.key] || 0
                        }
                    }
                }
                MD.Button {
                    Layout.fillWidth: true
                    text: host.tr("step2_button_adjust_position")
                    mdState.type: root.adjustMode ? MD.Enum.BtFilledTonal : MD.Enum.BtOutlined
                    onClicked: root.adjustMode = !root.adjustMode
                }
                MD.Button {
                    Layout.fillWidth: true
                    text: host.tr("button_next")
                    enabled: (bridge.selectedCounts.PA || 0) + (bridge.selectedCounts.IA1 || 0) + (bridge.selectedCounts.IA2 || 0) > 0
                    onClicked: bridge.confirmGrid()
                }
            }
        }

        MD.Pane {
            Layout.fillWidth: true
            Layout.fillHeight: true
            padding: 0

            Map {
                id: map
                anchors.fill: parent
                plugin: Plugin {
                    name: "osm"
                }
                center: QtPositioning.coordinate(bridge.selectedLocation.latitude || 51, bridge.selectedLocation.longitude || 10)
                zoomLevel: 10

                MapItemView {
                    model: bridge.gridCells
                    delegate: MapPolygon {
                        required property var modelData
                        path: modelData.path
                        color: root.areaColor(modelData.areaType)
                        border.color: modelData.areaType ? root.areaColor(modelData.areaType) : "#666666"
                        border.width: 1
                    }
                }
                MapQuickItem {
                    coordinate: QtPositioning.coordinate(bridge.selectedLocation.latitude || 0, bridge.selectedLocation.longitude || 0)
                    anchorPoint.x: marker.width / 2
                    anchorPoint.y: marker.height
                    sourceItem: MD.Icon {
                        id: marker
                        name: MD.Token.icon.location_on
                        size: 38
                        color: MD.MProp.color.primary
                    }
                }
                TapHandler {
                    onTapped: function (eventPoint) {
                        const coordinate = map.toCoordinate(eventPoint.position);
                        if (root.adjustMode) {
                            bridge.adjustLocation(coordinate.latitude, coordinate.longitude);
                            root.adjustMode = false;
                        } else {
                            bridge.toggleGridCell(coordinate.longitude, coordinate.latitude, root.areaMode);
                        }
                    }
                }
                Text {
                    anchors.right: parent.right
                    anchors.bottom: parent.bottom
                    anchors.margins: 4
                    text: "<a href=\"https://www.openstreetmap.org/copyright\">© OpenStreetMap-Mitwirkende</a>"
                    font.pixelSize: 11
                    style: Text.Outline
                    styleColor: "#ffffff"
                    linkColor: "#0055aa"
                    onLinkActivated: function(link) { Qt.openUrlExternally(link) }
                }
            }
        }
    }
}
