import QtQuick
import QtQuick.Layouts
import QtQuick.Dialogs
import Qcm.Material as MD

Item {
    id: root
    required property var host

    FolderDialog {
        id: workspaceDialog
        title: host.tr("step1_dialog_new_project_workspace_title")
        onAccepted: bridge.createProject(selectedFolder.toString())
    }
    FolderDialog {
        id: projectFolderDialog
        title: host.tr("step1_dialog_open_project_folder_title")
        onAccepted: bridge.openProject(selectedFolder.toString())
    }
    FileDialog {
        id: projectFileDialog
        title: host.tr("step1_dialog_open_project_file_title")
        nameFilters: ["PANDO Config (config.json)", "JSON (*.json)"]
        onAccepted: bridge.openProject(selectedFile.toString())
    }

    ColumnLayout {
        width: Math.min(760, parent.width - 64)
        anchors.centerIn: parent
        spacing: 22

        MD.Text {
            Layout.fillWidth: true
            text: host.tr("step1_title")
            horizontalAlignment: Text.AlignHCenter
            typescale: MD.Token.typescale.headline_large
        }

        MD.Text {
            Layout.fillWidth: true
            visible: bridge.projectPath.length > 0
            text: bridge.projectPath
            horizontalAlignment: Text.AlignHCenter
            elide: Text.ElideMiddle
            typescale: MD.Token.typescale.body_medium
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 12
            MD.TextField {
                id: cityInput
                Layout.fillWidth: true
                placeholderText: host.tr("step1_prompt_city")
                enabled: !bridge.busy
                onAccepted: bridge.searchCity(text)
            }
            MD.Button {
                text: host.tr("button_search")
                icon.name: MD.Token.icon.search
                enabled: cityInput.text.trim().length > 0 && !bridge.busy
                onClicked: bridge.searchCity(cityInput.text)
            }
        }

        RowLayout {
            Layout.alignment: Qt.AlignHCenter
            spacing: 10
            MD.Button {
                text: host.tr("step1_button_open_project_folder")
                mdState.type: MD.Enum.BtOutlined
                onClicked: projectFolderDialog.open()
            }
            MD.Button {
                text: host.tr("step1_button_open_project_file")
                mdState.type: MD.Enum.BtOutlined
                onClicked: projectFileDialog.open()
            }
            MD.Button {
                text: host.tr("step1_button_create_project")
                onClicked: workspaceDialog.open()
            }
        }
    }
}
