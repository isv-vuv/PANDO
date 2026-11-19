## @package dlnt_GUI
# @brief Graphical User Interface for the Direct-Line Network Tool
#
# This class provides a graphical interface for the Direct Line Network Tool (DLNT),
# allowing users to interactively:
# - Load and zone data from Visum model
# - Set calculation parameters
# - Execute network calculations
# - Export results
#
# The GUI consists of:
# - Main window with toolbar and status bar
# - Configuration panels for input parameters
# - Progress indicators for long-running calculations
# - Result visualization area
# - Export options dialog
#
# Usage example:
# @code
# app = wx.App()
# frame = dlnt_GUI(None, title="Direct Line Network Tool")
# frame.Show()
# app.MainLoop()
# @endcode
#
# @author MaS
# @date 2022
#
# @note This GUI is implemented using wxPython framework.
# see DirectLineNetworkCalculator for the underlying calculation.
# Not all methods of the calculator class are available in the GUI.


import importlib
import sys
from pathlib import Path


def _resolve_pando_visum_dirs() -> tuple[Path | None, Path | None]:
    """Locate PANDO's core/scripts/visum/{scripts,helper_files} from either core or a project's processed/visum/script."""
    here = Path(__file__).resolve().parent
    # Case 1: running directly inside core/scripts/visum/scripts
    if (here / "05_cfl_directlinenetwork_tool.py").is_file():
        return here, here.parent / "helper_files"
    # Case 2: running from <PANDO_ROOT>/projects/<project>/processed/visum/script
    for ancestor in here.parents:
        cand_scripts = ancestor / "core" / "scripts" / "visum" / "scripts"
        if (cand_scripts / "05_cfl_directlinenetwork_tool.py").is_file():
            return cand_scripts, ancestor / "core" / "scripts" / "visum" / "helper_files"
    return None, None


_SCRIPT_DIR = Path(__file__).resolve().parent
_PANDO_SCRIPTS_DIR, _PANDO_HELPER_DIR = _resolve_pando_visum_dirs()
for _p in (_SCRIPT_DIR, _PANDO_SCRIPTS_DIR, _PANDO_HELPER_DIR):
    if _p is not None and _p.is_dir() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import wx
import wx.html2
import logging
import json
import os
import warnings

try:
    dlnt = importlib.import_module("05_cfl_directlinenetwork_tool")
except ImportError:
    import cfl_directlinenetwork_tool as dlnt

try:
    Translator = importlib.import_module("05_language_management").Translator
except ImportError:
    from language_management import Translator


def _configure_file_logging(visum=None) -> Path:
    """Route all logging strictly to processed/visum/log/dlnt_GUI.log.

    Removing StreamHandlers prevents Visum's embedded Python from intercepting
    sys.stderr writes as modal Warning popups or deadlocking the wx UI loop.
    """
    log_name = Path(__file__).name.replace(".py", ".log")
    script_parent = Path(__file__).resolve().parent
    log_dir = script_parent.parent / "log"
    if script_parent.name.lower() != "script" and visum is not None:
        try:
            doc_name = str(visum.UserPreferences.DocumentName or "").strip()
            if doc_name:
                doc_parent = Path(doc_name).resolve().parent
                visum_root = doc_parent.parent if doc_parent.name.lower() == "ver" else doc_parent
                log_dir = visum_root / "log"
        except Exception:
            pass
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        path_logfile = log_dir / log_name
    except OSError:
        path_logfile = Path(log_name)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    for existing_h in root_logger.handlers[:]:
        root_logger.removeHandler(existing_h)
        try:
            existing_h.close()
        except Exception:
            pass

    logger_format = logging.Formatter("%(asctime)s %(levelname)s: %(message)s", datefmt="%d.%m.%Y %I:%M:%S %p")
    file_handler = logging.FileHandler(path_logfile, mode="w", encoding="utf-8")
    file_handler.setFormatter(logger_format)
    root_logger.addHandler(file_handler)

    # Redirect Python warnings (e.g. pandas FutureWarning during export_net) away from
    # Visum's sys.stderr modal Warning popup interceptor directly into dlnt_GUI.log.
    def _log_warning(message, category, filename, lineno, file=None, line=None):
        logging.warning("%s:%s: %s: %s", filename, lineno, category.__name__, message)

    warnings.showwarning = _log_warning
    warnings.filterwarnings("ignore", category=FutureWarning)
    return path_logfile


# ---------------------------------------------------------------------------
# PANDO Design System Palette & Custom Widgets (matches core/app/app_qt/main_window.py)
# ---------------------------------------------------------------------------
_PANDO_BG          = wx.Colour(248, 250, 252)  # #f8fafc
_PANDO_CARD        = wx.Colour(255, 255, 255)  # #ffffff
_PANDO_BORDER      = wx.Colour(203, 213, 225)  # #cbd5e1
_PANDO_BORDER_SOFT = wx.Colour(226, 232, 240)  # #e2e8f0
_PANDO_TEXT        = wx.Colour(15, 23, 42)     # #0f172a
_PANDO_BODY        = wx.Colour(51, 65, 85)     # #334155
_PANDO_MUTED       = wx.Colour(100, 116, 139)  # #64748b
_PANDO_BLUE        = wx.Colour(37, 99, 235)    # #2563eb
_PANDO_BLUE_HOVER  = wx.Colour(29, 78, 216)    # #1d4ed8
_PANDO_GREEN       = wx.Colour(16, 185, 129)   # #10b981
_PANDO_GREEN_HOVER = wx.Colour(5, 150, 105)    # #059669
_PANDO_HOVER_BG    = wx.Colour(241, 245, 249)  # #f1f5f9
_PANDO_DANGER      = wx.Colour(185, 28, 28)    # #b91c1c
_PANDO_DANGER_BDR  = wx.Colour(254, 202, 202)  # #fecaca
_PANDO_DANGER_HOV  = wx.Colour(254, 242, 242)  # #fef2f2
_PANDO_DISABLED_BG = wx.Colour(248, 250, 252)  # #f8fafc
_PANDO_DISABLED_FG = wx.Colour(148, 163, 184)  # #94a3b8


class _PandoButton(wx.Panel):
    """Flat, rounded PANDO-style button with hover/disabled states (drop-in for wx.Button)."""

    def __init__(self, parent, label: str = "", *, variant: str = "secondary", height: int = 38):
        super().__init__(parent, style=wx.NO_BORDER)
        self._label = label
        self._variant = variant
        self._hover = False
        self._pressed = False
        self._enabled = True
        self.SetBackgroundStyle(wx.BG_STYLE_PAINT)
        self.SetMinSize((-1, height))
        self.SetCursor(wx.Cursor(wx.CURSOR_HAND))

        self.Bind(wx.EVT_PAINT, self._on_paint)
        self.Bind(wx.EVT_ENTER_WINDOW, self._on_enter)
        self.Bind(wx.EVT_LEAVE_WINDOW, self._on_leave)
        self.Bind(wx.EVT_LEFT_DOWN, self._on_down)
        self.Bind(wx.EVT_LEFT_UP, self._on_up)

    def SetLabel(self, label: str):
        self._label = str(label)
        self.Refresh()

    def GetLabel(self) -> str:
        return self._label

    def Enable(self, enable: bool = True):
        self._enabled = bool(enable)
        self.SetCursor(wx.Cursor(wx.CURSOR_HAND if self._enabled else wx.CURSOR_ARROW))
        super().Enable(self._enabled)
        self.Refresh()
        return True

    def _palette(self):
        if not self._enabled:
            return _PANDO_DISABLED_BG, _PANDO_BORDER_SOFT, _PANDO_DISABLED_FG, False
        if self._variant == "green":
            bg = _PANDO_GREEN_HOVER if (self._hover or self._pressed) else _PANDO_GREEN
            return bg, _PANDO_GREEN_HOVER, _PANDO_CARD, True
        if self._variant == "blue":
            bg = _PANDO_BLUE_HOVER if (self._hover or self._pressed) else _PANDO_BLUE
            return bg, _PANDO_BLUE_HOVER, _PANDO_CARD, True
        if self._variant == "danger":
            bg = _PANDO_DANGER_HOV if (self._hover or self._pressed) else _PANDO_CARD
            return bg, _PANDO_DANGER_BDR, _PANDO_DANGER, False
        bg = _PANDO_HOVER_BG if (self._hover or self._pressed) else _PANDO_CARD
        return bg, _PANDO_BORDER, _PANDO_BODY, False

    def _on_enter(self, _evt):
        if self._enabled:
            self._hover = True
            self.Refresh()

    def _on_leave(self, _evt):
        self._hover = False
        self._pressed = False
        self.Refresh()

    def _on_down(self, _evt):
        if self._enabled:
            self._pressed = True
            self.Refresh()

    def _on_up(self, _evt):
        if self._enabled and self._pressed:
            self._pressed = False
            self.Refresh()
            evt = wx.CommandEvent(wx.wxEVT_BUTTON, self.GetId())
            evt.SetEventObject(self)
            self.GetEventHandler().ProcessEvent(evt)

    def _on_paint(self, _evt):
        dc = wx.AutoBufferedPaintDC(self)
        w, h = self.GetClientSize()
        dc.SetBackground(wx.Brush(self.GetParent().GetBackgroundColour()))
        dc.Clear()

        bg, border, fg, is_bold = self._palette()
        gc = wx.GraphicsContext.Create(dc)
        if gc:
            gc.SetBrush(gc.CreateBrush(wx.Brush(bg)))
            gc.SetPen(gc.CreatePen(wx.Pen(border, 1)))
            gc.DrawRoundedRectangle(0.5, 0.5, max(1, w - 1), max(1, h - 1), 6)

            font = self.GetFont()
            font.SetPointSize(9)
            if is_bold:
                font.SetWeight(wx.FONTWEIGHT_BOLD)
            gc.SetFont(font, fg)
            tw, th = gc.GetTextExtent(self._label)
            gc.DrawText(self._label, (w - tw) / 2.0, (h - th) / 2.0)


