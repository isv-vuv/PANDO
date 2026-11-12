"""Isolated report generation entry point. Launch with the application's QGIS Python."""
from __future__ import annotations
import json
import os
from pathlib import Path
import shutil
import sys
import traceback


def emit(percent, message, **extra):
    print(json.dumps({'percent': percent, 'message': message, **extra}, ensure_ascii=True), flush=True)


def generate_report(project_path, progress=emit, language='de'):
    from core.app.app_core.project import tool_root
    from core.app.app_core.report_data import build_report_inputs
    if language not in ('de', 'en'):
        raise ValueError('Unsupported report language: ' + str(language))
    root = Path(project_path).resolve()
    data = build_report_inputs(root, language=language)
    report = root / 'report'
    template = Path(tool_root()) / f'core/report_template_{language}'
    progress(1, 'report_progress_prepare')
    report.mkdir(parents=True, exist_ok=True)
    # Retire only the known fonts copied by the old report generator.
    for legacy_font in ('ARIAL.TTF', 'ARIALBD.TTF', 'ARIALBI.TTF', 'ARIALI.TTF'):
        (report / 'fonts' / legacy_font).unlink(missing_ok=True)
    shutil.copytree(template, report, dirs_exist_ok=True)
    (report / 'report_inputs.json').write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    from core.app.app_core.report_maps import export_report_maps
    export_report_maps(root, report / 'pictures/maps', data, progress)
    from core.app.app_core.report_visum import export_visum_maps
    data['visum'] = export_visum_maps(root, report / 'pictures/maps', data, progress)
    (report / 'report_inputs.json').write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    progress(92, 'report_progress_compile')
    import typst
    # Compile to bytes first: a failed or interrupted run cannot truncate the previous PDF.
    pdf = typst.compile(str(report / 'main.typ'), root=str(root),
                        font_paths=[str(report / 'fonts')],
                        sys_inputs={'report': json.dumps(data, ensure_ascii=False)})
    from core.app.app_core.report_data import report_blockers
    errors = report_blockers(root)
    if errors:
        raise ValueError('\n'.join(errors))
    pending = report / 'report.pending.pdf'
    pending.write_bytes(pdf)
    os.replace(pending, report / 'report.pdf')
    progress(100, 'report_progress_done')
    return report / 'report.pdf'


def main():
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    # Import bootstrap before providers, SSL, and other native dependencies.
    from core.app.app_qt.qt_base import QgsApplication, QtCore
    if QgsApplication is None:
        raise RuntimeError('QGIS-Python ist nicht verfügbar.')
    language = sys.argv[2] if len(sys.argv) > 2 else 'de'
    QtCore.QLocale.setDefault(QtCore.QLocale('en_GB' if language == 'en' else 'de_DE'))
    app = QgsApplication([], False)
    app.initQgis()
    try:
        result = generate_report(sys.argv[1], language=language)
        emit(100, 'report_progress_done', pdf=str(result))
        return 0
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        emit(0, str(exc), error=True)
        return 1
    finally:
        app.exitQgis()


if __name__ == '__main__':
    raise SystemExit(main())
