import QtQuick
import QtQuick.Layouts
import QtLocation
import QtPositioning
import Qcm.Material as MD

Item {
    id: root
    required property var host
    property int selectedIndex: -1
    property bool adjustMode: false

    RowLayout {
        anchors.fill: parent
        anchors.margins: 20
        spacing: 16

        ColumnLayout {
            Layout.preferredWidth: Math.min(420, root.width * 0.4)
            Layout.fillHeight: true
            spacing: 12

            MD.Text {
                text: host.tr("step2_title")
                typescale: MD.Token.typescale.headline_small
            }
            MD.Text {
                text: host.tr("step2_label_found_places")
            }

            ListView {
                id: resultList
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                spacing: 8
                model: bridge.geocodeResults
                delegate: MD.Pane {
                    required property var modelData
                    width: resultList.width
                    height: addressText.implicitHeight + 28
                    MD.MProp.backgroundColor: root.selectedIndex === modelData.index ? MD.MProp.color.secondary_container : MD.MProp.color.surface_container
                    MD.Text {
                        id: addressText
                        anchors.fill: parent
                        anchors.margins: 14
                        text: modelData.address
                        wrapMode: Text.WordWrap
                    }
                    TapHandler {
                        onTapped: {
                            root.selectedIndex = modelData.index;
                            bridge.selectLocation(modelData.index);
                        }
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                MD.Button {
                    text: host.tr("step2_button_adjust_position")
                    mdState.type: root.adjustMode ? MD.Enum.BtFilledTonal : MD.Enum.BtOutlined
                    onClicked: root.adjustMode = !root.adjustMode
                }
                MD.Button {
                    text: host.tr("step2_button_check_osm")
                    mdState.type: MD.Enum.BtOutlined
                    enabled: bridge.selectedLocation.latitude !== undefined
                    onClicked: Qt.openUrlExternally("https://www.openstreetmap.org/?mlat=" + bridge.selectedLocation.latitude + "&mlon=" + bridge.selectedLocation.longitude + "#map=13/" + bridge.selectedLocation.latitude + "/" + bridge.selectedLocation.longitude)
                }
                MD.Button {
                    Layout.fillWidth: true
                    text: host.tr("step2_button_confirm_selection")
                    enabled: bridge.selectedLocation.latitude !== undefined
                    onClicked: bridge.confirmLocation()
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
                center: bridge.selectedLocation.latitude !== undefined ? QtPositioning.coordinate(bridge.selectedLocation.latitude, bridge.selectedLocation.longitude) : QtPositioning.coordinate(51, 10)
                zoomLevel: bridge.selectedLocation.latitude !== undefined ? 11 : 5

                MapQuickItem {
                    visible: bridge.selectedLocation.latitude !== undefined
                    coordinate: QtPositioning.coordinate(bridge.selectedLocation.latitude || 0, bridge.selectedLocation.longitude || 0)
                    anchorPoint.x: marker.width / 2
                    anchorPoint.y: marker.height
                    sourceItem: MD.Icon {
                        id: marker
                        name: MD.Token.icon.location_on
                        size: 42
                        color: MD.MProp.color.primary
                    }
                }

                TapHandler {
                    enabled: root.adjustMode
                    onTapped: function (eventPoint) {
                        const coordinate = map.toCoordinate(eventPoint.position);
                        bridge.adjustLocation(coordinate.latitude, coordinate.longitude);
                        root.adjustMode = false;
                    }
                }
            }
        }
    }
}