class _PandoProgressBar(wx.Panel):
    """Slim full-width PANDO progress bar (#e2e8f0 track, #2563eb fill)."""

    def __init__(self, parent, height: int = 6):
        super().__init__(parent, style=wx.NO_BORDER)
        self._value = 0
        self.SetBackgroundStyle(wx.BG_STYLE_PAINT)
        self.SetMinSize((-1, height))
        self.Bind(wx.EVT_PAINT, self._on_paint)

    def SetValue(self, pct: int):
        self._value = max(0, min(100, int(pct)))
        self.Refresh()

    def _on_paint(self, _evt):
        dc = wx.AutoBufferedPaintDC(self)
        w, h = self.GetClientSize()
        dc.SetBackground(wx.Brush(_PANDO_BORDER_SOFT))
        dc.Clear()
        if self._value > 0:
            fill_w = int(round(w * (self._value / 100.0)))
            dc.SetPen(wx.TRANSPARENT_PEN)
            dc.SetBrush(wx.Brush(_PANDO_BLUE))
            dc.DrawRectangle(0, 0, fill_w, h)


def _make_pando_card(parent, title: str):
    """Create a PANDO-styled white card with a crisp 1px border and bold header."""
    border_wrap = wx.Panel(parent)
    border_wrap.SetBackgroundColour(_PANDO_BORDER_SOFT)
    wrap_sizer = wx.BoxSizer(wx.VERTICAL)

    card = wx.Panel(border_wrap)
    card.SetBackgroundColour(_PANDO_CARD)
    card_sizer = wx.BoxSizer(wx.VERTICAL)

    title_lbl = wx.StaticText(card, -1, title)
    t_font = title_lbl.GetFont()
    t_font.SetPointSize(10)
    t_font.SetWeight(wx.FONTWEIGHT_BOLD)
    title_lbl.SetFont(t_font)
    title_lbl.SetForegroundColour(_PANDO_TEXT)
    card_sizer.Add(title_lbl, 0, wx.LEFT | wx.RIGHT | wx.TOP, 14)

    divider = wx.Panel(card, size=(-1, 1))
    divider.SetBackgroundColour(_PANDO_BORDER_SOFT)
    card_sizer.Add(divider, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 10)

    content_sizer = wx.BoxSizer(wx.VERTICAL)
    card_sizer.Add(content_sizer, 1, wx.EXPAND | wx.ALL, 14)
    card.SetSizer(card_sizer)

    wrap_sizer.Add(card, 1, wx.EXPAND | wx.ALL, 1)
    border_wrap.SetSizer(wrap_sizer)
    return border_wrap, card, title_lbl, content_sizer


# ---------------------------------------------------------------------------
# Shared config reader  (rin_config.json written by RIN_Matrizen.py)
# ---------------------------------------------------------------------------

