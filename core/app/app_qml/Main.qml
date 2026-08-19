import QtQuick
import QtQuick.Layouts
import QtQuick.Templates as T
import Qcm.Material as MD
import "pages"

MD.ApplicationWindow {
    id: window
    width: 1280
    height: 820
    minimumWidth: 900
    minimumHeight: 640
    visible: true
    title: "PANDO"

    MD.MProp.textColor: MD.MProp.color.on_surface
    MD.MProp.backgroundColor: MD.MProp.color.surface
    MD.MProp.size.windowClass: MD.Token.window_class.select_type(width)

    function tr(key) {
        const dependency = bridge.language;
        return bridge.text(key);
    }

    Connections {
        target: bridge
        function onDialogRequested(kind, title, message) {
            messageDialog.title = title;
            messageText.text = message;
            messageDialog.open();
        }
    }

    MD.Dialog {
        id: messageDialog
        width: Math.min(520, window.width - 64)
        standardButtons: T.Dialog.Ok
        contentItem: MD.Text {
            id: messageText
            width: messageDialog.availableWidth
            wrapMode: Text.WordWrap
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        MD.LinearIndicator {
            Layout.fillWidth: true
            Layout.preferredHeight: 5
            from: 0
            to: 100
            value: bridge.progressPercent
            indeterminate: false
        }

        MD.Pane {
            Layout.fillWidth: true
            Layout.preferredHeight: 64
            horizontalPadding: 20

            RowLayout {
                anchors.fill: parent
                spacing: 12

                MD.Button {
                    text: window.tr("button_back")
                    enabled: bridge.backEnabled && !bridge.busy
                    mdState.type: MD.Enum.BtText
                    icon.name: MD.Token.icon.arrow_back
                    onClicked: bridge.goBack()
                }

                MD.Text {
                    Layout.fillWidth: true
                    horizontalAlignment: Text.AlignHCenter
                    text: window.tr("wizard_step_indicator").replace("{current}", bridge.currentStep).replace("{total}", bridge.stepCount)
                    typescale: MD.Token.typescale.title_medium
                }

                MD.Button {
                    text: bridge.language.toUpperCase()
                    mdState.type: MD.Enum.BtOutlined
                    onClicked: bridge.switchLanguage()
                }
            }
        }

        Loader {
            id: pageLoader
            Layout.fillWidth: true
            Layout.fillHeight: true
            sourceComponent: {
                switch (bridge.currentStep) {
                case 1:
                    return searchPage;
                case 2:
                    return cityPage;
                case 3:
                    return gridPage;
                case 4:
                    return pbfPage;
                case 5:
                    return processingPage;
                default:
                    return searchPage;
                }
            }
        }

        MD.Pane {
            Layout.fillWidth: true
            Layout.preferredHeight: 46
            horizontalPadding: 20

            RowLayout {
                anchors.fill: parent
                MD.CircularIndicator {
                    Layout.preferredWidth: 24
                    Layout.preferredHeight: 24
                    visible: bridge.busy
                    running: visible
                    indeterminate: true
                }
                MD.Text {
                    Layout.fillWidth: true
                    text: bridge.statusText
                    elide: Text.ElideRight
                    typescale: MD.Token.typescale.body_small
                }
            }
        }
    }

    Component {
        id: searchPage
        SearchPage {
            host: window
        }
    }
    Component {
        id: cityPage
        CitySelectionPage {
            host: window
        }
    }
    Component {
        id: gridPage
        GridAreaPage {
            host: window
        }
    }
    Component {
        id: pbfPage
        ProjectPbfPage {
            host: window
        }
    }
    Component {
        id: processingPage
        ProcessingPage {
            host: window
        }
    }
}
