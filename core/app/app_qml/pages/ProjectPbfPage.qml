import QtQuick
import QtQuick.Layouts
import QtLocation
import QtPositioning
import Qcm.Material as MD

Item {
    id: root
    required property var host

    function availabilityText(value) {
        if (value === "offline")
            return host.tr("step4_availability_offline");
        if (value === "stale")
            return host.tr("step4_availability_stale");
        if (value === "download")
            return host.tr("step4_availability_download");
        return host.tr("step4_availability_checking");
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 18
        spacing: 12

        RowLayout {
            Layout.fillWidth: true
            MD.Text {
                Layout.fillWidth: true
                text: host.tr("step4_title")
                typescale: MD.Token.typescale.headline_small
            }
            MD.TextField {
                id: searchRadius
                Layout.preferredWidth: 150
                text: "250"
                placeholderText: host.tr("step4_label_pbf_search_radius")
                inputMethodHints: Qt.ImhDigitsOnly
            }
            MD.Button {
                text: host.tr("button_update")
                enabled: !bridge.busy
                onClicked: bridge.searchRegions(parseInt(searchRadius.text))
            }
            MD.Button {
                text: host.tr("step4_button_open_folder")
                mdState.type: MD.Enum.BtOutlined
                enabled: bridge.projectPath.length > 0
                onClicked: Qt.openUrlExternally(bridge.fileUrl(bridge.projectPath))
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 12

            MD.Pane {
                Layout.preferredWidth: root.width * 0.28
                Layout.fillHeight: true
                ColumnLayout {
                    anchors.fill: parent
                    MD.Text {
                        text: host.tr("step4_label_available_regions")
                        typescale: MD.Token.typescale.title_medium
                    }
                    ListView {
                        id: availableList
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        model: bridge.availableRegions
                        delegate: MD.Button {
                            required property var modelData
                            width: availableList.width
                            text: (modelData.depth ? "    " : "") + modelData.name + "  ·  " + bridge.formatBytes(modelData.sizeBytes)
                            mdState.type: MD.Enum.BtText
                            onClicked: bridge.addRegion(modelData.id)
                        }
                    }
                }
            }

            MD.Pane {
                Layout.preferredWidth: root.width * 0.32
                Layout.fillHeight: true
                ColumnLayout {
                    anchors.fill: parent
                    RowLayout {
                        Layout.fillWidth: true
                        MD.Text {
                            Layout.fillWidth: true
                            text: host.tr("step4_label_selection_for_download")
                            typescale: MD.Token.typescale.title_medium
                        }
                        MD.Button {
                            text: host.tr("step4_button_clear_list")
                            mdState.type: MD.Enum.BtText
                            onClicked: bridge.clearRegions()
                        }
                    }
                    MD.Button {
                        Layout.fillWidth: true
                        text: host.tr("step4_button_open_browser_manual_save")
                        mdState.type: MD.Enum.BtText
                        enabled: bridge.downloadUrls.length > 0
                        onClicked: {
                            for (let index = 0; index < bridge.downloadUrls.length; ++index)
                                Qt.openUrlExternally(bridge.downloadUrls[index]);
                        }
                    }
                    ListView {
                        id: selectedList
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        model: bridge.selectedRegions
                        delegate: MD.Pane {
                            required property var modelData
                            width: selectedList.width
                            height: 66
                            RowLayout {
                                anchors.fill: parent
                                MD.Text {
                                    Layout.fillWidth: true
                                    text: modelData.name + "\n" + root.availabilityText(modelData.availability)
                                    wrapMode: Text.WordWrap
                                }
                                MD.Button {
                                    text: "×"
                                    mdState.type: MD.Enum.BtText
                                    onClicked: bridge.removeRegion(modelData.id)
                                }
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
                            value: bridge.downloadProgress
                            indeterminate: false
                        }

                        Rectangle {
                            id: shimmerDownload
                            width: Math.max(80, parent.width * 0.3)
                            height: parent.height
                            anchors.verticalCenter: parent.verticalCenter
                            visible: bridge.busy
                            opacity: 0.6
                            gradient: Gradient {
                                orientation: Gradient.Horizontal
                                GradientStop { position: 0.0; color: "transparent" }
                                GradientStop { position: 0.5; color: "#2196F3" }
                                GradientStop { position: 1.0; color: "transparent" }
                            }
                            NumberAnimation on x {
                                from: -shimmerDownload.width
                                to: shimmerDownload.parent ? shimmerDownload.parent.width : 500
                                duration: 1300
                                loops: Animation.Infinite
                                running: bridge.busy
                            }
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        MD.Button {
                            text: host.tr("step4_button_download_selected")
                            enabled: bridge.selectedRegions.length > 0 && !bridge.busy
                            onClicked: bridge.downloadPbfs()
                        }
                        MD.Button {
                            text: host.tr("button_stop_download")
                            enabled: bridge.busy
                            mdState.type: MD.Enum.BtOutlined
                            onClicked: bridge.stopDownload()
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        MD.Button {
                            text: host.tr("step4_button_check_pbf_manual")
                            mdState.type: MD.Enum.BtOutlined
                            enabled: bridge.selectedRegions.length > 0 && !bridge.busy
                            onClicked: bridge.verifyPbfFiles()
                        }
                        MD.Button {
                            Layout.fillWidth: true
                            text: host.tr("button_next")
                            enabled: bridge.selectedRegions.length > 0 && !bridge.busy
                            onClicked: bridge.confirmPbfSelection()
                        }
                    }
                }
            }

            MD.Pane {
                Layout.fillWidth: true
                Layout.fillHeight: true
                padding: 0
                Map {
                    anchors.fill: parent
                    plugin: Plugin {
                        name: "osm"
                    }
                    center: QtPositioning.coordinate(bridge.selectedLocation.latitude || 51, bridge.selectedLocation.longitude || 10)
                    zoomLevel: 5
                    MapItemView {
                        model: bridge.regionPolygons
                        delegate: MapPolygon {
                            required property var modelData
                            path: modelData.path
                            color: modelData.name === "search-radius" ? "#244f46e5" : "#406755a4"
                            border.color: modelData.name === "search-radius" ? "#4f46e5" : "#6755a4"
                            border.width: 2
                        }
                    }
                }
            }
        }
    }
}