def _load_rin_config(visum=None):
    """
    Load rin_config.json from the directory of the open VISUM file.
    Returns a list of five dicts, each with keys "n" and "v", one per CFL
    tier (index 0 = CFL 0, …, index 4 = CFL 4).

    Falls back to default values (n=2, v=1 for every tier) if the file is
    absent or unreadable, so the GUI behaves exactly as before for users who
    have not yet run RIN_Matrizen.py with the new dialog.

    Parameters
    ----------
    visum : optional
        A live Visum COM object.  Its GetPath(1) gives the directory of the
        open .ver file – the canonical location for rin_config.json.
        Falls back to the current working directory when None.

    Returns
    -------
    list of dict  – five entries, each {"n": int, "v": int}.
    """
    _defaults = [{"n": 2, "v": 0}] * 5
    try:
        if visum is not None:
            ver_path = visum.GetPath(1)
            search_dir = os.path.dirname(ver_path) if ver_path else os.getcwd()
        else:
            search_dir = os.getcwd()

        config_path = os.path.join(search_dir, "rin_config.json")
        if os.path.isfile(config_path):
            with open(config_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            params = data.get("cfl_params")
            if isinstance(params, list) and len(params) == 5:
                result = [
                    {
                        "n": int(params[i].get("n", 2)),
                        "v": int(params[i].get("v", 1)),
                    }
                    for i in range(5)
                ]
                logging.info(
                    "dlnt_GUI: loaded per-CFL params from {}".format(config_path))
                return result
    except Exception as exc:
        logging.warning(
            "dlnt_GUI: could not read rin_config.json – {}".format(exc))
    return [dict(d) for d in _defaults]

# ===== Helper Functions =====
## Loads all Visum zone attributes.
#  @param Visum The Visum instance to get attributes from.
#  @return A list of all zone attribute IDs.
def get_attr_zones(Visum):
    list_attr = Visum.Net.Zones.Attributes.GetAll
    list_attr_id = [attr.ID for attr in list_attr]

    return list_attr_id


# ======= Classes ======

## @class DirectLineNetworkToolFrame
#  @brief Defines the complete window, creates the individual components and connects them with the logic.
#
#  The `DirectLineNetworkToolFrame` class is the main window of the application.
#  It creates and manages all UI components,
#  handles user interactions, and connects the UI with the underlying logic.
class DirectLineNetworkToolFrame(wx.Frame):
    ## Initializes the main application window.
    #  @param translator The translator object to handle language localization.
    def __init__(self, translator):
        super().__init__(parent=None, style=wx.DEFAULT_FRAME_STYLE | wx.STAY_ON_TOP)

        self.translator = translator # Pointer to translator

        # ===== Attributes =====
        self.buttons_value_n_supplier = None
        self.cb_origin = None
        self.cb_destination = None
        self.cb_cfl = None
        self.buttons_cfl_value = None
        self.buttons_value_k_neighbor_cfl = None
        self.button_cfl_active = None
        self.dln_calculator = None
        self.default_k_neighbor = 1
        self.default_no_supplier = 0
        self.attr_cfl = "TypeNo"
        self.attr_origin = None
        self.attr_destination = None

        # If Visum exists -> do nothing
        # otherwise open a window to select a file
        try:
            # tests if the variable Visum exists
            global Visum
            Visum
        except NameError:
            import win32com.client as com
            defDir = Path.cwd()
            with wx.FileDialog(self,
                               self.translator.translate('file_selection'),
                               str(defDir),
                               wildcard=(self.translator.translate('ver_files')+ " (*.ver)|*.ver"), # Use self.translator
                               style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as file_dlg:
                if file_dlg.ShowModal() == wx.ID_CANCEL:
                    return  # the user changed their mind
                source = Path(file_dlg.Path)

            if source.suffix == ".ver":
                version = 250
                Visum = com.Dispatch(f"Visum.Visum.{version}")
                logging.info('open visum file: {}'.format(source))
                Visum.LoadVersion(source)

        self.visum = Visum
        _configure_file_logging(self.visum)
        self.list_attr = get_attr_zones(self.visum)

        # ── Read shared config (written by RIN_Matrizen.py) ──────────────
        # _rin_cfl_params is a list of five {"n", "v"} dicts, one per CFL tier.
        # The v values pre-populate the supplier spinners (cfl_0 … cfl_4) so
        # the user sees the values they entered in the earlier workflow step.
        # cfl_5 has no matching RIN tier – it keeps the previous default (0).
        # The user can still override any value in the GUI at any time.
        self._default_no_supplier_per_cfl = {
            "cfl_0": 0,
            "cfl_1": 0,
            "cfl_2": 0,
            "cfl_3": 0,
            "cfl_4": 0,
            "cfl_5": 0,
        }
        logging.info("dlnt_GUI: per-CFL default suppliers: {}".format(
            self._default_no_supplier_per_cfl))

        self.attr_origin = None
        self.attr_destination = None
        self.attr_cfl = "TypeNo"
        self.attr_dist_fcn = "euclidean"

        self.__set_layout__()
        self.__set_properties__()
        self.__bind_events__()

        self.Show()

        # Fit to best size so Export to Visum & Tools are 100% visible on open without resizing
        self.panel.Layout()
        best = self.panel.GetBestSize()
        min_w = max(1320, best.width + 36)
        min_h = max(620, best.height + 64)
        self.SetMinSize((min_w, min_h))
        self.SetSize((min_w, min_h))
        self.CentreOnScreen()
        self.Layout()
        self.SendSizeEvent()

        self.Raise()
        self.SetFocus()

        # Auto-import data once the wx MainLoop is active
        wx.CallAfter(self.event_import_data, None)

    ## Sets the properties of the main window.
    #  Configures window title, size, and initializes default values.
    def __set_properties__(self):
        self.SetTitle(self.translator.translate('app_title'))
        self.__set_values_cfl_buttons__()
        self.event_set_default()

    ## Sets up the layout of the main window.
    #  Creates and arranges all UI components in PANDO style with full-width progress bar.
    def __set_layout__(self):
        self.SetBackgroundColour(_PANDO_BG)
        self.panel = wx.Panel(self)
        self.panel.SetBackgroundColour(_PANDO_BG)

        root_sizer = wx.BoxSizer(wx.VERTICAL)

        # ---- Main content (directly inside panel, no redundant header or Notebook tab) ----
        self.notebook = None
        self.tabMain = MainTab(self.panel, self.translator)

        # ---- Right PANDO action card ----
        side_wrap, self.side_panel, self._lbl_side_title, side_sizer = _make_pando_card(
            self.panel, "Tools"
        )
        self.btn_side_language = _PandoButton(self.side_panel, self.translator.translate('language_choice_title'), height=36)
        self.btn_side_reset = _PandoButton(self.side_panel, self.translator.translate('menu_reset_calculations'), height=36)
        self.btn_side_defaults = _PandoButton(self.side_panel, self.translator.translate('menu_set_defaults'), height=36)
        self.btn_side_info = _PandoButton(self.side_panel, self.translator.translate('menu_info'), height=36)
        self.btn_side_filter = _PandoButton(self.side_panel, self.translator.translate('toolbar_filter_inserted_links'), height=36)
        self.btn_side_delete = _PandoButton(self.side_panel, self.translator.translate('toolbar_delete_inserted_links'), variant="danger", height=36)

        for btn in (self.btn_side_language, self.btn_side_reset,
                    self.btn_side_defaults, self.btn_side_info, self.btn_side_filter, self.btn_side_delete):
            btn.SetMinSize((190, 36))
            side_sizer.Add(btn, 0, wx.EXPAND | wx.BOTTOM, 8)

        side_sizer.AddStretchSpacer()

        body_sizer = wx.BoxSizer(wx.HORIZONTAL)
        body_sizer.Add(self.tabMain, 1, wx.EXPAND | wx.ALL, 12)
        body_sizer.Add(side_wrap, 0, wx.EXPAND | wx.TOP | wx.RIGHT | wx.BOTTOM, 12)
        root_sizer.Add(body_sizer, 1, wx.EXPAND)

        # ---- Full-width slim PANDO progress bar across the entire window ----
        self.progress_bar = _PandoProgressBar(self.panel, height=5)
        root_sizer.Add(self.progress_bar, 0, wx.EXPAND)

        self.panel.SetSizer(root_sizer)
        self.CreateStatusBar()


    ## Binds event handlers to UI elements.
    # Sets up all event bindings for menu items, toolbar buttons, and other UI elements.
    def __bind_events__(self):
        # Event Handler
        # bind the menu event to an event handler, share QuitBtn event
        self.Bind(wx.EVT_CLOSE, self.event_quit_button)

        # Side panel buttons
        self.btn_side_language.Bind(wx.EVT_BUTTON, self.on_choose_language)
        self.btn_side_reset.Bind(wx.EVT_BUTTON, self.event_reset)
        self.btn_side_defaults.Bind(wx.EVT_BUTTON, self.event_set_default)
        self.btn_side_info.Bind(wx.EVT_BUTTON, self.event_info)
        self.btn_side_filter.Bind(wx.EVT_BUTTON, self.event_filter)
        self.btn_side_delete.Bind(wx.EVT_BUTTON, self.event_delete_links)


    ## Sets the values and ranges for the CFL buttons.
    #  Configures the range and initial values for the CFL value buttons based on the maximum value of the selected attribute.
    def __set_values_cfl_buttons__(self):
        max_value_cfl = int(self.visum.Net.AttValue(f"Max:Zones\\{self.attr_cfl}"))
        idx = 0
        for btn in self.buttons_cfl_value.values():
            btn.SetRange(0, max_value_cfl)
            btn.SetValue(idx)
            idx += 1

    ## Event handler for language selection.
    #  Opens a dialog for the user to select a language, updates the translator with the selected language,
    #  and refreshes the GUI text to reflect the new language.
    #  @param event The event object.
    def on_choose_language(self, event):

        languages = sorted(list(self.translator.translations.keys()))

        current_language = self.translator.get_selected_language()
        current_selection_index = languages.index(current_language) if current_language in languages else 0

        # Create the wx.SingleChoiceDialogue
        dlg = wx.SingleChoiceDialog(
            self,
            self.translator.translate('language_choice_title'),
            self.translator.translate('language_choice'),
            choices=languages,
        )
        dlg.SetSelection(current_selection_index)

        if dlg.ShowModal() == wx.ID_OK:
            selected_language = languages[dlg.GetSelection()]

            if selected_language != self.translator.get_selected_language():
                # Change the language in the translator
                self.translator.update_selected_language(selected_language)
                # Update the GUI texts (Step 3)
                self.refresh_gui_text()

                if self.dln_calculator is not None:
                    self.dln_calculator.update_label_cfl()

        dlg.Destroy()  # Important: Destroy the dialog after it has been closed.

    ## Event handler for setting default values.
    #  Resets all input fields to their default values, including neighbor counts,
    #  supplier counts, and attribute selections.
    #  @param event The event object (optional).
    def event_set_default(self, event=None):
        # functional, may need to add default attribute values for CFL

        self.buttons_value_k_neighbor_cfl['cfl_0'].SetValue(self.default_k_neighbor) # Reverted to 'cfl_0'
        self.buttons_value_k_neighbor_cfl['cfl_1'].SetValue(self.default_k_neighbor)
        self.buttons_value_k_neighbor_cfl['cfl_2'].SetValue(self.default_k_neighbor)
        self.buttons_value_k_neighbor_cfl['cfl_3'].SetValue(self.default_k_neighbor)
        self.buttons_value_k_neighbor_cfl['cfl_4'].SetValue(self.default_k_neighbor)
        self.buttons_value_k_neighbor_cfl['cfl_5'].SetValue(self.default_k_neighbor)

        # Apply per-CFL supplier defaults from rin_config.json when available,
        # otherwise fall back to the single global default_no_supplier value.
        _per_cfl = getattr(self, '_default_no_supplier_per_cfl', None)
        for key in ('cfl_0', 'cfl_1', 'cfl_2', 'cfl_3', 'cfl_4', 'cfl_5'):
            v = _per_cfl[key] if _per_cfl is not None else self.default_no_supplier
            self.buttons_value_n_supplier[key].SetValue(v)

        self.cb_cfl.SetValue("TypeNo")
        self.cb_origin.SetValue("None")
        self.cb_destination.SetValue("None")
        self.cb_dist_fcn.SetValue("euclidean")

        self.SetStatusText(self.translator.translate('status_set_defaults'))

    ## Event handler for attribute selection changes.
    #  Updates the appropriate attribute based on which combo box triggered the event,
    #  reinitializes the calculator with the new attributes, and updates the status bar.
    #  @param event The event object.
    def event_choose_attr(self, event):
        attr = event.GetEventObject().GetStringSelection()

        if event.GetEventObject().Label == 'attr_cfl':
            self.attr_cfl = attr
            # Adjust buttons for attribute values
            self.__set_values_cfl_buttons__()

        elif event.GetEventObject().Label == 'attr_origin':
            if attr == 'None':
                self.attr_origin = None
            else:
                self.attr_origin = attr
        elif event.GetEventObject().Label == 'attr_destination':
            if attr == 'None':
                self.attr_destination = None
            else:
                self.attr_destination = attr
        elif event.GetEventObject().Label == 'attr_dist_fcn':
            self.attr_dist_fcn = attr
        else:
            logging.warning("should never happen")

        # Creating a new Calculator instance
        if self.visum is not None:
            # Initialize Calculator instance
            self.dln_calculator = dlnt.DirectLineNetworkCalculator(self.visum, attr_cfl=self.attr_cfl, max_distance=1,
                                                                   no_suppliers=0, attr_orig=self.attr_origin,
                                                                   attr_dest=self.attr_destination,
                                                                   translator=self.translator)
            # Pass current parameters
            self.update_param_cfl()
        else:
            logging.warning("Handling of file type is not implemented")
        self.SetStatusText(self.translator.translate('status_attribute_applied_reimport_reset'))

    ## Event handler for the calculate button.
    #  Updates the parameters from the UI, performs the main calculation,
    #  and updates the status bar to indicate completion.
    #  @param event The event object.
    def set_progress(self, current: int, total: int, message: str = ""):
        """Update the full-width PANDO progress bar and status bar without re-entering Visum's UI loop."""
        gauge = getattr(self, "progress_bar", None)
        if gauge is not None:
            total = max(1, int(total))
            pct = max(0, min(100, int(round(100.0 * current / total))))
            gauge.SetValue(pct)
            gauge.Update()
        if message:
            self.SetStatusText(message)
        sb = self.GetStatusBar()
        if sb is not None:
            sb.Refresh()
            sb.Update()

    def event_calculate(self, event):

        if self.dln_calculator is None:
            logging.warning("There is no calculator instance defined")
            wx.MessageBox(self.translator.translate('error_no_calculator_instance'), 'Info', wx.OK | wx.ICON_INFORMATION)
            return

        wx.BeginBusyCursor()
        try:
            self.update_param_cfl()
            self.set_progress(0, 1, "Calculating...")
            self.dln_calculator.calculate_main(
                on_progress=lambda cur, tot, label: self.set_progress(
                    cur, tot, f"Calculating {label} ({cur}/{tot})..." if label else "Calculating..."
                )
            )
            self.set_progress(1, 1, self.translator.translate('status_calculation_performed'))
        except Exception as exc:
            logging.exception("Calculation failed: %s", exc)
            self.set_progress(0, 1, f"Calculation failed: {exc}")
            wx.MessageBox(f"Calculation failed:\n{exc}", "Error", wx.OK | wx.ICON_ERROR)
        finally:
            if wx.IsBusy():
                wx.EndBusyCursor()
            for handler in logging.getLogger().handlers:
                try:
                    handler.flush()
                except Exception:
                    pass

    ## Event handler for the quit button or window close event.
    #  Destroys the panel and frame, and exits the application's main loop.
    #  @param event The event object.
    def event_quit_button(self, event):
        root_logger = logging.getLogger()
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass
        self.panel.Destroy()
        self.Destroy()
        wx.GetApp().ExitMainLoop()

    ## Event handler for importing data.
    #  Creates a new DirectLineNetworkCalculator instance with the current attributes,
    #  updates parameters, and updates the status bar.
    #  @param event The event object.
    def event_import_data(self, event):
        # Show loading feedback in status bar and busy cursor while data is being imported
        self.SetStatusText(self.translator.translate('status_loading_data') if
                           self.translator.translate('status_loading_data') != 'status_loading_data'
                           else "Loading data, please wait...")
        self.Refresh()
        self.Update()
        wx.BeginBusyCursor()
        try:
            if self.visum is not None:
                logging.info("Importing zone data from Visum...")
                self.dln_calculator = dlnt.DirectLineNetworkCalculator(
                    self.visum,
                    attr_cfl=self.attr_cfl,
                    max_distance=1,
                    no_suppliers=0,
                    attr_orig=self.attr_origin,
                    attr_dest=self.attr_destination,
                    translator=self.translator,
                )
                self.update_param_cfl()
                self.SetStatusText(self.translator.translate('status_data_imported'))
            else:
                logging.warning("Handling of file type is not implemented")
                self.SetStatusText("No Visum connection available.")
        except Exception as exc:
            logging.exception("Failed to import data from Visum: %s", exc)
            self.SetStatusText(f"Import failed: {exc}")
            wx.MessageBox(f"Data import failed:\n{exc}", "Error", wx.OK | wx.ICON_ERROR)
        finally:
            if wx.IsBusy():
                wx.EndBusyCursor()
            for handler in logging.getLogger().handlers:
                try:
                    handler.flush()
                except Exception:
                    pass

    ## Event handler for the Info button.
    #  @param event The event object.
    def event_info(self, event):
        doc_base = (
            _PANDO_HELPER_DIR
            if (_PANDO_HELPER_DIR is not None and (_PANDO_HELPER_DIR / "documentation").is_dir())
            else Path(__file__).parent
        )
        logging.info(doc_base)
        HelpPopUp(self, self.translator, doc_base)

    ## Event handler for resetting calculations.
    #  Initializes the results in the calculator, effectively clearing any previous calculations,
    #  and updates the status bar.
    #  @param event The event object.
    def event_reset(self, event):
        if self.dln_calculator is None:
            a = 1  # todo
        else:
            self.dln_calculator.init_results()

        self.SetStatusText(self.translator.translate('status_results_deleted_may_reimport'))

    ## Event handler for exporting all results.
    #  Exports both the network and matrix results from the calculator if it exists.
    #  @param event The event object.
    def event_export_results(self, event):
        if self.dln_calculator is not None:
            self.update_param_cfl()
            self.dln_calculator.export_net(links_additive=True)
            self.dln_calculator.export_matrix()

    ## Event handler for exporting network results for a specific CFL level.
    #  Exports the network results for the specified CFL level, deletes unused nodes,
    #  and updates the status bar with the CFL level that was exported.
    #  @param event The event object containing the CFL level to export.
    def event_export_net(self, event):
        cfl_level = event.GetEventObject().cfl
        cfl_name = self.button_cfl_active[cfl_level].Label

        if self.dln_calculator is not None:
            self.update_param_cfl()
            self.dln_calculator.export_net(links_additive=True, list_cfl=[cfl_level])
            self.dln_calculator.delete_unused_nodes()

        self.SetStatusText(f"{cfl_name}: {self.translator.translate('status_net_exported_imported')}")

    ## Event handler for exporting matrix results for a specific CFL level.
    #  Exports the matrix results for the specified CFL level and updates the status bar
    #  with the CFL level that was exported.
    #  @param event The event object containing the CFL level to export.
    def event_export_mtx(self, event):
        cfl_level = event.GetEventObject().cfl
        cfl_name = self.button_cfl_active[cfl_level].Label

        if self.dln_calculator is not None:
            self.update_param_cfl()
            overwrite = bool(self.tabMain.cb_overwrite_matrix.GetValue())
            self.dln_calculator.export_matrix(list_cfl=[cfl_level], overwrite_existing=overwrite)

        self.SetStatusText(f"{cfl_name}: {self.translator.translate('status_matrix_loaded_into_visum')}")

    ## Event handler for exporting all results at once.
    #  Exports both matrix and network results for all CFL levels, deletes unused nodes,
    #  and updates the status bar.
    #  @param event The event object.
    def event_export_master(self, event):
        if self.dln_calculator is None:
            return
        wx.BeginBusyCursor()
        try:
            self.update_param_cfl()
            overwrite = bool(self.tabMain.cb_overwrite_matrix.GetValue())
            self.set_progress(1, 3, "Exporting matrices...")
            self.dln_calculator.export_matrix(overwrite_existing=overwrite)
            self.set_progress(2, 3, "Exporting link network...")
            self.dln_calculator.export_net(links_additive=True)
            self.dln_calculator.delete_unused_nodes()
            self.tabMain.mark_exported()
            self.set_progress(3, 3, self.translator.translate('status_combined_results_imported'))
        except Exception as exc:
            logging.exception("Combined export failed: %s", exc)
            self.set_progress(0, 1, f"Export failed: {exc}")
            wx.MessageBox(f"Export failed:\n{exc}", "Error", wx.OK | wx.ICON_ERROR)
        finally:
            if wx.IsBusy():
                wx.EndBusyCursor()
            for handler in logging.getLogger().handlers:
                try:
                    handler.flush()
                except Exception:
                    pass

    ## Event handler for exporting MTX results for all currently checked CFL levels.
    #  Iterates over checked CFL checkboxes and exports the matrix for each selected level.
    #  @param event The event object.
    def event_export_mtx_selected(self, event):
        if self.dln_calculator is None:
            return
        self.update_param_cfl()
        selected = [cfl_level for cfl_level, cb in self.button_cfl_active.items() if cb.Value > 0]
        if not selected:
            wx.MessageBox(self.translator.translate('error_no_calculator_instance'), 'Info', wx.OK | wx.ICON_INFORMATION)
            return
        wx.BeginBusyCursor()
        try:
            overwrite = bool(self.tabMain.cb_overwrite_matrix.GetValue())
            total = len(selected)
            for idx, cfl_level in enumerate(selected, start=1):
                lbl = self.button_cfl_active[cfl_level].Label
                self.set_progress(idx - 1, total, f"Exporting matrix {lbl} ({idx}/{total})...")
                self.dln_calculator.export_matrix(list_cfl=[cfl_level], overwrite_existing=overwrite)
                self.set_progress(idx, total, f"Exported matrix {lbl} ({idx}/{total})")
            names = ", ".join(self.button_cfl_active[c].Label for c in selected)
            self.set_progress(total, total, f"{names}: {self.translator.translate('status_matrix_loaded_into_visum')}")
            self.tabMain.mark_exported(selected)
        except Exception as exc:
            logging.exception("Matrix export failed: %s", exc)
            self.set_progress(0, 1, f"Export failed: {exc}")
            wx.MessageBox(f"Matrix export failed:\n{exc}", "Error", wx.OK | wx.ICON_ERROR)
        finally:
            if wx.IsBusy():
                wx.EndBusyCursor()
            for handler in logging.getLogger().handlers:
                try:
                    handler.flush()
                except Exception:
                    pass

    ## Event handler for exporting Net results for all currently checked CFL levels.
    #  Iterates over checked CFL checkboxes and exports the network for each selected level.
    #  @param event The event object.
    def event_export_net_selected(self, event):
        if self.dln_calculator is None:
            return
        self.update_param_cfl()
        selected = [cfl_level for cfl_level, cb in self.button_cfl_active.items() if cb.Value > 0]
        if not selected:
            wx.MessageBox(self.translator.translate('error_no_calculator_instance'), 'Info', wx.OK | wx.ICON_INFORMATION)
            return
        wx.BeginBusyCursor()
        try:
            self.set_progress(1, 3, "Exporting link network...")
            self.dln_calculator.export_net(links_additive=True, list_cfl=selected)
            self.set_progress(2, 3, "Cleaning unused nodes...")
            self.dln_calculator.delete_unused_nodes()
            names = ", ".join(self.button_cfl_active[c].Label for c in selected)
            self.tabMain.mark_exported(selected)
            self.set_progress(3, 3, f"{names}: {self.translator.translate('status_net_exported_imported')}")
        except Exception as exc:
            logging.exception("Failed to export link network: %s", exc)
            self.set_progress(0, 1, f"Export failed: {exc}")
            wx.MessageBox(f"Link network export failed:\n{exc}", "Error", wx.OK | wx.ICON_ERROR)
        finally:
            if wx.IsBusy():
                wx.EndBusyCursor()
            for handler in logging.getLogger().handlers:
                try:
                    handler.flush()
                except Exception:
                    pass

    ## Event handler for filtering links.
    #  Applies a filter to show only the links created by the calculator.
    #  @param event The event object.
    def event_filter(self, event):
        if self.dln_calculator is not None:
            self.dln_calculator.filter_links_cfl()

    ## Event handler for deleting links.
    #  Deletes all links created by the calculator and removes any unused nodes.
    #  @param event The event object.
    def event_delete_links(self, event):
        if self.dln_calculator is not None:
            self.dln_calculator.delete_added_links()
            self.dln_calculator.delete_unused_nodes()

    ## Updates the calculator parameters from the UI.
    #  Collects the current values from the UI controls and updates the calculator's parameters,
    #  including CFL levels, supplier counts, neighbor counts, and distance formula. Also logs the current settings.
    def update_param_cfl(self):
        if self.dln_calculator is not None:
            # Use stable, language-independent keys ('cfl_0', 'cfl_1', etc.) for internal logic
            active_cfls = {cfl_level for cfl_level, item in self.button_cfl_active.items() if item.Value > 0}
            dict_num_suppliers = {cfl_level: self.buttons_value_n_supplier[cfl_level].Value for cfl_level in active_cfls}
            dict_max_neighbor = {cfl_level: self.buttons_value_k_neighbor_cfl[cfl_level].Value for cfl_level in active_cfls}
            dict_cfl_values = {cfl_level: self.buttons_cfl_value[cfl_level].Value for cfl_level in active_cfls}

            self.dln_calculator.deg_neighbourhood_cfl = dict_max_neighbor
            self.dln_calculator.num_suppliers_cfl = dict_num_suppliers
            self.dln_calculator.cfl = dict_cfl_values
            self.dln_calculator.formula_dist = self.attr_dist_fcn

            # Update/Assign labels for user-friendly export names
            self.dln_calculator.update_label_cfl()

            logging.info("Current Settings \n" +
                         f'zones: Attr. Centrality: {self.dln_calculator.attr_central_level}' + "\n" +
                         f'Attr is Origin: {self.dln_calculator.attr_is_from_zone}' + "\n" +
                         f'Attr is Destination: {self.dln_calculator.attr_is_to_zone}' + "\n" +
                         f'Distance calculation: {self.dln_calculator.formula_dist}' + "\n" +
                         f'CFL: {self.dln_calculator.cfl}' + "\n" +
                         f'Neighbourhood level per CFL: {self.dln_calculator.deg_neighbourhood_cfl}' + "\n" +
                         f'Number of Suppliers per CFL: {self.dln_calculator.num_suppliers_cfl}')


    ## Updates all text elements in the GUI to the current language.
    #  Also triggers refresh_gui_text on child components.
    def refresh_gui_text(self):
        # 1. Update main window title & header
        title = self.translator.translate('app_title')
        self.SetTitle(title)
        if hasattr(self, "lbl_header_title"):
            self.lbl_header_title.SetLabel(title)

        # 2. Update side panel buttons
        self.btn_side_language.SetLabel(self.translator.translate('language_choice_title'))
        self.btn_side_reset.SetLabel(self.translator.translate('menu_reset_calculations'))
        self.btn_side_defaults.SetLabel(self.translator.translate('menu_set_defaults'))
        self.btn_side_info.SetLabel(self.translator.translate('menu_info'))
        self.btn_side_filter.SetLabel(self.translator.translate('toolbar_filter_inserted_links'))
        self.btn_side_delete.SetLabel(self.translator.translate('toolbar_delete_inserted_links'))

        # 3. Update MainTab
        self.tabMain.refresh_gui_text()

        # Update the status text
        self.SetStatusText(self.translator.translate('status_set_defaults'))

        # Important: Force layout and refresh after text changes
        self.Layout()
        self.Refresh()


## @class MainTab
#  @brief Specifies and manages the tab with input and action options.
#
#  The `MainTab` class creates and manages the main tab of the application, which contains
#  all the input fields, buttons, and other controls for configuring and executing the
#  air-line calculations.
class MainTab(wx.Panel):
    ## Initializes the main tab panel.
    #  @param parent The parent window that contains this panel.
    #  @param translator The translator object to handle language localization.
    def __init__(self, parent, translator):
        wx.Panel.__init__(self, parent)

        self.translator = translator
        # Initialize instance attributes for widgets and sizers that need to be updated
        self.hbox1 = None
        self.gridbagsizer1 = None
        self.static_text_centrality = None
        self.static_text_origin = None
        self.static_text_destination = None
        self.static_text_cfl_label = None
        self.static_text_attr_cfl = None
        self.static_text_exchange_fcn = None
        self.static_text_supply_fcn = None
        self.static_text_visum_as = None
        self.static_text_dist_fcn_label = None
        self.btn_export_master = None # Ensure it's initialized
        self._box_zone_attrs = None
        self._box_cfl = None
        self._box_export = None
        self.static_text_status_header = None
        self.status_labels = {}          # {cfl_key: wx.StaticText}
        self._cfl_status = {}            # {cfl_key: str}  internal state
        self.btn_calculate = None        # new Calculate button

        self.__set_layout__()
        self.__bind_events__()

    ## Sets up the layout of the main tab panel.
    #  Creates and arranges all UI components including zone attribute selection,
    #  connectivity function level parameters, status column, calculate button,
    #  and a separate export section.
    def __set_layout__(self):
        self.SetBackgroundColour(_PANDO_BG)
        vbox_outer = wx.BoxSizer(wx.VERTICAL)

        # ---- Card 1: Zone Attribute Selection (3 strictly equal-width columns) ----
        zone_wrap, zone_card, self._box_zone_attrs, zone_body = _make_pando_card(
            self, self.translator.translate('zone_attributes')
        )
        zone_grid = wx.GridSizer(rows=1, cols=3, vgap=0, hgap=16)

        self.cb_cfl = wx.ComboBox(zone_card, choices=self.TopLevelParent.list_attr,
                                  style=wx.CB_DROPDOWN | wx.CB_READONLY | wx.CB_SORT)
        self.cb_cfl.Label = 'attr_cfl'
        self.TopLevelParent.cb_cfl = self.cb_cfl

        self.cb_origin = wx.ComboBox(zone_card, choices=self.TopLevelParent.list_attr,
                                     style=wx.CB_DROPDOWN | wx.CB_READONLY | wx.CB_SORT)
        self.cb_origin.Label = 'attr_origin'
        self.TopLevelParent.cb_origin = self.cb_origin

        self.cb_destination = wx.ComboBox(zone_card, choices=self.TopLevelParent.list_attr,
                                          style=wx.CB_DROPDOWN | wx.CB_READONLY | wx.CB_SORT)
        self.cb_destination.Label = 'attr_destination'
        self.TopLevelParent.cb_destination = self.cb_destination

        self.static_text_centrality = wx.StaticText(zone_card, -1, self.translator.translate('setting_zone_attribute_centrality'))
        self.static_text_origin = wx.StaticText(zone_card, -1, self.translator.translate('setting_zone_attribute_origin'))
        self.static_text_destination = wx.StaticText(zone_card, -1, self.translator.translate('setting_zone_attribute_destination'))

        for lbl, cb in (
            (self.static_text_centrality, self.cb_cfl),
            (self.static_text_origin, self.cb_origin),
            (self.static_text_destination, self.cb_destination),
        ):
            lbl.SetForegroundColour(_PANDO_BODY)
            cb.SetMinSize((-1, 30))
            col_box = wx.BoxSizer(wx.VERTICAL)
            col_box.Add(lbl, 0, wx.BOTTOM, 6)
            col_box.AddStretchSpacer(1)
            col_box.Add(cb, 0, wx.EXPAND)
            zone_grid.Add(col_box, 1, wx.EXPAND)

        zone_body.Add(zone_grid, 0, wx.EXPAND)
        self.hbox1 = zone_wrap

        # ---- Card 2: CFL/VFS Settings (GridSizer for strictly equal-width columns) ----
        cfl_wrap, cfl_card, self._box_cfl, self._gridbox_sizer = _make_pando_card(
            self, self.translator.translate('cfl_vfs_settings')
        )

        # Strictly equal-width 5-column GridSizer: 0=CFL, 1=AttrVal, 2=Exchange, 3=Supply, 4=Status
        NUM_COLS = 5
        self.gridbagsizer1 = wx.GridSizer(rows=7, cols=NUM_COLS, vgap=8, hgap=14)

        str_cfl = self.translator.translate('cfl')

        # --- Header row ---
        self.static_text_cfl_label = wx.StaticText(cfl_card, -1, self.translator.translate('setting_cfl'))
        self.static_text_attr_cfl = wx.StaticText(cfl_card, -1, self.translator.translate('setting_attribute_value_CFL'))
        self.static_text_exchange_fcn = wx.StaticText(cfl_card, -1, self.translator.translate('setting_exchange_function'))
        self.static_text_supply_fcn = wx.StaticText(cfl_card, -1, self.translator.translate('setting_supply_function'))
        self.static_text_status_header = wx.StaticText(cfl_card, -1, self.translator.translate('setting_status'))

        for hdr in (self.static_text_cfl_label, self.static_text_attr_cfl,
                    self.static_text_exchange_fcn, self.static_text_supply_fcn,
                    self.static_text_status_header):
            hdr.SetFont(hdr.GetFont().Bold())
            hdr.SetForegroundColour(_PANDO_TEXT)
            self.gridbagsizer1.Add(hdr, 0, wx.EXPAND | wx.ALIGN_CENTER_VERTICAL)

        # --- CFL rows (rows 1-6) ---
        self.button_cfl_active = {
            "cfl_0": wx.CheckBox(cfl_card, -1, f"{str_cfl} 0"),
            "cfl_1": wx.CheckBox(cfl_card, -1, f"{str_cfl} 1"),
            "cfl_2": wx.CheckBox(cfl_card, -1, f"{str_cfl} 2"),
            "cfl_3": wx.CheckBox(cfl_card, -1, f"{str_cfl} 3"),
            "cfl_4": wx.CheckBox(cfl_card, -1, f"{str_cfl} 4"),
            "cfl_5": wx.CheckBox(cfl_card, -1, f"{str_cfl} 5"),
        }
        self.buttons_cfl_value = {k: wx.SpinCtrl(cfl_card, -1, "") for k in self.button_cfl_active}
        self.buttons_value_k_neighbor_cfl = {k: wx.SpinCtrl(cfl_card, -1, "") for k in self.button_cfl_active}
        self.buttons_value_n_supplier = {k: wx.SpinCtrl(cfl_card, -1, "") for k in self.button_cfl_active}

        # Status labels + internal state
        for cfl_key in self.button_cfl_active:
            self._cfl_status[cfl_key] = 'inactive'
            lbl = wx.StaticText(cfl_card, -1, self.translator.translate('status_inactive'))
            lbl.SetForegroundColour(_PANDO_MUTED)
            self.status_labels[cfl_key] = lbl

        for cfl_key in self.button_cfl_active:
            self.button_cfl_active[cfl_key].SetForegroundColour(_PANDO_TEXT)
            self.gridbagsizer1.Add(self.button_cfl_active[cfl_key], 0, wx.EXPAND | wx.ALIGN_CENTER_VERTICAL)
            self.gridbagsizer1.Add(self.buttons_cfl_value[cfl_key], 0, wx.EXPAND | wx.ALIGN_CENTER_VERTICAL)
            self.gridbagsizer1.Add(self.buttons_value_k_neighbor_cfl[cfl_key], 0, wx.EXPAND | wx.ALIGN_CENTER_VERTICAL)
            self.gridbagsizer1.Add(self.buttons_value_n_supplier[cfl_key], 0, wx.EXPAND | wx.ALIGN_CENTER_VERTICAL)
            self.gridbagsizer1.Add(self.status_labels[cfl_key], 0, wx.EXPAND | wx.ALIGN_CENTER_VERTICAL)

        # Share references with TopLevelParent
        self.TopLevelParent.button_cfl_active = self.button_cfl_active
        self.TopLevelParent.buttons_cfl_value = self.buttons_cfl_value
        self.TopLevelParent.buttons_value_k_neighbor_cfl = self.buttons_value_k_neighbor_cfl
        self.TopLevelParent.buttons_value_n_supplier = self.buttons_value_n_supplier

        # Initially disable all CFL SpinCtrls – enabled when checkbox is ticked
        for cfl_key in self.button_cfl_active:
            self.buttons_cfl_value[cfl_key].Enable(False)
            self.buttons_value_k_neighbor_cfl[cfl_key].Enable(False)
            self.buttons_value_n_supplier[cfl_key].Enable(False)

        self._gridbox_sizer.Add(self.gridbagsizer1, 1, wx.EXPAND | wx.BOTTOM, 10)

        # Divider above bottom strip
        calc_div = wx.Panel(cfl_card, size=(-1, 1))
        calc_div.SetBackgroundColour(_PANDO_BORDER)
        self._gridbox_sizer.Add(calc_div, 0, wx.EXPAND | wx.BOTTOM, 10)

        # --- Bottom strip: distance function + PANDO green Calculate button ---
        self.cb_dist_fcn = wx.ComboBox(cfl_card, size=(160, 30),
                                       choices=["euclidean"],
                                       style=wx.CB_DROPDOWN | wx.CB_READONLY | wx.CB_SORT)
        self.cb_dist_fcn.Label = 'attr_dist_fcn'
        self.TopLevelParent.cb_dist_fcn = self.cb_dist_fcn

        self.static_text_dist_fcn_label = wx.StaticText(cfl_card, -1, self.translator.translate('label_distance_calculation_function'))
        self.static_text_dist_fcn_label.SetForegroundColour(_PANDO_BODY)

        self.btn_calculate = _PandoButton(cfl_card, self.translator.translate('btn_calculate'), variant="green", height=40)
        self.btn_calculate.SetMinSize((200, 40))

        _dist_sizer = wx.BoxSizer(wx.HORIZONTAL)
        _dist_sizer.Add(self.static_text_dist_fcn_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        _dist_sizer.Add(self.cb_dist_fcn, 0, wx.ALIGN_CENTER_VERTICAL)
        _dist_sizer.AddStretchSpacer(1)

        _bottom_strip = wx.BoxSizer(wx.HORIZONTAL)
        _bottom_strip.Add(_dist_sizer, 1, wx.EXPAND)
        _bottom_strip.Add(self.btn_calculate, 0, wx.ALIGN_CENTER_VERTICAL)
        self._gridbox_sizer.Add(_bottom_strip, 0, wx.EXPAND)

        # ---- Card 3: Export to Visum ----
        export_wrap, export_card, self._box_export, self._export_sizer = _make_pando_card(
            self, self.translator.translate('export_to_visum_section')
        )
        self.static_text_visum_as = None

        _btn_size = (185, 40)
        self.btn_export_mtx = _PandoButton(export_card, self.translator.translate('btn_export_mtx'), variant="secondary", height=40)
        self.btn_export_mtx.SetMinSize(_btn_size)
        self._export_sizer.Add(self.btn_export_mtx, 0, wx.EXPAND | wx.BOTTOM, 8)

        self.btn_export_net = _PandoButton(export_card, self.translator.translate('btn_export_net'), variant="secondary", height=40)
        self.btn_export_net.SetMinSize(_btn_size)
        self._export_sizer.Add(self.btn_export_net, 0, wx.EXPAND | wx.BOTTOM, 8)

        self.btn_export_master = _PandoButton(export_card, self.translator.translate('setting_import_to_visum'), variant="blue", height=40)
        self.btn_export_master.cfl_level = 'all'
        self.btn_export_master.SetMinSize(_btn_size)
        self._export_sizer.Add(self.btn_export_master, 0, wx.EXPAND | wx.BOTTOM, 10)

        self.cb_overwrite_matrix = wx.CheckBox(export_card, -1, self.translator.translate('setting_overwrite_matrix'))
        self.cb_overwrite_matrix.SetValue(True)
        self.cb_overwrite_matrix.SetForegroundColour(_PANDO_BODY)
        self._export_sizer.Add(self.cb_overwrite_matrix, 0, wx.EXPAND | wx.TOP, 2)
        self._export_sizer.AddStretchSpacer()

        # Start with export buttons disabled in PANDO muted style
        self._set_export_buttons_state(enabled=False)

        # ---- Horizontal container: calculation card + export card ----
        hbox_main = wx.BoxSizer(wx.HORIZONTAL)
        hbox_main.Add(cfl_wrap, 3, wx.EXPAND | wx.RIGHT, 12)
        hbox_main.Add(export_wrap, 0, wx.EXPAND)

        vbox_outer.Add(zone_wrap, 0, wx.EXPAND | wx.BOTTOM, 12)
        vbox_outer.Add(hbox_main, 1, wx.EXPAND)

        self.SetSizer(vbox_outer)

        # ==== Event binding

    ## Binds event handlers to UI elements in the main tab.
    #  Sets up all event bindings for buttons and combo boxes in the main tab,
    #  connecting them to the appropriate event handlers in the parent frame.
    def __bind_events__(self):

        self.btn_export_mtx.Bind(wx.EVT_BUTTON, self.TopLevelParent.event_export_mtx_selected)
        self.btn_export_net.Bind(wx.EVT_BUTTON, self.TopLevelParent.event_export_net_selected)

        self.btn_export_master.Bind(wx.EVT_BUTTON, self.TopLevelParent.event_export_master)

        # Calculate button
        self.btn_calculate.Bind(wx.EVT_BUTTON, self.on_calculate)

        self.cb_cfl.Bind(wx.EVT_COMBOBOX, self.TopLevelParent.event_choose_attr)
        self.cb_origin.Bind(wx.EVT_COMBOBOX, self.TopLevelParent.event_choose_attr)
        self.cb_destination.Bind(wx.EVT_COMBOBOX, self.TopLevelParent.event_choose_attr)
        self.cb_dist_fcn.Bind(wx.EVT_COMBOBOX, self._on_settings_changed)
        self.cb_dist_fcn.Bind(wx.EVT_COMBOBOX, self.TopLevelParent.event_choose_attr)

        # Enable/disable SpinCtrls based on checkbox state; also mark status outdated on change
        for cfl_key, checkbox in self.button_cfl_active.items():
            checkbox.Bind(wx.EVT_CHECKBOX, self.on_cfl_checkbox_toggle)
        for cfl_key in self.button_cfl_active:
            for spin in (self.buttons_cfl_value[cfl_key],
                         self.buttons_value_k_neighbor_cfl[cfl_key],
                         self.buttons_value_n_supplier[cfl_key]):
                spin.cfl_key = cfl_key   # attach key as attribute
                spin.Bind(wx.EVT_SPINCTRL, self._on_spin_changed)


    ## Enables or disables the three SpinCtrl fields for a CFL row based on its checkbox state.
    #  @param event The checkbox event. The event object's GetEventObject() identifies which checkbox fired.
    def on_cfl_checkbox_toggle(self, event):
        checkbox = event.GetEventObject()
        cfl_key = next((k for k, cb in self.button_cfl_active.items() if cb is checkbox), None)
        if cfl_key is None:
            return
        enabled = checkbox.IsChecked()
        self.buttons_cfl_value[cfl_key].Enable(enabled)
        self.buttons_value_k_neighbor_cfl[cfl_key].Enable(enabled)
        self.buttons_value_n_supplier[cfl_key].Enable(enabled)
        # Update status
        if enabled:
            # Only mark as not-calculated if it was inactive before
            if self._cfl_status[cfl_key] == 'inactive':
                self._set_row_status(cfl_key, 'not_calculated')
        else:
            self._set_row_status(cfl_key, 'inactive')
        self._refresh_export_button_state()
        event.Skip()

    ## Marks a row as outdated when a SpinCtrl value is changed after calculation.
    def _on_spin_changed(self, event):
        spin = event.GetEventObject()
        cfl_key = getattr(spin, 'cfl_key', None)
        if cfl_key and self._cfl_status.get(cfl_key) in ('calculated', 'exported'):
            self._set_row_status(cfl_key, 'outdated')
            self._refresh_export_button_state()
        event.Skip()

    ## Marks all active rows as outdated when distance function changes.
    def _on_settings_changed(self, event):
        for cfl_key, status in self._cfl_status.items():
            if status in ('calculated', 'exported'):
                self._set_row_status(cfl_key, 'outdated')
        self._refresh_export_button_state()
        event.Skip()

    ## Performs the calculation and updates status labels.
    def on_calculate(self, event):
        self.TopLevelParent.event_calculate(event)
        # Mark all active (non-inactive) rows as calculated
        for cfl_key, cb in self.button_cfl_active.items():
            if cb.IsChecked():
                self._set_row_status(cfl_key, 'calculated')
        self._refresh_export_button_state()

    ## Sets the status of a single CFL row and updates its label colour.
    def _set_row_status(self, cfl_key, status):
        self._cfl_status[cfl_key] = status
        lbl = self.status_labels[cfl_key]
        _colors = {
            'not_calculated': wx.Colour(180, 80, 0),
            'calculated':     wx.Colour(0, 140, 0),
            'outdated':       wx.Colour(180, 130, 0),
            'exported':       wx.Colour(0, 100, 180),
            'inactive':       wx.Colour(150, 150, 150),
        }
        _keys = {
            'not_calculated': 'status_not_calculated',
            'calculated':     'status_calculated',
            'outdated':       'status_outdated',
            'exported':       'status_exported',
            'inactive':       'status_inactive',
        }
        lbl.SetLabel(self.translator.translate(_keys[status]))
        lbl.SetForegroundColour(_colors.get(status, wx.Colour(0, 0, 0)))
        lbl.Refresh()

    ## Enables or greys-out the export buttons depending on whether all active rows are 'calculated' or 'exported'.
    def _refresh_export_button_state(self):
        active_statuses = [
            self._cfl_status[k] for k, cb in self.button_cfl_active.items() if cb.IsChecked()
        ]
        all_ready = active_statuses and all(s in ('calculated', 'exported') for s in active_statuses)
        self._set_export_buttons_state(all_ready)

    ## Enables or disables (greys out) the export buttons in PANDO style.
    def _set_export_buttons_state(self, enabled: bool):
        for btn in (self.btn_export_mtx, self.btn_export_net, self.btn_export_master):
            btn.Enable(enabled)

    ## Marks all active rows as 'exported'; called after a successful export.
    def mark_exported(self, cfl_keys=None):
        if cfl_keys is None:
            cfl_keys = [k for k, cb in self.button_cfl_active.items() if cb.IsChecked()]
        for k in cfl_keys:
            if self._cfl_status.get(k) in ('calculated', 'exported'):
                self._set_row_status(k, 'exported')
        self._refresh_export_button_state()

    ## Updates all text elements in the main tab to the current language.
    def refresh_gui_text(self):
        # Update StaticBox labels
        self._box_zone_attrs.SetLabel(self.translator.translate('zone_attributes'))
        self._box_cfl.SetLabel(self.translator.translate('cfl_vfs_settings'))
        self._box_export.SetLabel(self.translator.translate('export_to_visum_section'))

        # Update StaticText widgets
        self.static_text_centrality.SetLabel(self.translator.translate('setting_zone_attribute_centrality'))
        self.static_text_origin.SetLabel(self.translator.translate('setting_zone_attribute_origin'))
        self.static_text_destination.SetLabel(self.translator.translate('setting_zone_attribute_destination'))
        self.static_text_cfl_label.SetLabel(self.translator.translate('setting_cfl'))
        self.static_text_attr_cfl.SetLabel(self.translator.translate('setting_attribute_value_CFL'))
        self.static_text_exchange_fcn.SetLabel(self.translator.translate('setting_exchange_function'))
        self.static_text_supply_fcn.SetLabel(self.translator.translate('setting_supply_function'))
        self.static_text_status_header.SetLabel(self.translator.translate('setting_status'))
        self.static_text_dist_fcn_label.SetLabel(self.translator.translate('label_distance_calculation_function'))

        # Update Calculate button
        self.btn_calculate.SetLabel(self.translator.translate('btn_calculate'))

        # Update CheckBox labels
        for key, checkbox in self.button_cfl_active.items():
            str_cfl = self.translator.translate('cfl')
            no_cfl = int(key.split('_')[1])
            checkbox.SetLabel(f"{str_cfl} {no_cfl}")

        # Update status labels to current language keeping their current state
        for cfl_key, status in self._cfl_status.items():
            self._set_row_status(cfl_key, status)

        # Update master export button label
        self.btn_export_master.SetLabel(self.translator.translate('setting_import_to_visum'))
        self.btn_export_mtx.SetLabel(self.translator.translate('btn_export_mtx'))
        self.btn_export_net.SetLabel(self.translator.translate('btn_export_net'))
        self.cb_overwrite_matrix.SetLabel(self.translator.translate('setting_overwrite_matrix'))

        self.Layout()
        self.Refresh()


## @class LogTab
#  @brief Specifies the tab that displays log messages.
#
#  The `LogTab` class creates and manages the log tab of the application, which displays
#  log messages from the application. It sets up logging handlers to capture and display
#  messages in a text control.
class LogTab(wx.Panel):
    ## Initializes the log tab panel.
    #  @param parent The parent window that contains this panel.
    def __init__(self, parent):
        wx.Panel.__init__(self, parent)
        vbox = wx.BoxSizer(wx.VERTICAL)
        vbox.Add(wx.StaticText(self, -1, "Message-Log"), 0, wx.ALL | wx.CENTER, 5)
        # self.multiText = wx.TextCtrl(panel, -1, "", style=wx.TE_MULTILINE)
        # self.multiText.SetInsertionPoint(0)
        #
        # vbox2.Add(self.multiText, 2, wx.ALIGN_CENTER | wx.ALL, 20)
        #

        # ==== Logging =====
        log_name = Path(__file__).name.replace(".py", ".log")
        log_dir = Path(__file__).resolve().parent.parent / "log"
        try:
            visum_log_dir = Path(self.visum.GetPath(57)) if self.visum is not None else None
            if visum_log_dir and visum_log_dir.is_dir():
                log_dir = visum_log_dir
        except Exception:
            pass
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            path_logfile = log_dir / log_name
        except OSError:
            path_logfile = Path(log_name)
        self.logger = logging.getLogger()
        self.logger.setLevel(logging.INFO)
        logger_format = logging.Formatter("%(asctime)s %(levelname)s: %(message)s", datefmt="%d.%m.%Y %I:%M:%S %p")

        # Clear any stale handlers (e.g. from prior runs in the same Visum Python session or
        # StreamHandlers targeting Visum's redirected sys.stderr, which deadlocks wx.MainLoop).
        for existing_h in self.logger.handlers[:]:
            self.logger.removeHandler(existing_h)
            try:
                existing_h.close()
            except Exception:
                pass

        # Output to Logfile & GUI Log-Tab (avoid sys.stderr StreamHandler inside Visum UI)
        file_handler = logging.FileHandler(path_logfile, mode="w", encoding="utf-8")
        file_handler.setFormatter(logger_format)
        self.logger.addHandler(file_handler)

        self.log = wx.TextCtrl(self, wx.ID_ANY, size=(700, 200),
                               style=wx.TE_MULTILINE | wx.TE_READONLY | wx.HSCROLL | wx.EXPAND)
        self.handler = WxTextCtrlHandler(self.log)
        self.handler.setFormatter(logger_format)
        self.logger.addHandler(self.handler)

        vbox.Add(self.log, 1, wx.ALL | wx.EXPAND, 5)
        self.SetSizer(vbox)

        # Bind the panel destruction event to ensure cleanup
        self.Bind(wx.EVT_WINDOW_DESTROY, self.on_close)

    ## Handles the window destruction event.
    #  Performs cleanup actions when the panel is destroyed, removing and closing log handlers.
    #  @param event The window destruction event.
    def on_close(self, event):
        """ Perform any cleanup actions here """

        # Remove and close the custom handler
        self.logger.removeHandler(self.handler)
        self.handler.close()

        # Remove the file and stream handlers (optional if they need cleanup)
        for handler in self.logger.handlers[:]:
            if isinstance(handler, (logging.FileHandler, logging.StreamHandler)):
                self.logger.removeHandler(handler)
                handler.close()

        event.Skip()  # Ensure the event propagates to the parent if needed

    ## Destructor for the LogTab class.
    #  Ensures the logger handler is removed when the object is destroyed.
    def __del__(self):
        self.logger.removeHandler(self.handler)


    ## Updates the layout of the log tab.
    #  @details This tab only needs to layout and refresh as it doesn't have dynamic text.
    def refresh_gui_text(self):
        # This tab only needs to layout and refresh as it doesn't have dynamic text.
        self.Layout()
        self.Refresh()


## @class WxTextCtrlHandler
#  @brief Handler for log commands that redirects log messages to a wxPython text control.
#
#  The `WxTextCtrlHandler` class is a custom logging handler that redirects log messages
#  to a wxPython text control, allowing log messages to be displayed in the GUI.
class WxTextCtrlHandler(logging.Handler):
    ## Initializes the log handler.
    #  @param ctrl The wxPython text control to which log messages will be redirected.
    def __init__(self, ctrl):
        logging.Handler.__init__(self)
        self.ctrl = ctrl

    ## Emits a log record to the text control.
    #  @param record The log record to be emitted.
    #  @details Formats the log record and writes it to the text control using CallAfter to ensure thread safety.
    def emit(self, record):
        s = self.format(record) + '\n'
        wx.CallAfter(self.ctrl.WriteText, s)


## @class HelpPopUp
#  @brief A popup window for displaying documentation pages.
#
#  The `HelpPopUp` class creates a wxPython-based window that embeds multiple documentation
#  pages in a tabbed interface using `wx.Notebook` and `wx.html2.WebView`. It supports Markdown (`.md`) files
#  by converting them to HTML before rendering.
class HelpPopUp(wx.Frame):
    ## Initializes the documentation popup.
    #  @param parent The parent wx object.
    #  @param translator The translator object to determine the language.
    #  @param file_dir The directory containing the documentation files.
    def __init__(self, parent, translator, file_dir: Path):
        super(HelpPopUp, self).__init__(parent, title="Help", size=(900, 900))

        ## @var notebook
        #  A wx.Notebook widget containing different documentation tabs.
        notebook = wx.Notebook(self)

        # Get the selected language from the translator
        selected_language = translator.get_selected_language()

        # Determine the correct documentation subdirectory based on the language
        doc_dir = file_dir / "documentation" / selected_language

        ## @var dict_docu
        #  A dictionary mapping tab names to documentation file paths based on the selected language.
        dict_docu = {
            translator.translate('application'): doc_dir / "application.html",
            translator.translate('foundations'): doc_dir / "foundations.html",
            translator.translate('documentation_code'): doc_dir.parent / "code" / "html" / "index.html",
        }

        # Create tabs with embedded WebView for each documentation page
        for page_name, file_path in dict_docu.items():
            panel = wx.Panel(notebook)
            sizer = wx.BoxSizer(wx.VERTICAL)

            ## @var html_view
            #  A WebView widget to render the documentation content.
            html_view =wx.html2.WebView.New(panel)

            if file_path.suffix == ".html":
                if file_path.exists():
                    # Load URL correctly for local files
                    wx.CallAfter(html_view.LoadURL, str(file_path.resolve()))
                else:
                    html_view.SetPage(f"<h3>Error: File {file_path} not found!</h3>", "")
            else:
                html_view.SetPage("<h3>Error: Unsupported file format.</h3>", "")

            sizer.Add(html_view, 1, wx.EXPAND | wx.ALL, 5)
            panel.SetSizer(sizer)

            # Add the panel as a new tab
            notebook.AddPage(panel, page_name)

        ## @var main_sizer
        #  The main layout container for the popup window.
        main_sizer = wx.BoxSizer(wx.VERTICAL)
        main_sizer.Add(notebook, 1, wx.EXPAND)
        self.SetSizer(main_sizer)
        self.Show()



## @class LoadingSplash
#a loading screen to show the tool is responding
class LoadingSplash(wx.Frame):

    # ---- colours & geometry ------------------------------------------------
    _BG        = wx.Colour(28,  28,  34)
    _BAR_BG    = wx.Colour(55,  55,  65)
    _BAR_FG    = wx.Colour(70, 130, 220)
    _WHITE     = wx.Colour(255, 255, 255)
    _GREY      = wx.Colour(160, 160, 175)
    _HINT      = wx.Colour(100, 100, 115)

    W, H       = 440, 210
    BAR_W      = 360
    BAR_H      = 12
    BAR_X      = (W - BAR_W) // 2
    BAR_Y      = 138
    BLOCK_LEN  = 90
    STEP_PX    = 5

    ## Initializes and immediately paints the loading splash screen.
    def __init__(self):
        super().__init__(None,
                         style=wx.BORDER_NONE | wx.STAY_ON_TOP | wx.FRAME_NO_TASKBAR)
        self.SetClientSize((self.W, self.H))
        self.Centre()

        self._pos  = 0
        self._hint = "Starting up\u2026"

        self.Bind(wx.EVT_PAINT,            self._on_paint)
        self.Bind(wx.EVT_ERASE_BACKGROUND, lambda e: None)  # prevent grey flash


        self._timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._on_timer, self._timer)
        self._timer.Start(40)


        self.Show()
        self.Refresh()
        self.Update()
        wx.SafeYield()

    # -----------------------------------------------------------------------
    def _on_timer(self, _event):
        """Advance the bar position and request a repaint on every tick."""
        self._pos = (self._pos + self.STEP_PX) % (self.BAR_W + self.BLOCK_LEN)
        self.Refresh()
        self.Update()

    # -----------------------------------------------------------------------
    def _on_paint(self, _event):
        """Draw the entire splash content via DC """
        dc = wx.PaintDC(self)
        w  = self.W

        # Background
        dc.SetBackground(wx.Brush(self._BG))
        dc.Clear()

        # Title
        dc.SetFont(wx.Font(15, wx.FONTFAMILY_DEFAULT,
                           wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        dc.SetTextForeground(self._WHITE)
        title = "Direct Line Network Tool"
        dc.DrawText(title, (w - dc.GetTextExtent(title)[0]) // 2, 28)

        # Subtitle
        dc.SetFont(wx.Font(10, wx.FONTFAMILY_DEFAULT,
                           wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        dc.SetTextForeground(self._GREY)
        sub = "Initializing, please wait\u2026"
        dc.DrawText(sub, (w - dc.GetTextExtent(sub)[0]) // 2, 66)

        # Bar track
        dc.SetPen(wx.TRANSPARENT_PEN)
        dc.SetBrush(wx.Brush(self._BAR_BG))
        dc.DrawRoundedRectangle(self.BAR_X, self.BAR_Y, self.BAR_W, self.BAR_H, 6)

        # Animated gliding block
        block_x = self.BAR_X + self._pos - self.BLOCK_LEN
        clip_x  = max(block_x, self.BAR_X)
        clip_w  = min(block_x + self.BLOCK_LEN, self.BAR_X + self.BAR_W) - clip_x
        if clip_w > 0:
            dc.SetBrush(wx.Brush(self._BAR_FG))
            dc.DrawRoundedRectangle(clip_x, self.BAR_Y, clip_w, self.BAR_H, 6)

        # Hint text (current loading phase)
        dc.SetFont(wx.Font(8, wx.FONTFAMILY_DEFAULT,
                           wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        dc.SetTextForeground(self._HINT)
        dc.DrawText(self._hint,
                    (w - dc.GetTextExtent(self._hint)[0]) // 2,
                    self.BAR_Y + self.BAR_H + 14)



    #  @param text Short description of the current loading phase.
    def set_hint(self, text: str):
        self._hint = text
        self.Refresh()
        self.Update()

    ## Stops the timer and destroys the splash window.
    def close(self):
        self._timer.Stop()
        self.Destroy()


if __name__ == '__main__':
    # Initialize translator using PANDO's helper_files/translations.json (with local fallback)
    _translations_path = (
        (_PANDO_HELPER_DIR / "translations.json")
        if (_PANDO_HELPER_DIR is not None and (_PANDO_HELPER_DIR / "translations.json").is_file())
        else (Path(__file__).parent / "translations.json")
    )
    translator = Translator(_translations_path, language="en")
    app = wx.App()
    frame = DirectLineNetworkToolFrame(translator)
    app.MainLoop()