# -*- coding: utf-8 -*-
# EvaluaciónRSU — Plugin QGIS para la selección de sitios de disposición final de RSU
# Copyright (C) 2024-2025  Sergio López Olvera <lopezolverasergio@gmail.com>
# SPDX-License-Identifier: GPL-3.0-or-later
# Repository: https://github.com/sergio-lopez-olvera/evaluacion-rsu

"""
Herramienta interactiva para dibujar el polígono del área de interés
directamente sobre el lienzo de QGIS.

Controles:
    Clic izquierdo          — añadir vértice
    Doble clic izquierdo    — cerrar y aceptar polígono
    Clic derecho            — cerrar y aceptar polígono
    Retroceso / Suprimir    — eliminar último vértice
    Enter                   — cerrar y aceptar polígono
    Escape                  — cancelar
"""

from __future__ import annotations

from qgis.core import QgsGeometry, QgsPointXY, QgsWkbTypes
from qgis.gui import QgsMapTool, QgsRubberBand
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor, QFont, QKeyEvent, QMouseEvent
from qgis.PyQt.QtWidgets import QLabel


class HerramientaDibujoPoligono(QgsMapTool):
    """QgsMapTool que captura un polígono por clics sobre el canvas de QGIS."""

    capturado = pyqtSignal(QgsGeometry)
    cancelado = pyqtSignal()

    # ── Estilos visuales ────────────────────────────────────────────────────
    _COLOR_RELLENO = QColor(46, 125, 50, 50)
    _COLOR_BORDE   = QColor(46, 125, 50, 220)
    _COLOR_CURSOR  = QColor(46, 125, 50, 100)
    _ANCHO_BORDE   = 2

    _HINT_INICIO = "🖱  Clic izquierdo para añadir el primer vértice"
    _HINT_UNO    = "🖱  Clic para añadir vértices  —  ⌫ Retroceso: deshacer  —  Esc: cancelar"
    _HINT_LISTO  = ("🖱  Clic: añadir vértice  —  "
                    "Doble clic / Clic derecho / Enter: cerrar polígono  —  "
                    "⌫ Retroceso: deshacer  —  Esc: cancelar")

    def __init__(self, canvas):
        super().__init__(canvas)
        self._canvas = canvas
        self._vertices: list[QgsPointXY] = []
        self._limpiado = False          # guard contra doble _limpiar()

        # Rubber band del polígono en construcción
        self._rubber_band = QgsRubberBand(canvas, QgsWkbTypes.PolygonGeometry)
        self._rubber_band.setColor(self._COLOR_BORDE)
        self._rubber_band.setFillColor(self._COLOR_RELLENO)
        self._rubber_band.setWidth(self._ANCHO_BORDE)

        # Línea punteada cursor → último vértice
        self._cursor_band = QgsRubberBand(canvas, QgsWkbTypes.LineGeometry)
        self._cursor_band.setColor(self._COLOR_CURSOR)
        self._cursor_band.setWidth(1)
        self._cursor_band.setLineStyle(Qt.DashLine)

        # Panel flotante de instrucciones
        self._lbl_hint = QLabel(canvas)
        self._lbl_hint.setWordWrap(True)
        self._lbl_hint.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._lbl_hint.setStyleSheet(
            "QLabel {"
            "  background-color: rgba(30, 30, 30, 210);"
            "  color: #FFFFFF;"
            "  border-radius: 6px;"
            "  padding: 8px 14px;"
            "  font-size: 12px;"
            "}"
        )
        f = QFont("Arial")
        f.setPixelSize(12)
        self._lbl_hint.setFont(f)
        self._actualizar_hint()
        self._lbl_hint.adjustSize()
        self._lbl_hint.move(12, 12)
        self._lbl_hint.show()
        self._lbl_hint.raise_()

        self.setCursor(Qt.CrossCursor)

    # ── Eventos del ratón ───────────────────────────────────────────────────

    def canvasMoveEvent(self, event: QMouseEvent):
        if not self._vertices or self._limpiado:
            return
        punto = self.toMapCoordinates(event.pos())
        self._cursor_band.reset(QgsWkbTypes.LineGeometry)
        self._cursor_band.addPoint(self._vertices[-1])
        self._cursor_band.addPoint(punto)
        self._cursor_band.show()

    def canvasPressEvent(self, event: QMouseEvent):
        if self._limpiado:
            return
        punto = self.toMapCoordinates(event.pos())
        if event.button() == Qt.LeftButton:
            self._vertices.append(punto)
            self._actualizar_rubber_band()
            self._actualizar_hint()
        elif event.button() == Qt.RightButton:
            self._finalizar()

    def canvasDoubleClickEvent(self, event: QMouseEvent):
        # canvasPressEvent ya añadió el primer clic del doble; solo cerramos
        if event.button() == Qt.LeftButton and not self._limpiado:
            self._finalizar()

    # ── Teclado ─────────────────────────────────────────────────────────────

    def keyPressEvent(self, event: QKeyEvent):
        if self._limpiado:
            return
        key = event.key()
        if key in (Qt.Key_Backspace, Qt.Key_Delete):
            if self._vertices:
                self._vertices.pop()
                self._actualizar_rubber_band()
                self._actualizar_hint()
        elif key == Qt.Key_Escape:
            self._limpiar()
            self.cancelado.emit()
        elif key in (Qt.Key_Return, Qt.Key_Enter):
            self._finalizar()
        else:
            super().keyPressEvent(event)

    # ── Helpers ─────────────────────────────────────────────────────────────

    def _actualizar_hint(self):
        n = len(self._vertices)
        if n == 0:
            texto = self._HINT_INICIO
        elif n < 3:
            texto = f"({n} vértice{'s' if n > 1 else ''})  " + self._HINT_UNO
        else:
            texto = f"({n} vértices)  " + self._HINT_LISTO
        self._lbl_hint.setText(texto)
        self._lbl_hint.adjustSize()

    def _actualizar_rubber_band(self):
        self._rubber_band.reset(QgsWkbTypes.PolygonGeometry)
        for v in self._vertices:
            self._rubber_band.addPoint(v)
        self._rubber_band.show()

    def _finalizar(self):
        """Construye la geometría y emite capturado. Requiere ≥ 3 vértices."""
        if len(self._vertices) < 3 or self._limpiado:
            return
        puntos = self._vertices + [self._vertices[0]]   # anillo cerrado
        geom = QgsGeometry.fromPolygonXY([puntos])
        self._limpiar()
        self.capturado.emit(geom)

    def _limpiar(self):
        """Elimina rubber bands y panel. Protegido contra llamadas dobles."""
        if self._limpiado:
            return
        self._limpiado = True
        try:
            self._rubber_band.reset(QgsWkbTypes.PolygonGeometry)
            self._cursor_band.reset(QgsWkbTypes.LineGeometry)
        except Exception:
            pass
        self._vertices.clear()
        try:
            self._lbl_hint.hide()
            self._lbl_hint.deleteLater()
        except Exception:
            pass

    # ── Ciclo de vida ───────────────────────────────────────────────────────

    def deactivate(self):
        """QGIS llama esto al cambiar de herramienta; garantizamos limpieza."""
        self._limpiar()
        super().deactivate()
