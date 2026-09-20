# -*- coding: utf-8 -*-
# EvaluaciónRSU — Plugin QGIS para la selección de sitios de disposición final de RSU
# Copyright (C) 2024-2025  Sergio López Olvera <lopezolverasergio@gmail.com>
# SPDX-License-Identifier: GPL-3.0-or-later
# Repository: https://github.com/sergio-lopez-olvera/evaluacion-rsu

"""Clase principal del plugin EvaluaciónRSU."""

import os

from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction


class EvaluacionRSUPlugin:
    """Plugin QGIS para evaluación multicriterio de sitios RSU."""

    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.action = None
        self.menu = "&Evaluación RSU"

    # ------------------------------------------------------------------
    # Ciclo de vida del plugin
    # ------------------------------------------------------------------

    def initGui(self):
        """Registra la acción en el menú y barra de herramientas de QGIS."""
        icon_path = os.path.join(self.plugin_dir, "icon.png")
        icon = QIcon(icon_path) if os.path.exists(icon_path) else QIcon()

        self.action = QAction(
            icon,
            "Selección de sitios RSU (NOM-083)",
            self.iface.mainWindow(),
        )
        self.action.setToolTip(
            "Evaluación multicriterio para selección de sitios de\n"
            "disposición final de RSU — NOM-083-SEMARNAT-2003"
        )
        self.action.triggered.connect(self.run)

        self.iface.addPluginToMenu(self.menu, self.action)
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        """Elimina la acción del menú y barra de herramientas."""
        self.iface.removePluginMenu(self.menu, self.action)
        self.iface.removeToolBarIcon(self.action)

    # ------------------------------------------------------------------
    # Ejecución
    # ------------------------------------------------------------------

    def run(self):
        """Abre el asistente de evaluación multicriterio."""
        from .gui.wizard import AsistenteEvaluacionRSU

        wizard = AsistenteEvaluacionRSU(self.iface, self.iface.mainWindow())
        wizard.exec_()
