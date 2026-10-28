import sys
import os
import shutil  # [ÚJ] Ez végzi majd a fájlmásolást
import pymupdf as fitz
from PySide6.QtWidgets import QApplication, QLabel, QScrollArea, QSplitter, QFileDialog, QTreeView, QFileSystemModel # <-- Ide került át!
from PySide6.QtCore import QFile, QTimer, QProcess, QObject, QEvent, Qt
from PySide6.QtGui import QImage, QPixmap, QAction, QPainter # <-- Innen pedig kikerült
from PySide6.QtUiTools import QUiLoader




class MainWindow(QObject):
    def __init__(self):
        super().__init__()
        self.current_file_path = None  # [ÚJ] Ebbe mentjük el, hogy épp melyik fájlon dolgozunk
        loader = QUiLoader()
        ui_file_path = os.path.join(os.path.dirname(__file__), "form.ui")
        ui_file = QFile(ui_file_path)

        if not ui_file.open(QFile.ReadOnly):
            print(f"Hiba: Nem sikerült megnyitni a fájlt: {ui_file_path}")
            sys.exit(-1)

        self.ui = loader.load(ui_file)
        ui_file.close()

        # ==========================================
        # 1. ABLAKOK FELOSZTÁSA (Fájlfa | Kód | PDF)
        # ==========================================
        splitter = self.ui.findChild(QSplitter)
        if splitter:
            # 3 elemünk van a splitterben.
            # Ha az összeget 1000-nek vesszük, könnyű százalékban számolni:
            # 150 = 15% a fájlfának, 350 = 35% a kódnak, 500 = 50% a PDF-nek
            splitter.setSizes([150, 350, 500])

        self.compile_timer = QTimer(self.ui)
        self.compile_timer.setSingleShot(True)
        self.compile_timer.setInterval(1000)
        self.compiler_process = QProcess(self.ui)

        self.ui.editorTextEdit.textChanged.connect(self.restart_timer)
        self.compile_timer.timeout.connect(self.start_compilation)
        self.compiler_process.finished.connect(self.on_compilation_finished)

        # Nagyítás és mozgatás változói
        self.current_zoom = 2.0
        self.pan_active = False
        self.last_mouse_pos = None

        self.pdf_label = self.ui.findChild(QLabel, "pdfLabel")

        # ==========================================
        # [ÚJ] 2. NÉV NÉLKÜLI SCROLLAREA KERESÉS
        # ==========================================
        # Így 100%, hogy megtalálja, akármi is a neve a Qt Designerben
        self.scroll_area = self.ui.findChild(QScrollArea)

        if self.pdf_label:
            self.pdf_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        if self.scroll_area:
            # [ÚJ] Ez a két sor garantálja, hogy kisebb zoom esetén is középen maradjon a lap!
            self.scroll_area.setWidgetResizable(True)
            self.scroll_area.setAlignment(Qt.AlignmentFlag.AlignCenter)

            # A viewport figyelése marad:
            self.scroll_area.viewport().installEventFilter(self)
            # ==========================================
            # MENÜ MŰVELETEK BEKÖTÉSE
            # ==========================================

            # 1. Megnyitás gomb
            self.action_open = self.ui.findChild(QAction, "actionOpen")
            if self.action_open:
                self.action_open.triggered.connect(self.open_file)

            # 2. Sima Mentés gomb (Mentés, ha már van útvonal, különben Mentés másként-et hív)
            self.action_save = self.ui.findChild(QAction, "actionSave")
            if self.action_save:
                self.action_save.triggered.connect(self.save_file)

            # 3. Mentés másként gomb (Mindig feldobja a fájlkezelőt)
            self.action_save_as = self.ui.findChild(QAction, "actionSaveAs")
            if self.action_save_as:
                self.action_save_as.triggered.connect(self.save_as_file)
            # 4. PDF Exportálás gomb
            self.action_export = self.ui.findChild(QAction, "actionExport")
            if self.action_export:
                self.action_export.triggered.connect(self.export_pdf)
            # 5. Mappa megnyitása gomb
            self.action_open_folder = self.ui.findChild(QAction, "actionOpenFolder")
            if self.action_open_folder:
                self.action_open_folder.triggered.connect(self.open_folder)

            # ==========================================
            # [ÚJ] FÁJLFA (PROJECT EXPLORER) BEÁLLÍTÁSA
            # ==========================================
            self.file_tree = self.ui.findChild(QTreeView, "fileTreeView")

            # Alapértelmezettként a 'main.tex' van nyitva
            # [IDE KERÜL] Itt adjuk meg a kezdő útvonalakat teljes elérési úttal!
            self.project_path = os.getcwd()
            self.current_file_path = os.path.join(self.project_path, "main.tex")

            if self.file_tree:
                self.file_model = QFileSystemModel()
                self.file_model.setRootPath(self.project_path)

                self.file_tree.setModel(self.file_model)
                self.file_tree.setRootIndex(self.file_model.index(self.project_path))

                # Elrejtjük a "Méret", "Típus", és "Dátum" oszlopokat, hogy csak a név látsszon
                self.file_tree.setColumnHidden(1, True)
                self.file_tree.setColumnHidden(2, True)
                self.file_tree.setColumnHidden(3, True)

                # Ha rákattintasz egy fájlra, hívja meg a megnyitó függvényt
                self.file_tree.doubleClicked.connect(self.on_tree_clicked)

    def eventFilter(self, obj, event):
            # Most már a teljes jobb oldali dobozt figyeljük
            if self.scroll_area and obj == self.scroll_area.viewport():

                # 1. Mozgatás kezdete
                if event.type() == QEvent.Type.MouseButtonPress:
                    if event.button() == Qt.MouseButton.LeftButton:
                        self.pan_active = True
                        self.last_mouse_pos = event.globalPosition().toPoint()
                        # A kurzort a viewporton változtatjuk
                        self.scroll_area.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
                        return True

                # 2. Mozgatás (Drag)
                elif event.type() == QEvent.Type.MouseMove:
                    if self.pan_active:
                        current_pos = event.globalPosition().toPoint()
                        delta = current_pos - self.last_mouse_pos

                        h_bar = self.scroll_area.horizontalScrollBar()
                        v_bar = self.scroll_area.verticalScrollBar()

                        h_bar.setValue(h_bar.value() - delta.x())
                        v_bar.setValue(v_bar.value() - delta.y())

                        self.last_mouse_pos = current_pos
                        return True

                # 3. Mozgatás vége
                elif event.type() == QEvent.Type.MouseButtonRelease:
                    if event.button() == Qt.MouseButton.LeftButton:
                        self.pan_active = False
                        self.scroll_area.viewport().setCursor(Qt.CursorShape.ArrowCursor)
                        return True

                # 4. Görgő (Nagyítás) - Ez továbbra is a Ctrl gombra működik
                elif event.type() == QEvent.Type.Wheel:
                    if event.modifiers() == Qt.KeyboardModifier.ControlModifier:
                        angle = event.angleDelta().y()
                        if angle > 0:
                            self.current_zoom *= 1.25
                        else:
                            self.current_zoom /= 1.25

                        self.current_zoom = max(0.5, min(self.current_zoom, 5.0))
                        self.render_pdf()
                        return True

            return super().eventFilter(obj, event)

    def restart_timer(self):
        self.compile_timer.start()

    def save_current_file_silently(self):
            """Mentés a háttérben abba a fájlba, ami épp meg van nyitva."""
            if self.current_file_path:
                try:
                    latex_code = self.ui.editorTextEdit.toPlainText()
                    with open(self.current_file_path, "w", encoding="utf-8") as f:
                        f.write(latex_code)
                except Exception as e:
                    print(f"Hiba a fájl automatikus mentésekor: {e}")

    def start_compilation(self):
        """Amikor megállsz a gépeléssel, ez fut le."""
        if self.compiler_process.state() == QProcess.Running:
            self.compiler_process.kill()
            self.compiler_process.waitForFinished()

        # 1. Elmentjük azt a fájlt, amit ÉPPEN szerkesztesz (pl. fejezet1.tex vagy my_style.sty)
        self.save_current_file_silently()

        # 2. Ellenőrizzük, hogy létezik-e a main.tex
        # Overleaf szabály: mindig a main.tex a fordítás kiindulópontja!
        main_tex_path = os.path.join(self.project_path, "main.tex")
        if not os.path.exists(main_tex_path):
            print("Hiba: Nem található 'main.tex' a mappában! A fordításhoz lennie kell egy fő fájlnak.")
            return

        print("Fordítás indítása (main.tex)...")
        args = ["-interaction=nonstopmode", "-halt-on-error", "main.tex"]
        # FONTOS: Beállítjuk a munkakönyvtárat a projekt mappájára, hogy megtalálja a képeket!
        self.compiler_process.setWorkingDirectory(self.project_path)
        self.compiler_process.start("pdflatex", args)
    def on_compilation_finished(self, exit_code, exit_status):
        if exit_code == 0:
            self.render_pdf()
        else:
            print("Hiba történt a fordítás során (szintaktikai hiba).")

    def render_pdf(self):
            pdf_path = os.path.join(self.project_path, "main.pdf")
            if not os.path.exists(pdf_path):
                return

            try:
                doc = fitz.open(pdf_path)

                if doc.page_count == 0:
                    doc.close()
                    return

                matrix = fitz.Matrix(self.current_zoom, self.current_zoom)

                # ==========================================
                # [ÚJ] TÖBB OLDAL FELDOLGOZÁSA ÉS ÖSSZEFŰZÉSE
                # ==========================================
                qimages = []
                total_height = 0
                max_width = 0

                # 20 pixeles rés az oldalak között, ami a zoommal együtt nő
                padding = int(20 * self.current_zoom)

                # 1. Végigmegyünk a PDF összes oldalán
                for page_num in range(doc.page_count):
                    page = doc.load_page(page_num)
                    pix = page.get_pixmap(matrix=matrix)
                    qimg = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888)
                    qimages.append(qimg)

                    # Számoljuk a végső "óriáskép" méreteit
                    max_width = max(max_width, qimg.width())
                    total_height += qimg.height() + padding

                # Az utolsó oldal után nem kell üres hely (padding)
                if doc.page_count > 0:
                    total_height -= padding

                # 2. Létrehozunk egy óriási, üres vásznat, amire ráfér az összes lap
                combined_pixmap = QPixmap(max_width, total_height)
                combined_pixmap.fill(Qt.GlobalColor.transparent) # A lapok között átlátszó (szürke) lesz

                # 3. Ráfestjük a lapokat az óriási vászonra, szépen egymás alá
                painter = QPainter(combined_pixmap)
                current_y = 0
                for qimg in qimages:
                    # Vízszintesen középre igazítjuk a lapot
                    x_offset = (max_width - qimg.width()) // 2
                    painter.drawImage(x_offset, current_y, qimg)
                    current_y += qimg.height() + padding
                painter.end() # Befejezzük a festést

                # ==========================================
                # 4. AZ ÓRIÁSKÉP MEGJELENÍTÉSE
                # ==========================================
                if self.pdf_label is not None:
                    self.pdf_label.setPixmap(combined_pixmap)

                    # Fixáljuk a méretet az eddigiek alapján, de most az óriáskép méretére!
                    self.pdf_label.setFixedSize(combined_pixmap.size())

                    if self.scroll_area and self.scroll_area.widget():
                        self.scroll_area.widget().setFixedSize(combined_pixmap.size())

                doc.close()
            except Exception as e:
                print(f"Hiba a PDF kirajzolásakor: {e}")
    def save_file(self):
            """Sima mentés: ha már van fájlunk, csendben felülírja. Ha nincs, kér egyet."""
            # Ha már tudjuk, hova kell menteni (mert megnyitottuk, vagy egyszer már elmentettük)
            if self.current_file_path:
                try:
                    latex_code = self.ui.editorTextEdit.toPlainText()
                    with open(self.current_file_path, "w", encoding="utf-8") as f:
                        f.write(latex_code)
                    print(f"Fájl csendben frissítve: {self.current_file_path}")
                except Exception as e:
                    print(f"Hiba a fájl mentésekor: {e}")
            else:
                # Ha még sosem volt elmentve ez a dokumentum, akkor fel kell dobni a fájlkezelőt
                self.save_as_file()

    def save_as_file(self):
            """Mentés másként: mindig feldobja a fájlkezelőt."""
            file_path, _ = QFileDialog.getSaveFileName(
                self.ui,
                "LaTeX kód mentése másként",
                self.project_path,
                "LaTeX fájlok (*.tex);;Minden fájl (*)"
            )

            if file_path:
                # [ÚJ] Megjegyezzük az új útvonalat
                self.current_file_path = file_path

                # És miután megvan az új útvonal, simán meghívjuk az okos mentést, ami kiírja a fájlt
                self.save_file()
    def open_file(self):
            file_path, _ = QFileDialog.getOpenFileName(
                self.ui,
                "LaTeX kód megnyitása",
                self.project_path,  # <--- EZ VOLT A HIBA: "" helyett self.project_path kell!
                "LaTeX fájlok (*.tex);;Minden fájl (*)"
             )

            if file_path:
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        latex_code = f.read()

                    self.ui.editorTextEdit.setPlainText(latex_code)

                    # [ÚJ] Megjegyezzük, hogy mostantól ez az aktuális fájlunk!
                    self.current_file_path = file_path

                    print(f"Fájl sikeresen betöltve: {file_path}")
                except Exception as e:
                    print(f"Hiba a fájl megnyitásakor: {e}")
    def export_pdf(self):
            """Kimenti a háttérben legenerált main.pdf-et a kért helyre."""
            source_pdf = os.path.join(self.project_path, "main.pdf")

            # 1. Ellenőrizzük, hogy van-e egyáltalán mit exportálni
            # (Előfordulhat, hogy még nem gépelt semmit, vagy hibás a kód és nem generálódott le a PDF)
            if not os.path.exists(source_pdf):
                print("Nincs mit exportálni. Kérlek, várj amíg lefordul a dokumentum!")
                return

            # 2. Fájlmentő ablak megnyitása, kifejezetten PDF kiterjesztéssel
            file_path, _ = QFileDialog.getSaveFileName(
                self.ui,
                "PDF Exportálása",
                "",
                "PDF fájlok (*.pdf);;Minden fájl (*)"
            )

            # 3. Fájl átmásolása a végleges helyére
            if file_path:
                try:
                    # A shutil.copy2 fogja az ideiglenes main.pdf-ünket, és létrehoz belőle
                    # egy tökéletes másolatot azon a néven és helyen, amit megadtál.
                    shutil.copy2(source_pdf, file_path)
                    print(f"PDF sikeresen exportálva ide: {file_path}")
                except Exception as e:
                    print(f"Hiba a PDF exportálásakor: {e}")
    def open_folder(self):
            """Megnyit egy kiválasztott mappát (projektet), és betölti a fájlfába."""

            # 1. Mappaválasztó ablak megnyitása
            folder_path = QFileDialog.getExistingDirectory(
                self.ui,
                "Projekt mappa kiválasztása",
                self.project_path # A jelenlegi mappából indul
            )

            # Ha választott mappát (nem nyomott Mégse-t)
            if folder_path:
                # 2. Átállítjuk a belső projekt útvonalat
                self.project_path = folder_path

                # 3. Frissítjük a bal oldali fájlfát, hogy az új mappát mutassa!
                self.file_model.setRootPath(self.project_path)
                self.file_tree.setRootIndex(self.file_model.index(self.project_path))

                # 4. Megpróbáljuk automatikusan megnyitni a 'main.tex'-et az új mappában
                main_tex_path = os.path.join(self.project_path, "main.tex")
                if os.path.exists(main_tex_path):
                    # Ha van main.tex, betöltjük
                    with open(main_tex_path, "r", encoding="utf-8") as f:
                        content = f.read()

                    # Némán töltjük be, nehogy fals fordítást indítson azonnal
                    self.ui.editorTextEdit.blockSignals(True)
                    self.ui.editorTextEdit.setPlainText(content)
                    self.ui.editorTextEdit.blockSignals(False)

                    self.current_file_path = main_tex_path

                    # És manuálisan indítunk egy fordítást, hogy megjelenjen a PDF
                    self.start_compilation()
                else:
                    # Ha üres a mappa (nincs main.tex), kiürítjük a szerkesztőt
                    self.ui.editorTextEdit.blockSignals(True)
                    self.ui.editorTextEdit.clear()
                    self.ui.editorTextEdit.blockSignals(False)
                    self.current_file_path = None

                    # És letöröljük a jobb oldali PDF előnézetet
                    if self.pdf_label:
                        self.pdf_label.clear()
                    print("Az új mappában nem található 'main.tex' fájl.")

                print(f"Új projekt mappa megnyitva: {self.project_path}")
    def on_tree_clicked(self, index):
            """Ha a fájlfában egy fájlra kattintanak, megnyitjuk a szerkesztőben."""
            clicked_path = self.file_model.filePath(index)

            # Csak ha fájl (nem mappa), és szerkeszthető szöveges kiterjesztés
            if os.path.isfile(clicked_path):
                valid_extensions = ['.tex', '.sty', '.cls', '.bib']
                if any(clicked_path.endswith(ext) for ext in valid_extensions):

                    # Mielőtt váltunk, kimentjük az aktuális tartalmat a régi fájlba
                    if self.current_file_path:
                        self.save_current_file_silently()

                    # Betöltjük az újat
                    with open(clicked_path, "r", encoding="utf-8") as f:
                        content = f.read()

                    # Kikapcsoljuk a textChanged szignált arra a pillanatra, amíg betöltjük,
                    # hogy ne indítson el rögtön egy fals újrafordítást
                    self.ui.editorTextEdit.blockSignals(True)
                    self.ui.editorTextEdit.setPlainText(content)
                    self.ui.editorTextEdit.blockSignals(False)

                    # Frissítjük, hogy mostantól ezt a fájlt szerkesztjük
                    self.current_file_path = clicked_path
                    print(f"Szerkesztés alatt: {os.path.basename(self.current_file_path)}")
    def show(self):
        self.ui.show()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
