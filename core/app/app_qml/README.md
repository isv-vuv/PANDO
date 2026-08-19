# QML frontend

`MainQml.py` starts the complete, independent QML presentation layer. It uses
the same services in `core/app/app_core` as the QWidget application, but it
does not import widgets, dialogs, map canvases, or step implementations from
`core/app/app_qt`.

## QmlMaterial dependency

The frontend intentionally has no QtQuick Controls fallback. It imports
[`Qcm.Material`](https://github.com/hypengw/QmlMaterial) and therefore needs a
QmlMaterial build made for the exact Qt runtime bundled with QGIS.

QmlMaterial currently requires Qt 6.8 or newer and is installed with CMake.
Build it with a matching Qt development SDK, then point
`QML_MATERIAL_IMPORT_PATH` at the QML import root containing
`Qcm/Material/qmldir`:

```powershell
$env:QML_MATERIAL_IMPORT_PATH = 'C:\path\to\qml-import-root'
& 'C:\Program Files\QGIS 4.2.0\bin\python-qgis.bat' MainQml.py
```

The runner also searches these project-local locations:

- `third_party/QmlMaterial/qml`
- `third_party/QmlMaterial/lib/qml`
- `build/qml-material/qml`
- `build/qml-material/qml_modules`

The binary QGIS distribution is a runtime and may not contain the CMake
package, headers, and C++ compiler needed to build QmlMaterial itself.

## Architecture

- `Main.qml` provides the Material Design 3 application shell.
- `pages/` contains the five QML-only workflow pages.
- `bridge.py` exposes state and commands to QML and imports only `app_core`.
- `workers.py` contains QtCore-only background workers.
- Maps use `QtLocation` and OpenStreetMap tiles; no `QgsMapCanvas` or QWidget
  is embedded.
- `app_core/workflow_state.py` is shared by both frontends. The previous
  `app_qt.app_state` module remains as a compatibility re-export.
