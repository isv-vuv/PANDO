"""Step 7: isolated report generation and an embedded, continuous PDF preview."""
from __future__ import annotations
import json
from pathlib import Path
import shutil
import sys
from core.app.app_qt.qt_base import QtCore, QtGui, QtWidgets, Qt, create_step_header, AnimatedProgressBar, pyqtSignal
from core.app.app_core.project import tool_root
from core.app.app_core.centrality_edits import project_session
from core.app.app_core.report_data import report_blockers

_BASE = QtWidgets.QWidget if QtWidgets is not None else object


class Step7ReportWidget(_BASE):
    generation_finished = pyqtSignal() if pyqtSignal else None

    def __init__(self, localizer_obj, parent=None, project_path=''):
        super().__init__(parent)
        self.localizer = localizer_obj
        self.project_path = project_path
        self.pdf_path = Path(project_path) / 'report/report.pdf'
        self.edit_session = project_session(parent, project_path)
        self.process = None
        self.document = None
        self.page = 0
        self.zoom = 1.0
        self._stdout = ''
        self._stderr = ''
        self._error = ''
        self._cancelled = False
        self.cancel_timer = QtCore.QTimer(self)
        self.cancel_timer.setSingleShot(True)
        self.cancel_timer.timeout.connect(self._kill_process)
        layout = QtWidgets.QVBoxLayout(self)
        header, _, _ = create_step_header(self.text('step7_title'), current_step=7, total_steps=7, localizer=self.localizer, parent=self)
        layout.addWidget(header)
        actions = QtWidgets.QHBoxLayout()
        self.generate_button = QtWidgets.QPushButton(self.text('report_generate'))
        self.cancel_button = QtWidgets.QPushButton(self.text('report_cancel'))
        self.save_button = QtWidgets.QPushButton(self.text('report_save'))
        for button in (self.generate_button, self.cancel_button, self.save_button):
            actions.addWidget(button)
        actions.addWidget(QtWidgets.QLabel(self.text('report_language')))
        self.language_group = QtWidgets.QButtonGroup(self)
        self.language_buttons = {}
        current_language = self.localizer.get_current_language()
        for language, label in (('de', 'Deutsch'), ('en', 'English')):
            button = QtWidgets.QPushButton(label)
            button.setCheckable(True)
            button.setChecked(language == ('en' if current_language == 'en' else 'de'))
            self.language_group.addButton(button)
            self.language_buttons[language] = button
            actions.addWidget(button)
        actions.addStretch(); layout.addLayout(actions)
        self.progress = AnimatedProgressBar(self, sync_taskbar=True); layout.addWidget(self.progress)
        self.status = QtWidgets.QLabel(); self.status.setWordWrap(True); layout.addWidget(self.status)
        controls = QtWidgets.QHBoxLayout()
        self.page_label = QtWidgets.QLabel()
        self.zoom_out = QtWidgets.QPushButton('−'); self.zoom_in = QtWidgets.QPushButton('+')
        for widget in (self.page_label, self.zoom_out, self.zoom_in):
            controls.addWidget(widget)
        controls.addStretch(); layout.addLayout(controls)
        self.scroll = QtWidgets.QScrollArea(); self.scroll.setWidgetResizable(True)
        self.pages = QtWidgets.QWidget()
        self.pages_layout = QtWidgets.QVBoxLayout(self.pages)
        self.pages_layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.pages_layout.setSpacing(16)
        self.page_images = []
        self.scroll.setWidget(self.pages); layout.addWidget(self.scroll, 1)
        self.render_timer = QtCore.QTimer(self)
        self.render_timer.setSingleShot(True)
        self.render_timer.timeout.connect(self.render_page)
        self.scroll.viewport().installEventFilter(self)
        self.scroll.verticalScrollBar().valueChanged.connect(lambda: self.render_timer.start(30))
        self.generate_button.clicked.connect(self.generate)
        self.cancel_button.clicked.connect(self.cancel)
        self.save_button.clicked.connect(self.save_pdf)
        self.zoom_out.clicked.connect(lambda: self.change_zoom(1 / 1.25))
        self.zoom_in.clicked.connect(lambda: self.change_zoom(1.25))
        app = QtWidgets.QApplication.instance()
        if app:
            app.aboutToQuit.connect(self.release_resources)
        self._controls(False)
        self._readiness()
        if self.pdf_path.is_file():
            self.load_pdf()

    def eventFilter(self, watched, event):
        if watched is self.scroll.viewport() and event.type() == QtCore.QEvent.Type.Resize:
            self.render_timer.start(0)
        return super().eventFilter(watched, event)

    def text(self, key):
        return self.localizer.get_string(key)

    def _readiness(self):
        blockers = report_blockers(self.project_path)
        if self.edit_session.busy:
            blockers.insert(0, self.text('report_busy'))
        self.generate_button.setEnabled(not blockers)
        self.status.setText(self.text('report_blocked') + '\n' + '\n'.join(blockers) if blockers else self.text('report_ready'))
        return not blockers

    def _controls(self, running):
        for button in self.language_buttons.values():
            button.setEnabled(not running)
        self.generate_button.setEnabled(not running and not self.edit_session.busy and not report_blockers(self.project_path))
        self.cancel_button.setEnabled(running)
        self.save_button.setEnabled(not running and self.pdf_path.is_file())
        self.zoom_out.setEnabled(not running and self.document is not None)
        self.zoom_in.setEnabled(not running and self.document is not None)

    def generate(self):
        if self.process is not None or not self._readiness():
            return
        self._stdout = ''; self._stderr = ''; self._error = ''; self._cancelled = False
        self.progress.setValue(0)
        self.progress.start_animation()
        self.edit_session.busy = True
        self._controls(True)
        process = QtCore.QProcess(self)
        self.process = process
        process.setWorkingDirectory(tool_root())
        environment = QtCore.QProcessEnvironment.systemEnvironment()
        environment.insert('QT_QPA_PLATFORM', 'offscreen')
        environment.insert('PYTHONIOENCODING', 'utf-8')
        process.setProcessEnvironment(environment)
        process.readyReadStandardOutput.connect(self._read_output)
        process.readyReadStandardError.connect(self._read_error)
        process.finished.connect(self._finished)
        process.errorOccurred.connect(self._process_error)
        self.status.setText(self.text('report_progress_prepare'))
        process.start(sys.executable, ['-m', 'core.app.app_core.report_worker', self.project_path, 'en' if self.language_buttons['en'].isChecked() else 'de'])

    def _read_output(self):
        if self.process is None:
            return
        self._stdout += bytes(self.process.readAllStandardOutput()).decode('utf-8', errors='replace')
        while '\n' in self._stdout:
            line, self._stdout = self._stdout.split('\n', 1)
            try:
                message = json.loads(line)
            except (ValueError, TypeError):
                continue
            if message.get('error'):
                self._error = message['message']
            else:
                self.progress.setValue(message.get('percent', 0))
                key = message.get('message', '')
                self.status.setText(self.text(key) if key.startswith('report_') else key)

    def _read_error(self):
        if self.process is not None:
            self._stderr = (self._stderr + bytes(self.process.readAllStandardError()).decode('utf-8', errors='replace'))[-20000:]

    def _process_error(self, error):
        if error == QtCore.QProcess.ProcessError.FailedToStart:
            self._error = self.process.errorString()
            self._finished(-1, QtCore.QProcess.ExitStatus.CrashExit)

    def _finished(self, code, exit_status):
        if self.process is None:
            return
        self.cancel_timer.stop()
        self._read_output(); self._read_error()
        process, self.process = self.process, None
        self.progress.stop_animation()
        self.edit_session.busy = False
        process.deleteLater()
        if self._cancelled:
            self.status.setText(self.text('report_cancelled'))
        elif code != 0 or self._error:
            self.status.setText(self.text('report_failed') + '\n' + (self._error or self._stderr or str(code)))
        else:
            if self.load_pdf():
                self.status.setText(self.text('report_progress_done'))
        self._controls(False)
        self.generation_finished.emit()

    def cancel(self):
        if self.process is not None and not self._cancelled:
            self._cancelled = True
            self.cancel_button.setEnabled(False)
            self.cancel_timer.start(1500)
            self.process.terminate()

    def _kill_process(self):
        if self.process is not None:
            self.process.kill()

    def load_pdf(self):
        self._close_pdf()
        try:
            import pypdfium2 as pdfium
            # Own bytes instead of keeping the output file open on Windows.
            self.document = pdfium.PdfDocument(self.pdf_path.read_bytes())
            self.page = 0
            self._build_pages()
            self.render_page()
            self._controls(False)
            return True
        except Exception as exc:
            self._close_pdf()
            self.status.setText(self.text('report_preview_error') + '\n' + str(exc))
            return False

    def _build_pages(self):
        for label in self.page_images:
            self.pages_layout.removeWidget(label)
            label.deleteLater()
        self.page_images = []
        for index in range(len(self.document)):
            page = self.document[index]
            try:
                width, height = page.get_size()
            finally:
                page.close()
            label = QtWidgets.QLabel()
            label.setFixedSize(int(width * self.zoom * 1.4), int(height * self.zoom * 1.4))
            label.setStyleSheet('background: white;')
            self.pages_layout.addWidget(label, 0, Qt.AlignmentFlag.AlignHCenter)
            self.page_images.append(label)
        self.pages_layout.activate()

    def render_page(self):
        if self.document is None:
            return
        top = self.scroll.verticalScrollBar().value()
        bottom = top + self.scroll.viewport().height()
        visible = []
        for index, label in enumerate(self.page_images):
            y = label.y()
            if y + label.height() < top - 400 or y > bottom + 400:
                label.clear()
                continue
            if y + label.height() > top and y < bottom:
                visible.append(index)
            if label.pixmap() is not None and not label.pixmap().isNull():
                continue
            page = self.document[index]
            bitmap = None
            try:
                bitmap = page.render(scale=self.zoom * 1.4, rev_byteorder=True)
                format_ = QtGui.QImage.Format.Format_RGB888 if bitmap.n_channels == 3 else QtGui.QImage.Format.Format_RGBA8888
                image = QtGui.QImage(bytes(bitmap.buffer), bitmap.width, bitmap.height, bitmap.stride, format_).copy()
                label.setPixmap(QtGui.QPixmap.fromImage(image))
            finally:
                if bitmap is not None:
                    bitmap.close()
                page.close()
        if visible:
            self.page = visible[0]
        self.page_label.setText(f'{self.page + 1} / {len(self.document)}  ·  {self.zoom:.0%}')

    def change_zoom(self, factor):
        if self.document is None:
            return
        bar = self.scroll.verticalScrollBar()
        fraction = bar.value() / max(1, bar.maximum())
        self.zoom = max(0.3, min(3.0, self.zoom * factor))
        self._build_pages()
        self.pages.adjustSize()
        bar.setValue(round(fraction * bar.maximum()))
        self.render_timer.start(0)

    def save_pdf(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, self.text('report_save'), str(Path.home() / (Path(self.project_path).name + '_report.pdf')), 'PDF (*.pdf)')
        if path:
            try:
                if not path.lower().endswith('.pdf'):
                    path += '.pdf'
                if Path(path).resolve() != self.pdf_path.resolve():
                    shutil.copyfile(self.pdf_path, path)
                self.status.setText(self.text('report_saved') + ': ' + path)
            except OSError as exc:
                self.status.setText(self.text('report_failed') + '\n' + str(exc))

    def _close_pdf(self):
        self.render_timer.stop()
        for label in self.page_images:
            self.pages_layout.removeWidget(label)
            label.deleteLater()
        self.page_images = []
        if self.document is not None:
            self.document.close()
            self.document = None

    def release_resources(self):
        process = self.process
        if process is not None:
            self._cancelled = True
            process.kill()
            process.waitForFinished(1500)
        self.progress.stop_animation()
        self._close_pdf()
