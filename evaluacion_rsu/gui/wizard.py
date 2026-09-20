# -*- coding: utf-8 -*-
# EvaluaciónRSU — Plugin QGIS para la selección de sitios de disposición final de RSU
# Copyright (C) 2024-2025  Sergio López Olvera <lopezolverasergio@gmail.com>
# SPDX-License-Identifier: GPL-3.0-or-later
# Repository: https://github.com/sergio-lopez-olvera/evaluacion-rsu

"""
Interfaz gráfica del plugin EvaluaciónRSU.

Asistente multi-paso implementado como QDialog con QStackedWidget.
Páginas:
  0 — Paso 1: Bienvenida y selección del área de interés (AOI)
  1 — Paso 2: Selección de criterios activos, modo y rol
  2 — Paso 3: Carga / descarga de datos por criterio
  3 — Paso 4: Configuración del análisis de exclusión
  4 — Paso 5: Configuración de ponderación
  5 — Paso 6: Mapeo de atributos para capas cargadas manualmente
  6 — Paso 7: Ejecución y resultados
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Optional

from qgis.core import (
    QgsMapLayer,
    QgsMapLayerProxyModel,
    QgsProject,
    QgsVectorLayer,
    QgsVectorLayerUtils,
    QgsWkbTypes,
)
from qgis.gui import QgsMapLayerComboBox
from qgis.PyQt.QtCore import Qt, QThread, QTimer, QUrl, pyqtSignal
from qgis.PyQt.QtGui import QColor, QDesktopServices, QFont, QIcon
from qgis.PyQt.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpacerItem,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..core.analisis import MotorAnalisis
from ..core.criterios import CRITERIOS_DEFAULT, Criterio, FuenteDatos
from ..core.descargador import Descargador
from ..utils.estilos import (
    aplicar_estilo_aptas,
    aplicar_estilo_criterio as _aplicar_estilo_criterio,
    aplicar_estilo_excluidas,
    aplicar_pseudocolor_aptitud,
)


# ---------------------------------------------------------------------------
# Estilos y constantes
# ---------------------------------------------------------------------------

COLOR_PRIMARIO  = "#2E7D32"   # verde oscuro
COLOR_SECUNDARIO = "#388E3C"
COLOR_ACENTO    = "#66BB6A"
COLOR_FONDO     = "#F5F5F5"
COLOR_EXCLUIDO  = "#D32F2F"   # rojo — zona excluida
COLOR_APTO      = "#388E3C"   # verde — zona apta

# ── Paleta neutra (slate) ───────────────────────────────────────────────────
# Base cromática para acciones secundarias, encabezados de tabla y estados
# informativos. Sustituye los antiguos tonos naranja.
COLOR_SLATE       = "#546E7A"   # acción secundaria
COLOR_SLATE_HOVER = "#37474F"
COLOR_SLATE_TEXTO = "#455A64"   # texto de estado / etiquetas
COLOR_TEXTO       = "#263238"   # texto principal
COLOR_TEXTO_SUAVE = "#78868F"   # texto terciario / valores de referencia
COLOR_BORDE       = "#DADEE2"   # líneas y bordes sutiles
COLOR_CABECERA    = "#F4F6F7"   # fondo de encabezado de tabla
COLOR_FILA_ALT    = "#FAFBFC"   # fondo de fila alterna
COLOR_INFO        = "#1565C0"   # azul informativo (descargas, progreso)
COLOR_INFO_HOVER  = "#0D47A1"

# ── Escala tipográfica ──────────────────────────────────────────────────────
# Un solo lugar donde ajustar tamaños; todas las páginas la consumen.
FS_TITULO   = 16   # título de paso
FS_CUERPO   = 12   # texto de fila / descripciones principales
FS_META     = 11   # metadatos, fuentes, tipos
FS_MICRO    = 10   # encabezados de columna, notas, badges

ESTILO_TITULO = (
    f"font-size: {FS_TITULO}px; font-weight: bold; color: #1B5E20; "
    "margin-bottom: 4px;"
)
ESTILO_SUBTITULO = f"font-size: {FS_CUERPO}px; color: #33691E;"
ESTILO_NOTA = (
    f"font-size: {FS_MICRO}px; color: {COLOR_SLATE_TEXTO}; font-style: italic;"
)
ESTILO_BOTON_PRIMARIO = (
    f"QPushButton {{ background-color: {COLOR_PRIMARIO}; color: white; "
    "border: none; border-radius: 4px; padding: 6px 16px; font-weight: bold; }}"
    f"QPushButton:hover {{ background-color: {COLOR_SECUNDARIO}; }}"
    "QPushButton:disabled { background-color: #B0BEC5; }"
)
ESTILO_BOTON_SECUNDARIO = (
    f"QPushButton {{ background-color: {COLOR_SLATE}; color: white; "
    f"border: none; border-radius: 4px; padding: 5px 12px; "
    f"font-size: {FS_META}px; }}"
    f"QPushButton:hover {{ background-color: {COLOR_SLATE_HOVER}; }}"
    "QPushButton:disabled { background-color: #B0BEC5; }"
)
ESTILO_BOTON_CONTORNO = (
    f"QPushButton {{ background: white; color: {COLOR_SLATE_TEXTO}; "
    f"border: 1px solid {COLOR_BORDE}; border-radius: 4px; padding: 4px 12px; "
    f"font-size: {FS_META}px; }}"
    f"QPushButton:hover {{ background: {COLOR_CABECERA}; "
    f"border-color: {COLOR_SLATE}; color: {COLOR_SLATE_HOVER}; }}"
    "QPushButton:disabled { color: #B0BEC5; border-color: #ECEFF1; }"
)
ESTILO_BOTON_INFO = (
    f"QPushButton {{ background-color: {COLOR_INFO}; color: white; "
    f"border: none; border-radius: 4px; padding: 4px 12px; "
    f"font-size: {FS_META}px; }}"
    f"QPushButton:hover {{ background-color: {COLOR_INFO_HOVER}; }}"
)
ESTILO_CABECERA_COL = (
    f"color: {COLOR_SLATE_TEXTO}; font-size: {FS_MICRO}px; font-weight: bold; "
    "background: transparent;"
)
ESTILO_CHIP_NORMA = (
    "background: #E8F1E9; color: #1B5E20; border: 1px solid #C3DEC6; "
    "border-radius: 9px; padding: 1px 7px; font-size: 9px; font-weight: bold;"
)


# ---------------------------------------------------------------------------
# Hilo de descarga
# ---------------------------------------------------------------------------

class HiloDescarga(QThread):
    progreso = pyqtSignal(int, str)
    terminado = pyqtSignal()

    def __init__(self, descargador: Descargador, criterios: list[Criterio]):
        super().__init__()
        self.descargador = descargador
        self.criterios = criterios

    def run(self):
        self.descargador.progreso.connect(self.progreso)
        self.descargador.descargar_todos(self.criterios)
        self.terminado.emit()


# ---------------------------------------------------------------------------
# Hilo de análisis
# ---------------------------------------------------------------------------

class HiloAnalisis(QThread):
    progreso = pyqtSignal(int, str)
    fase1_lista = pyqtSignal(object, object, object)  # (excluida, apta, dict_criterios)
    fase2_lista = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, motor: MotorAnalisis):
        super().__init__()
        self.motor = motor

    def run(self):
        self.motor.progreso.connect(self.progreso)
        self.motor.fase1_lista.connect(self.fase1_lista)
        self.motor.fase2_lista.connect(self.fase2_lista)
        self.motor.error.connect(self.error)

        capa_excluida, capa_apta = self.motor.ejecutar_fase1()
        if capa_apta and self.motor.modo_analisis != "dicotomico":
            self.motor.ejecutar_fase2(capa_apta)


# ---------------------------------------------------------------------------
# Widget auxiliares
# ---------------------------------------------------------------------------

def _separador() -> QFrame:
    sep = QFrame()
    sep.setFrameShape(QFrame.HLine)
    sep.setFrameShadow(QFrame.Sunken)
    sep.setStyleSheet(f"color: {COLOR_BORDE};")
    return sep


def _etiqueta_titulo(texto: str) -> QLabel:
    lbl = QLabel(texto)
    lbl.setStyleSheet(ESTILO_TITULO)
    lbl.setWordWrap(True)
    return lbl


def _etiqueta_nota(texto: str) -> QLabel:
    lbl = QLabel(texto)
    lbl.setStyleSheet(ESTILO_NOTA)
    lbl.setWordWrap(True)
    return lbl


def _etiqueta_cuerpo(texto: str) -> QLabel:
    """Párrafo introductorio de una página, en la escala tipográfica del asistente."""
    lbl = QLabel(texto)
    lbl.setStyleSheet(
        f"font-size: {FS_CUERPO}px; color: {COLOR_TEXTO};"
    )
    lbl.setWordWrap(True)
    return lbl


def _etiqueta_seccion(texto: str) -> QLabel:
    """Rótulo de un bloque de controles dentro de una página."""
    lbl = QLabel(texto.upper())
    lbl.setStyleSheet(
        f"font-size: {FS_MICRO}px; font-weight: bold; color: {COLOR_SLATE_TEXTO};"
    )
    return lbl


# ---------------------------------------------------------------------------
# Sistema de tabla compartido
#
# Todas las tablas del asistente (Criterios, Datos, Exclusión, Ponderación,
# Mapeo) usan estos tres bloques para verse idénticas:
#
#   _cabecera_tabla(cols)  → fila de encabezado, va DENTRO del QScrollArea
#   _fila_tabla(indice)    → contenedor de fila con fondo alterno
#   _celda(...)            → etiqueta de celda con ancho fijo y elisión
#
# Las columnas se declaran como (texto, ancho, alineación). El espacio
# sobrante se envía al final de la fila con addStretch(1), de modo que las
# columnas de datos queden contiguas al nombre y no empujadas al borde.
# ---------------------------------------------------------------------------

ESPACIO_COL = 10   # separación uniforme entre columnas


def _ancho_texto(metricas, texto: str) -> int:
    """Ancho en píxeles de un texto, compatible con Qt < 5.11."""
    try:
        return metricas.horizontalAdvance(texto)
    except AttributeError:
        return metricas.width(texto)


def _cabecera_tabla(columnas: list[tuple]) -> QWidget:
    """Construye la fila de encabezado de una tabla.

    columnas: lista de (texto, ancho | None, alineación). Un ancho None deja
    que la columna tome su tamaño natural.
    """
    w = QWidget()
    w.setStyleSheet(
        f"background: {COLOR_CABECERA}; "
        f"border-top: 1px solid {COLOR_BORDE}; "
        f"border-bottom: 1px solid {COLOR_BORDE};"
    )
    lay = QHBoxLayout(w)
    lay.setContentsMargins(8, 7, 8, 7)
    lay.setSpacing(ESPACIO_COL)

    for texto, ancho, alineacion in columnas:
        lbl = QLabel(texto.upper())
        lbl.setStyleSheet(ESTILO_CABECERA_COL)
        lbl.setAlignment(alineacion | Qt.AlignVCenter)
        if ancho is not None:
            lbl.setFixedWidth(ancho)
        lay.addWidget(lbl)

    lay.addStretch(1)
    return w


def _fila_tabla(indice: int) -> tuple[QWidget, QHBoxLayout]:
    """Crea el contenedor de una fila de tabla con fondo alterno.

    La regla de estilo se acota con un objectName para que NO se propague a
    los widgets hijos: una hoja de estilo sin acotar sobre el padre obliga a
    Qt a dibujar los QCheckBox con su motor de hojas de estilo, que pierde el
    recuadro nativo del indicador y deja solo la palomita suelta.
    """
    w = QWidget()
    if indice % 2 == 1:
        w.setObjectName("filaTablaAlt")
        w.setStyleSheet(f"QWidget#filaTablaAlt {{ background: {COLOR_FILA_ALT}; }}")
    lay = QHBoxLayout(w)
    lay.setContentsMargins(8, 4, 8, 4)
    lay.setSpacing(ESPACIO_COL)
    return w, lay


# ── Indicador de casilla de verificación ────────────────────────────────────
# Qt no permite dibujar una palomita dentro de un ::indicator personalizado sin
# una imagen, así que se generan los dos estados como PNG una sola vez y se
# referencian desde la hoja de estilo. Esto garantiza un recuadro visible en
# cualquier plataforma, en lugar de depender del estilo nativo.

_CACHE_ESTILO_CHK: Optional[str] = None


def _estilo_checkbox() -> str:
    """Hoja de estilo con indicador de casilla dibujado en tiempo de ejecución."""
    global _CACHE_ESTILO_CHK
    if _CACHE_ESTILO_CHK is not None:
        return _CACHE_ESTILO_CHK

    try:
        import tempfile
        from qgis.PyQt.QtCore import QRectF, QPointF
        from qgis.PyQt.QtGui import QPainter, QPen, QPixmap

        lado = 32          # se dibuja a 2× y se escala: bordes nítidos en HiDPI
        radio = 7.0
        dir_tmp = Path(tempfile.gettempdir()) / "evaluacion_rsu_ui"
        dir_tmp.mkdir(parents=True, exist_ok=True)
        rutas = {}

        for estado in ("vacio", "marcado"):
            pm = QPixmap(lado, lado)
            pm.fill(QColor(0, 0, 0, 0))
            p = QPainter(pm)
            p.setRenderHint(QPainter.Antialiasing, True)
            caja = QRectF(2.0, 2.0, lado - 4.0, lado - 4.0)

            if estado == "marcado":
                p.setBrush(QColor(COLOR_PRIMARIO))
                p.setPen(QPen(QColor(COLOR_PRIMARIO), 2.0))
                p.drawRoundedRect(caja, radio, radio)
                # Palomita
                p.setPen(QPen(QColor("#FFFFFF"), 4.0))
                p.drawPolyline(
                    QPointF(lado * 0.27, lado * 0.52),
                    QPointF(lado * 0.44, lado * 0.69),
                    QPointF(lado * 0.74, lado * 0.32),
                )
            else:
                p.setBrush(QColor("#FFFFFF"))
                p.setPen(QPen(QColor("#AEB8C0"), 2.0))
                p.drawRoundedRect(caja, radio, radio)

            p.end()
            ruta = dir_tmp / f"chk_{estado}.png"
            pm.save(str(ruta), "PNG")
            # Qt exige separadores '/' en las URL de las hojas de estilo
            rutas[estado] = str(ruta).replace("\\", "/")

        _CACHE_ESTILO_CHK = (
            "QCheckBox { background: transparent; spacing: 6px; }"
            "QCheckBox::indicator { width: 16px; height: 16px; }"
            f"QCheckBox::indicator:unchecked {{ image: url({rutas['vacio']}); }}"
            f"QCheckBox::indicator:checked {{ image: url({rutas['marcado']}); }}"
        )
    except Exception:  # noqa: BLE001 — si falla, se usa el indicador nativo
        _CACHE_ESTILO_CHK = ""

    return _CACHE_ESTILO_CHK


def _casilla(marcada: bool = False, tooltip: str = "") -> QCheckBox:
    """QCheckBox con recuadro visible y sin texto, para celdas de tabla."""
    chk = QCheckBox()
    chk.setChecked(marcada)
    chk.setStyleSheet(_estilo_checkbox())
    chk.setCursor(Qt.PointingHandCursor)
    if tooltip:
        chk.setToolTip(tooltip)
    return chk


def _celda_casilla(chk: QCheckBox, ancho: int) -> QWidget:
    """Envuelve una casilla en una celda de ancho fijo, centrada."""
    cont = QWidget()
    cont.setObjectName("celdaCasilla")
    cont.setFixedWidth(ancho)
    cont.setStyleSheet("QWidget#celdaCasilla { background: transparent; }")
    lay = QHBoxLayout(cont)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addStretch()
    lay.addWidget(chk)
    lay.addStretch()
    return cont


def _toggle_todos(texto: str = "Seleccionar todos / Deseleccionar todos") -> QCheckBox:
    """Casilla maestra de selección, con texto y recuadro visible."""
    chk = QCheckBox(texto)
    chk.setChecked(True)
    chk.setTristate(False)
    chk.setCursor(Qt.PointingHandCursor)
    chk.setStyleSheet(
        _estilo_checkbox()
        + f"QCheckBox {{ font-size: {FS_META}px; color: {COLOR_SLATE_TEXTO}; }}"
    )
    return chk


def _celda(
    texto: str,
    ancho: Optional[int] = None,
    alineacion=Qt.AlignLeft,
    color: str = COLOR_TEXTO,
    tamano: int = FS_CUERPO,
    tooltip: str = "",
    elidir: bool = True,
) -> QLabel:
    """Etiqueta de celda: ancho fijo, fondo transparente y elisión opcional."""
    lbl = QLabel(texto)
    lbl.setStyleSheet(
        f"background: transparent; color: {color}; font-size: {tamano}px;"
    )
    lbl.setAlignment(alineacion | Qt.AlignVCenter)
    lbl.setWordWrap(False)
    if tooltip:
        lbl.setToolTip(tooltip)
    if ancho is not None:
        lbl.setFixedWidth(ancho)
        if elidir and _ancho_texto(lbl.fontMetrics(), texto) > ancho - 4:
            lbl.setText(
                lbl.fontMetrics().elidedText(texto, Qt.ElideRight, ancho - 4)
            )
            if not tooltip:
                lbl.setToolTip(texto)
    return lbl


def _banda_seccion(texto: str, color_acento: str = COLOR_PRIMARIO) -> QWidget:
    """Banda de sección a todo el ancho, con barra de acento a la izquierda."""
    w = QWidget()
    w.setStyleSheet(
        f"background: {COLOR_CABECERA}; "
        f"border-left: 3px solid {color_acento};"
    )
    lay = QHBoxLayout(w)
    lay.setContentsMargins(9, 5, 8, 5)
    lbl = QLabel(texto.upper())
    lbl.setStyleSheet(
        f"background: transparent; color: {color_acento}; "
        f"font-size: {FS_MICRO}px; font-weight: bold;"
    )
    lay.addWidget(lbl)
    lay.addStretch()
    return w


def _chip(texto: str, estilo: str, tooltip: str = "") -> QLabel:
    """Etiqueta tipo píldora (badge) de ancho natural."""
    lbl = QLabel(texto)
    lbl.setStyleSheet(estilo)
    lbl.setAlignment(Qt.AlignCenter)
    if tooltip:
        lbl.setToolTip(tooltip)
    return lbl


# ===========================================================================
# PÁGINA 0 — Bienvenida y área de interés
# ===========================================================================

class PaginaAOI(QWidget):
    """Selección del área de interés (polígono de estudio)."""

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self._iface = iface
        self._herramienta_dibujo = None        # HerramientaDibujoPoligono activa
        self._herramienta_previa = None        # herramienta de mapa anterior
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # Título
        layout.addWidget(_etiqueta_titulo(
            "Paso 1 de 7 — Área de interés del análisis"
        ))
        layout.addWidget(_separador())

        # Descripción
        desc = QLabel(
            "Esta herramienta realiza una evaluación multicriterio para la "
            "selección de sitios de disposición final de residuos sólidos "
            "urbanos (RSU) conforme a la <b>NOM-083-SEMARNAT-2003</b>.<br><br>"
            "El análisis se realiza en dos fases:<br>"
            "  1. <b>Exclusión dicotómica</b>: identifica zonas donde la "
            "instalación está prohibida por normativa.<br>"
            "  2. <b>Ponderación</b>: califica las zonas aptas según criterios "
            "de aptitud relativa con pesos directos (suma = 100 %).<br>"
        )
        desc.setWordWrap(True)
        desc.setStyleSheet(f"font-size: {FS_CUERPO}px; color: {COLOR_TEXTO};")
        layout.addWidget(desc)
        layout.addWidget(_etiqueta_nota(
            "Las restricciones de ubicación del § 6.1 de la norma aplican a "
            "cualquier sitio de disposición final, sea tipo A, B, C o D "
            "(NOM-083-SEMARNAT-2003, § 6.1, párrafo introductorio). La categoría "
            "por tonelaje de la Tabla 1 rige los estudios previos y las "
            "especificaciones de diseño, no los criterios de ubicación, por lo "
            "que no altera este análisis."
        ))
        layout.addWidget(_separador())

        # Selector de capa del área de interés
        layout.addWidget(_etiqueta_seccion("Capa del área de interés (polígono)"))
        self.combo_aoi = QgsMapLayerComboBox()
        self.combo_aoi.setFilters(QgsMapLayerProxyModel.PolygonLayer)
        self.combo_aoi.setShowCrs(True)
        layout.addWidget(self.combo_aoi)

        # Opciones alternativas al combo
        _o = QLabel("— ó —")
        _o.setStyleSheet(f"font-size: {FS_META}px; color: {COLOR_TEXTO_SUAVE};")
        layout.addWidget(_o)
        btn_layout = QHBoxLayout()
        self.btn_dibujar_aoi = QPushButton("✏  Dibujar en el mapa…")
        self.btn_dibujar_aoi.setStyleSheet(ESTILO_BOTON_PRIMARIO)
        self.btn_dibujar_aoi.setToolTip(
            "Dibuja un polígono directamente sobre el lienzo de QGIS.\n"
            "Clic izquierdo: añadir vértice  |  Doble clic / clic derecho: finalizar  "
            "|  Retroceso: deshacer vértice  |  Escape: cancelar"
        )
        self.btn_dibujar_aoi.clicked.connect(self._iniciar_dibujo)
        self.btn_cargar_aoi = QPushButton("  Cargar área de interés desde archivo…")
        self.btn_cargar_aoi.setStyleSheet(ESTILO_BOTON_PRIMARIO)
        self.btn_cargar_aoi.clicked.connect(self._cargar_desde_archivo)
        btn_layout.addWidget(self.btn_dibujar_aoi)
        btn_layout.addWidget(self.btn_cargar_aoi)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)
        layout.addWidget(_etiqueta_nota(
            "Para dibujar: haz clic en el mapa para añadir vértices, "
            "doble clic o clic derecho para cerrar el polígono."
        ))

        # Directorio de trabajo
        layout.addWidget(_separador())
        layout.addWidget(_etiqueta_seccion(
            "Directorio de trabajo · datos y resultados"
        ))
        dir_layout = QHBoxLayout()
        self.txt_directorio = QLineEdit()
        self.txt_directorio.setPlaceholderText("Seleccione un directorio…")
        default_dir = QgsProject.instance().absolutePath() or os.path.expanduser("~")
        self.txt_directorio.setText(default_dir)
        dir_layout.addWidget(self.txt_directorio)
        btn_dir = QPushButton("Examinar…")
        btn_dir.clicked.connect(self._seleccionar_directorio)
        dir_layout.addWidget(btn_dir)
        layout.addLayout(dir_layout)

        layout.addWidget(_etiqueta_nota(
            "Los datos descargados del INEGI y los resultados del análisis se "
            "guardarán en el subdirectorio datos_evaluacion_rsu/ dentro de la "
            "ruta seleccionada."
        ))
        layout.addStretch()

    # ── Dibujo interactivo ──────────────────────────────────────────────────

    def _iniciar_dibujo(self):
        """Oculta el diálogo y activa la herramienta de dibujo sobre el canvas."""
        from .herramienta_dibujo import HerramientaDibujoPoligono
        canvas = self._iface.mapCanvas()

        # Guardar herramienta activa para restaurarla después
        self._herramienta_previa = canvas.mapTool()

        self._herramienta_dibujo = HerramientaDibujoPoligono(canvas)
        self._herramienta_dibujo.capturado.connect(self._poligono_dibujado)
        self._herramienta_dibujo.cancelado.connect(self._dibujo_cancelado)

        # Ocultar el diálogo para que el usuario vea el mapa
        self.window().hide()
        canvas.setMapTool(self._herramienta_dibujo)
        self._iface.mainWindow().statusBar().showMessage(
            "EvaluaciónRSU — dibujando área de interés: clic izquierdo=añadir vértice | "
            "doble clic / clic derecho=cerrar | Retroceso=deshacer | Escape=cancelar"
        )

    def _poligono_dibujado(self, geom):
        """Slot llamado cuando la herramienta emite capturado.

        Difiere el trabajo real al siguiente ciclo del event loop con
        QTimer.singleShot(0) para evitar re-entrancia durante el handler
        del clic/doble-clic de Qt.
        """
        dialogo = self.window()
        # Limpiar referencias aquí — antes de que deactivate() pueda llegar
        self._herramienta_dibujo = None
        self._herramienta_previa = None
        QTimer.singleShot(0, lambda: self._procesar_geom_dibujada(dialogo, geom))

    def _procesar_geom_dibujada(self, dialogo, geom):
        """Crea la capa de memoria y restaura el wizard. Se ejecuta en el próximo tick."""
        try:
            crs = QgsProject.instance().crs()
            auth = crs.authid() or "EPSG:4326"
            capa = QgsVectorLayer(
                f"Polygon?crs={auth}&field=nombre:string(100)",
                "Área de interés dibujada",
                "memory",
            )
            if not capa.isValid():
                raise RuntimeError(
                    f"No se pudo crear la capa temporal (CRS: {auth})"
                )

            from qgis.core import QgsFeature
            feat = QgsFeature(capa.fields())
            feat.setGeometry(geom)
            feat.setAttribute("nombre", "Área de interés")
            capa.dataProvider().addFeatures([feat])
            capa.updateExtents()
            QgsProject.instance().addMapLayer(capa)
            self.combo_aoi.setLayer(capa)
            self._iface.mainWindow().statusBar().clearMessage()

        except Exception as exc:
            import traceback
            from qgis.core import QgsMessageLog, Qgis
            QgsMessageLog.logMessage(
                f"EvaluaciónRSU — error al procesar área de interés dibujada:\n{traceback.format_exc()}",
                "EvaluaciónRSU", Qgis.Critical,
            )
            QMessageBox.critical(None, "Error al procesar el polígono", str(exc))

        finally:
            try:
                dialogo.show()
                dialogo.raise_()
                dialogo.activateWindow()
            except Exception:
                pass

    def _dibujo_cancelado(self):
        """Restaura el estado cuando el usuario pulsa Escape."""
        self._herramienta_dibujo = None
        self._herramienta_previa = None
        dialogo = self.window()
        self._iface.mainWindow().statusBar().clearMessage()
        QTimer.singleShot(0, lambda: (dialogo.show(), dialogo.raise_(), dialogo.activateWindow()))

    # ── Carga desde archivo ─────────────────────────────────────────────────

    def _cargar_desde_archivo(self):
        ruta, _ = QFileDialog.getOpenFileName(
            self, "Abrir área de interés",
            os.path.expanduser("~"),
            "Capas vectoriales (*.shp *.gpkg *.geojson *.kml)"
        )
        if ruta:
            nombre = Path(ruta).stem
            capa = QgsVectorLayer(ruta, nombre, "ogr")
            if not capa.isValid():
                QMessageBox.critical(self, "Error", f"No se pudo abrir: {ruta}")
                return
            if QgsWkbTypes.geometryType(capa.wkbType()) != QgsWkbTypes.PolygonGeometry:
                QMessageBox.warning(
                    self, "Tipo incorrecto",
                    "La capa seleccionada debe ser de tipo Polígono."
                )
                return
            QgsProject.instance().addMapLayer(capa)

    def _seleccionar_directorio(self):
        d = QFileDialog.getExistingDirectory(
            self, "Seleccionar directorio de trabajo",
            self.txt_directorio.text()
        )
        if d:
            self.txt_directorio.setText(d)

    def aoi_layer(self) -> Optional[QgsVectorLayer]:
        return self.combo_aoi.currentLayer()

    def directorio_trabajo(self) -> str:
        return self.txt_directorio.text().strip()

    def es_valido(self) -> tuple[bool, str]:
        if self.aoi_layer() is None:
            return False, "Seleccione una capa de área de interés (polígono)."
        if not self.directorio_trabajo():
            return False, "Seleccione un directorio de trabajo."
        return True, ""


# ===========================================================================
# PÁGINA 1 — Selección de criterios y modo de análisis
# ===========================================================================

class PaginaCriterios(QWidget):
    """Selección de criterios activos, modo de análisis y rol por criterio."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._checkboxes: dict[str, object] = {}   # id → QCheckBox
        self._rol_grupos: dict[str, object] = {}   # id → QButtonGroup
        self._build_ui()

    def _build_ui(self):
        from qgis.PyQt.QtWidgets import QRadioButton as _RB, QButtonGroup as _BG
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        layout.addWidget(_etiqueta_titulo("Paso 2 de 7 — Selección de criterios"))
        layout.addWidget(_separador())

        # ── Modo de análisis ──────────────────────────────────────────────────
        layout.addWidget(_etiqueta_seccion("Modo de análisis"))
        modo_container = QWidget()
        modo_h = QHBoxLayout(modo_container)
        modo_h.setContentsMargins(4, 4, 4, 4)
        modo_h.setSpacing(32)
        self._btn_grupo_modo = _BG(self)
        self._rad_dicotomico = _RB("Dicotómico (Prohibido / Permitido)")
        self._rad_dicotomico.setToolTip(
            "Cada criterio produce un resultado binario: Prohibido o Permitido.\n"
            "El resultado es un mapa vectorial de zonas excluidas y zonas aptas."
        )
        self._rad_ponderado = _RB("Ponderado (aptitud relativa)")
        self._rad_ponderado.setToolTip(
            "Cada capa tiene un rol:\n"
            "  • Excluyente: máscara binaria (aptitud = 0 si no se cumple).\n"
            "  • Ponderado: aporta un puntaje 0–100 con un peso asignado.\n"
            "El resultado incluye un ráster de aptitud continua (0–100 %)."
        )
        self._btn_grupo_modo.addButton(self._rad_dicotomico, 0)
        self._btn_grupo_modo.addButton(self._rad_ponderado, 1)
        self._rad_dicotomico.setChecked(True)
        modo_h.addWidget(self._rad_dicotomico)
        modo_h.addWidget(self._rad_ponderado)
        modo_h.addStretch()
        layout.addWidget(modo_container)
        layout.addWidget(_etiqueta_nota(
            "Dicotómico: aplica exclusivamente los criterios de exclusión del § 6.1. "
            "Ponderado: también evalúa aptitud relativa con pesos asignados por el usuario."
        ))
        layout.addWidget(_separador())

        # ── Lista de criterios ────────────────────────────────────────────────
        layout.addWidget(_etiqueta_seccion("Criterios a incluir en el análisis"))
        layout.addWidget(_etiqueta_nota(
            "En modo ponderado puede asignar el rol de cada criterio."
        ))

        self._chk_todos = _toggle_todos()
        self._chk_todos.stateChanged.connect(self._toggle_todos_criterios)
        layout.addWidget(self._chk_todos)

        # Scroll — el encabezado se construye dentro para alinear columnas
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        self._scroll_criterios = scroll
        layout.addWidget(scroll)

        # Conectar modo para actualizar columna Rol
        self._rad_dicotomico.toggled.connect(self._on_modo_cambiado)
        self._rad_ponderado.toggled.connect(self._on_modo_cambiado)

        self._rebuild_lista()

    # Anchos de columna
    _COL_ACTIVO = 54
    _COL_NOMBRE = 320
    _COL_NORMA  = 76
    _COL_ROL    = 206

    def _rebuild_lista(self):
        """Reconstruye la lista de criterios con checkboxes y selectores de rol."""
        self._checkboxes.clear()
        self._rol_grupos.clear()

        ponderado = self._rad_ponderado.isChecked()

        contenedor = QWidget()
        cont_layout = QVBoxLayout(contenedor)
        cont_layout.setSpacing(0)
        cont_layout.setContentsMargins(0, 0, 0, 0)

        columnas = [
            ("Activo", self._COL_ACTIVO, Qt.AlignHCenter),
            ("Criterio", self._COL_NOMBRE, Qt.AlignLeft),
            ("Norma", self._COL_NORMA, Qt.AlignHCenter),
        ]
        if ponderado:
            columnas.append(("Rol del criterio", self._COL_ROL, Qt.AlignLeft))
        cont_layout.addWidget(_cabecera_tabla(columnas))

        indice = 0
        cont_layout.addWidget(
            _banda_seccion("NOM-083-SEMARNAT-2003 · § 6.1", COLOR_PRIMARIO)
        )
        for criterio in CRITERIOS_DEFAULT:
            if criterio.obligatorio:
                cont_layout.addWidget(self._crear_fila(criterio, ponderado, indice))
                indice += 1

        indice = 0
        cont_layout.addWidget(_banda_seccion("Criterios adicionales", COLOR_SLATE))
        for criterio in CRITERIOS_DEFAULT:
            if not criterio.obligatorio:
                cont_layout.addWidget(self._crear_fila(criterio, ponderado, indice))
                indice += 1

        cont_layout.addStretch()
        self._scroll_criterios.setWidget(contenedor)

    def _crear_fila(self, criterio, mostrar_rol: bool, indice: int = 0) -> QWidget:
        from qgis.PyQt.QtWidgets import QButtonGroup as _BG, QRadioButton as _RB
        w, row = _fila_tabla(indice)

        chk = _casilla(criterio.activo, "Incluir este criterio en el análisis")
        chk.stateChanged.connect(lambda s, c=criterio: setattr(c, "activo", bool(s)))
        self._checkboxes[criterio.id] = chk
        row.addWidget(_celda_casilla(chk, self._COL_ACTIVO))

        row.addWidget(_celda(
            criterio.nombre, self._COL_NOMBRE,
            tooltip=f"<b>{criterio.nombre}</b><br><br>{criterio.descripcion}",
        ))

        # Columna Norma — contenedor de ancho fijo con el chip centrado
        norma_w = QWidget()
        norma_w.setFixedWidth(self._COL_NORMA)
        norma_w.setStyleSheet("background: transparent;")
        norma_l = QHBoxLayout(norma_w)
        norma_l.setContentsMargins(0, 0, 0, 0)
        if criterio.obligatorio:
            norma_l.addWidget(_chip(
                "NOM-083", ESTILO_CHIP_NORMA,
                "Criterio requerido por NOM-083-SEMARNAT-2003.",
            ))
        else:
            norma_l.addStretch()
        row.addWidget(norma_w)

        if mostrar_rol:
            rol_w = QWidget()
            rol_w.setFixedWidth(self._COL_ROL)
            rol_w.setStyleSheet("background: transparent;")
            rol_h = QHBoxLayout(rol_w)
            rol_h.setContentsMargins(0, 0, 0, 0)
            rol_h.setSpacing(10)
            rad_excl = _RB("Excluyente")
            rad_excl.setToolTip("Descarta zonas que no cumplen (máscara binaria).")
            rad_pond = _RB("Ponderado")
            rad_pond.setToolTip("Aporta puntaje de aptitud con un peso asignado.")
            grp = _BG(w)
            grp.addButton(rad_excl, 0)
            grp.addButton(rad_pond, 1)
            if criterio.rol_ponderado == "ponderado":
                rad_pond.setChecked(True)
            else:
                rad_excl.setChecked(True)
            grp.idClicked.connect(
                lambda bid, c=criterio: setattr(c, "rol_ponderado",
                                                "ponderado" if bid == 1 else "excluyente")
            )
            self._rol_grupos[criterio.id] = grp
            rol_h.addWidget(rad_excl)
            rol_h.addWidget(rad_pond)
            rol_h.addStretch()
            row.addWidget(rol_w)

        row.addStretch(1)
        return w

    def _toggle_todos_criterios(self, state: int):
        """Marca o desmarca todos los criterios de la lista."""
        marcado = bool(state)
        por_id = {c.id: c for c in CRITERIOS_DEFAULT}
        for crit_id, chk in self._checkboxes.items():
            chk.blockSignals(True)
            chk.setChecked(marcado)
            chk.blockSignals(False)
            c = por_id.get(crit_id)
            if c is not None:
                c.activo = marcado

    def _on_modo_cambiado(self):
        self._rebuild_lista()

    def modo_analisis(self) -> str:
        """Devuelve 'dicotomico' o 'ponderado'."""
        return "ponderado" if self._rad_ponderado.isChecked() else "dicotomico"


# ===========================================================================
# PÁGINA 2 — Carga / Descarga de datos
# ===========================================================================

class FilaCriterio(QWidget):
    """Fila de la tabla de criterios: muestra estado y permite cargar o descargar."""

    archivo_seleccionado = pyqtSignal(str, str)   # (id_criterio, ruta)

    ESTADO_PENDIENTE   = "Pendiente"
    ESTADO_DESCARGANDO = "Descargando…"
    ESTADO_LISTO       = "Listo"
    ESTADO_MANUAL      = "Requiere archivo"
    ESTADO_ERROR       = "Error"

    # Anchos de columna — compartidos con la cabecera de PaginaDatos
    COL_NOMBRE = 300
    COL_NORMA  = 76
    COL_FUENTE = 140
    COL_ESTADO = 140
    COL_ACCION = 156

    _FUENTE_TEXTO = {
        FuenteDatos.INEGI_DL:   "INEGI (descarga)",
        FuenteDatos.CONANP:     "CONANP",
        FuenteDatos.CONABIO:    "CONABIO",
        FuenteDatos.OSM:        "OpenStreetMap",
        FuenteDatos.COPERNICUS: "Copernicus GLO-30",
        FuenteDatos.MANUAL:     "Manual",
    }

    def __init__(self, criterio: Criterio, indice: int = 0, parent=None):
        super().__init__(parent)
        self.criterio = criterio
        self._indice = indice
        self._build_ui()

    def _build_ui(self):
        if self._indice % 2 == 1:
            self.setStyleSheet(f"background: {COLOR_FILA_ALT};")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(ESPACIO_COL)

        layout.addWidget(_celda(
            self.criterio.nombre, self.COL_NOMBRE,
            tooltip=f"<b>{self.criterio.nombre}</b><br><br>{self.criterio.descripcion}",
        ))

        # Columna Norma — chip centrado en contenedor de ancho fijo
        norma_w = QWidget()
        norma_w.setFixedWidth(self.COL_NORMA)
        norma_w.setStyleSheet("background: transparent;")
        norma_l = QHBoxLayout(norma_w)
        norma_l.setContentsMargins(0, 0, 0, 0)
        if self.criterio.obligatorio:
            norma_l.addWidget(_chip(
                "NOM-083", ESTILO_CHIP_NORMA,
                "Criterio requerido por NOM-083-SEMARNAT-2003.\n"
                "Se recomienda mantenerlo activo para cumplir con la norma.",
            ))
        else:
            norma_l.addStretch()
        layout.addWidget(norma_w)

        layout.addWidget(_celda(
            self._FUENTE_TEXTO.get(self.criterio.fuente, "—"),
            self.COL_FUENTE, color=COLOR_SLATE_TEXTO, tamano=FS_META,
        ))

        self.etq_estado = QLabel(self.ESTADO_PENDIENTE)
        self.etq_estado.setFixedWidth(self.COL_ESTADO)
        self._pintar_estado(COLOR_TEXTO_SUAVE)
        layout.addWidget(self.etq_estado)

        # Contenedor de acciones — ancho fijo para que la columna no baile
        acciones = QWidget()
        acciones.setFixedWidth(self.COL_ACCION)
        acciones.setStyleSheet("background: transparent;")
        acc_l = QHBoxLayout(acciones)
        acc_l.setContentsMargins(0, 0, 0, 0)
        acc_l.setSpacing(6)

        self.btn_archivo = QPushButton("Seleccionar archivo…")
        self.btn_archivo.setStyleSheet(ESTILO_BOTON_CONTORNO)
        self.btn_archivo.setCursor(Qt.PointingHandCursor)
        self.btn_archivo.clicked.connect(self._seleccionar_archivo)
        acc_l.addWidget(self.btn_archivo)

        # Botón de descarga — visible sólo en criterios MANUAL con descarga_url
        self.btn_descarga = QPushButton("Descargar")
        self.btn_descarga.setStyleSheet(ESTILO_BOTON_INFO)
        self.btn_descarga.setCursor(Qt.PointingHandCursor)
        if self.criterio.descarga_info:
            self.btn_descarga.setToolTip(self.criterio.descarga_info)
        self.btn_descarga.setVisible(False)   # oculto hasta que se requiera archivo manual
        self.btn_descarga.clicked.connect(self._abrir_url_descarga)
        acc_l.addWidget(self.btn_descarga)

        layout.addWidget(acciones)
        layout.addStretch(1)

    @staticmethod
    def cabecera() -> QWidget:
        """Fila de encabezado con los mismos anchos que las filas de datos."""
        return _cabecera_tabla([
            ("Criterio", FilaCriterio.COL_NOMBRE, Qt.AlignLeft),
            ("Norma",    FilaCriterio.COL_NORMA,  Qt.AlignHCenter),
            ("Fuente",   FilaCriterio.COL_FUENTE, Qt.AlignLeft),
            ("Estado",   FilaCriterio.COL_ESTADO, Qt.AlignLeft),
            ("Datos",    FilaCriterio.COL_ACCION, Qt.AlignLeft),
        ])

    def _pintar_estado(self, color: str, negrita: bool = False):
        self.etq_estado.setStyleSheet(
            f"background: transparent; color: {color}; font-size: {FS_META}px;"
            + (" font-weight: bold;" if negrita else "")
        )

    def _abrir_url_descarga(self):
        """Abre en el navegador la URL de descarga del criterio."""
        url = getattr(self.criterio, "descarga_url", None)
        if url:
            QDesktopServices.openUrl(QUrl(url))

    def _seleccionar_archivo(self):
        ruta, _ = QFileDialog.getOpenFileName(
            self, f"Cargar: {self.criterio.nombre}",
            os.path.expanduser("~"),
            "Vectoriales (*.shp *.gpkg *.geojson *.kml);;Ráster (*.tif *.img *.asc)"
        )
        if ruta:
            self.archivo_seleccionado.emit(self.criterio.id, ruta)

    def set_estado(self, estado: str, color: Optional[str] = None,
                   negrita: bool = False):
        self.etq_estado.setText(estado)
        self._pintar_estado(color or COLOR_TEXTO_SUAVE, negrita)

    def marcar_listo(self):
        self.set_estado(f"● {self.ESTADO_LISTO}", COLOR_PRIMARIO)
        self.btn_archivo.setText("Reemplazar…")

    def marcar_error(self, msg: str = ""):
        self.set_estado(f"● {self.ESTADO_ERROR}", COLOR_EXCLUIDO, negrita=True)
        if msg:
            self.etq_estado.setToolTip(msg)

    def marcar_manual(self):
        self.set_estado(f"○ {self.ESTADO_MANUAL}", COLOR_SLATE_TEXTO)
        if getattr(self.criterio, "descarga_url", None):
            self.btn_descarga.setVisible(True)

    def marcar_descargando(self):
        self.set_estado(f"◌ {self.ESTADO_DESCARGANDO}", COLOR_INFO)


class PaginaDatos(QWidget):
    """Gestión de la descarga y carga de datos."""

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        # Copias independientes de los criterios para que el usuario
        # pueda recargar datos sin perder el estado global
        self.criterios: list[Criterio] = copy.deepcopy(CRITERIOS_DEFAULT)
        self._descargador: Optional[Descargador] = None
        self._hilo: Optional[HiloDescarga] = None
        self._filas: dict[str, FilaCriterio] = {}
        self._inicializado: bool = False   # evitar reinicializar al volver atrás
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        layout.addWidget(_etiqueta_titulo("Paso 3 de 7 — Datos de los criterios"))
        layout.addWidget(_separador())

        info = QLabel(
            "Descargue o cargue los datos geográficos para los criterios seleccionados.<br>"
            "Los datos con fuente <b>automática</b> (INEGI, OSM, CONANP, etc.) "
            "se descargan con el botón de abajo.<br>"
            "Los marcados como <b>Manual</b> deben ser provistos por el usuario "
            "seleccionando el archivo desde su equipo."
        )
        info.setWordWrap(True)
        info.setStyleSheet(f"font-size: {FS_CUERPO}px; color: {COLOR_TEXTO};")
        layout.addWidget(info)

        # Barra de botones
        btn_layout = QHBoxLayout()
        self.btn_descargar = QPushButton("⬇  Descargar datos automáticos")
        self.btn_descargar.setStyleSheet(ESTILO_BOTON_PRIMARIO)
        self.btn_descargar.clicked.connect(self._iniciar_descarga)
        btn_layout.addWidget(self.btn_descargar)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        # Barra de progreso
        self.barra_progreso = QProgressBar()
        self.barra_progreso.setVisible(False)
        self.barra_progreso.setRange(0, 100)
        layout.addWidget(self.barra_progreso)

        self.etq_progreso = QLabel("")
        self.etq_progreso.setStyleSheet(
            f"font-size: {FS_MICRO}px; color: {COLOR_SLATE_TEXTO};"
        )
        layout.addWidget(self.etq_progreso)

        layout.addWidget(_separador())

        # Lista de criterios (scroll)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        contenedor = QWidget()
        contenedor_layout = QVBoxLayout(contenedor)
        contenedor_layout.setSpacing(0)
        contenedor_layout.setContentsMargins(0, 0, 0, 0)

        self._scroll_datos = scroll   # referencia para rebuild
        self._contenedor_datos_layout = contenedor_layout
        self._poblar_criterios(contenedor_layout)

        contenedor_layout.addStretch()
        scroll.setWidget(contenedor)
        layout.addWidget(scroll)

    def _poblar_criterios(self, layout):
        """Llena el layout con FilaCriterio para cada criterio activo."""
        activos_obligatorios = [c for c in self.criterios if c.obligatorio and c.activo]
        activos_opcionales   = [c for c in self.criterios if not c.obligatorio and c.activo]

        if not activos_obligatorios and not activos_opcionales:
            lbl = QLabel(
                "<i>No hay criterios activos. Active al menos uno en el Paso 2.</i>"
            )
            lbl.setStyleSheet(
                f"color: {COLOR_TEXTO_SUAVE}; font-size: {FS_META}px;"
            )
            layout.addWidget(lbl)
            return

        layout.addWidget(FilaCriterio.cabecera())

        def _agregar_seccion(criterios, titulo, acento):
            if not criterios:
                return
            layout.addWidget(_banda_seccion(titulo, acento))
            for i, c in enumerate(criterios):
                fila = FilaCriterio(c, indice=i)
                fila.archivo_seleccionado.connect(self._cargar_archivo_manual)
                self._filas[c.id] = fila
                layout.addWidget(fila)

        _agregar_seccion(
            activos_obligatorios, "NOM-083-SEMARNAT-2003", COLOR_PRIMARIO
        )
        _agregar_seccion(
            activos_opcionales, "Criterios adicionales", COLOR_SLATE
        )

    def refrescar_criterios(self):
        """Sincroniza el estado activo desde CRITERIOS_DEFAULT y reconstruye la vista."""
        # Actualizar estado activo en self.criterios desde CRITERIOS_DEFAULT
        src_por_id = {c.id: c for c in CRITERIOS_DEFAULT}
        for c in self.criterios:
            src = src_por_id.get(c.id)
            if src:
                c.activo = src.activo

        # Reconstruir lista (solo criterios activos)
        while self._contenedor_datos_layout.count():
            item = self._contenedor_datos_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._filas.clear()
        self._poblar_criterios(self._contenedor_datos_layout)
        self._contenedor_datos_layout.addStretch()

    def inicializar_descargador(self, aoi_layer: QgsVectorLayer, directorio: str):
        """Crea el descargador y conecta señales. Idempotente: no reinicializa."""
        if self._inicializado:
            return
        self._descargador = Descargador(aoi_layer, directorio)
        self._descargador.capa_lista.connect(self._on_capa_lista)
        self._descargador.error.connect(self._on_error)
        self._descargador.requiere_archivo.connect(self._on_requiere_archivo)
        self._inicializado = True

    def _iniciar_descarga(self):
        if self._descargador is None:
            QMessageBox.warning(self, "Error", "Primero configure el área de interés.")
            return

        self.btn_descargar.setEnabled(False)
        self.barra_progreso.setVisible(True)
        self.barra_progreso.setValue(0)

        criterios_auto = [
            c for c in self.criterios
            if c.fuente != FuenteDatos.MANUAL and c.activo
        ]
        for c in criterios_auto:
            self._filas[c.id].marcar_descargando()

        self._hilo = HiloDescarga(self._descargador, criterios_auto)
        self._hilo.progreso.connect(self._on_progreso)
        self._hilo.terminado.connect(self._on_descarga_terminada)
        self._hilo.start()

    def _cargar_archivo_manual(self, id_criterio: str, ruta: str):
        criterio = next((c for c in self.criterios if c.id == id_criterio), None)
        if criterio is None:
            return

        if self._descargador is None:
            # Sin descargador aún: cargar la capa directamente
            from qgis.core import QgsVectorLayer, QgsRasterLayer
            extension = Path(ruta).suffix.lower()
            if extension in (".tif", ".img", ".asc", ".vrt"):
                capa = QgsRasterLayer(ruta, criterio.nombre)
            else:
                capa = QgsVectorLayer(ruta, criterio.nombre, "ogr")
            if capa.isValid():
                criterio.capa = capa
                criterio.origen_carga = "manual"
                criterio.ruta_dato = ruta
                fila = self._filas[id_criterio]
                fila.marcar_listo()   # ya muestra btn_mapeo para vectoriales
            else:
                self._filas[id_criterio].marcar_error("Archivo inválido")
            return

        capa = self._descargador.cargar_archivo_local(criterio, ruta)
        if capa:
            self._filas[id_criterio].marcar_listo()
        else:
            self._filas[id_criterio].marcar_error("Archivo inválido")

    def _on_capa_lista(self, id_criterio: str, _capa):
        if id_criterio in self._filas:
            self._filas[id_criterio].marcar_listo()

    def _on_error(self, id_criterio: str, msg: str):
        if id_criterio in self._filas:
            self._filas[id_criterio].marcar_error(msg[:60])

    def _on_requiere_archivo(self, id_criterio: str):
        if id_criterio in self._filas:
            self._filas[id_criterio].marcar_manual()

    def _on_progreso(self, pct: int, msg: str):
        if pct >= 0:
            self.barra_progreso.setValue(pct)
        if msg:
            self.etq_progreso.setText(msg)

    def _on_descarga_terminada(self):
        try:
            self.btn_descargar.setEnabled(True)
            self.barra_progreso.setValue(100)
            self.etq_progreso.setText("Descarga completada.")
        except RuntimeError:
            pass


# ===========================================================================
# PÁGINA 2 — Configuración de exclusión
# ===========================================================================

class PaginaExclusion(QWidget):
    """Configuración de buffers y parámetros del análisis dicotómico/ponderado."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._spinboxes: dict[str, QDoubleSpinBox] = {}
        self._checkboxes: dict[str, QCheckBox] = {}
        self._rol_grupos: dict[str, object] = {}   # criterio.id → QButtonGroup
        self._modo = "dicotomico"
        self._build_ui()

    def _build_ui(self):
        from qgis.PyQt.QtWidgets import QButtonGroup as _BG, QRadioButton as _RB
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        layout.addWidget(_etiqueta_titulo(
            "Paso 4 de 7 — Configuración del análisis de exclusión"
        ))
        layout.addWidget(_separador())
        self._lbl_desc = QLabel(
            "Las zonas que cumplan alguno de los siguientes criterios quedarán "
            "<b style='color:#C62828'>excluidas</b> del análisis. Puede ajustar "
            "las distancias o desactivar criterios específicos."
        )
        self._lbl_desc.setWordWrap(True)
        self._lbl_desc.setStyleSheet(
            f"font-size: {FS_CUERPO}px; color: {COLOR_TEXTO};"
        )
        layout.addWidget(self._lbl_desc)
        layout.addWidget(_etiqueta_nota(
            "Los valores predeterminados corresponden a los mínimos establecidos "
            "en la NOM-083-SEMARNAT-2003. Modificarlos es responsabilidad del usuario. "
            "Excepción: los 13 000 m de aeropuertos (§ 6.1.1) no son una prohibición "
            "de la norma sino el umbral que obliga a realizar un estudio de riesgo "
            "aviario; aquí se aplican como exclusión por criterio conservador."
        ))
        layout.addWidget(_separador())

        # Toggle seleccionar/deseleccionar todos
        self._chk_todos_excl = _toggle_todos()
        self._chk_todos_excl.stateChanged.connect(self._toggle_todos_exclusion)
        layout.addWidget(self._chk_todos_excl)

        # Scroll con filas de criterios — el encabezado va DENTRO del scroll
        # para que se alinee con el contenido incluso cuando hay scrollbar vertical.
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        layout.addWidget(self._scroll)

        self._rebuild_scroll()

    def set_modo(self, modo: str):
        """Cambia el modo ('dicotomico' o 'ponderado') y reconstruye el scroll."""
        self._modo = modo
        ponderado = (modo == "ponderado")
        if ponderado:
            self._lbl_desc.setText(
                "Defina el rol de cada criterio: "
                "<b style='color:#C62828'>Excluyente</b> (descarta zonas que no cumplen) "
                "o <b style='color:#1565C0'>Ponderado</b> (aporta puntaje de aptitud)."
            )
        else:
            self._lbl_desc.setText(
                "Las zonas que cumplan alguno de los siguientes criterios quedarán "
                "<b style='color:#C62828'>excluidas</b> del análisis. Puede ajustar "
                "las distancias o desactivar criterios específicos."
            )
        self._rebuild_scroll()

    # Anchos fijos por columna — compartidos entre cabecera y filas para
    # alineación perfecta. Dimensionados para que ningún encabezado se recorte.
    _COL_ACTIVO  = 54
    _COL_NOMBRE  = 300
    _COL_TIPO    = 88
    _COL_BUFFER  = 116
    _COL_MIN_NOM = 104
    _COL_ROL     = 206

    def _crear_cabecera_exclusion(self) -> QWidget:
        """Fila de encabezado con los mismos anchos fijos que las filas de datos."""
        columnas = [
            ("Activo",     self._COL_ACTIVO,  Qt.AlignHCenter),
            ("Criterio",   self._COL_NOMBRE,  Qt.AlignLeft),
            ("Tipo",       self._COL_TIPO,    Qt.AlignLeft),
            ("Buffer (m)", self._COL_BUFFER,  Qt.AlignRight),
            ("Mín. NOM",   self._COL_MIN_NOM, Qt.AlignRight),
        ]
        if self._modo == "ponderado":
            columnas.append(("Rol del criterio", self._COL_ROL, Qt.AlignLeft))
        return _cabecera_tabla(columnas)

    def _rebuild_scroll(self):
        """Reconstruye el contenido del scroll según el modo actual.

        El encabezado se coloca DENTRO del contenedor del scroll para que
        se alinee automáticamente con las filas, incluso cuando el scrollbar
        vertical reduce el ancho disponible.
        """
        from qgis.PyQt.QtWidgets import QButtonGroup as _BG, QRadioButton as _RB
        self._spinboxes.clear()
        self._checkboxes.clear()
        self._rol_grupos.clear()

        contenedor = QWidget()
        cont_layout = QVBoxLayout(contenedor)
        cont_layout.setSpacing(0)
        cont_layout.setContentsMargins(0, 0, 0, 0)

        # Encabezado dentro del scroll
        cont_layout.addWidget(self._crear_cabecera_exclusion())

        indice = 0
        for criterio in CRITERIOS_DEFAULT:
            if not criterio.activo:
                continue   # solo criterios seleccionados en PaginaCriterios
            if self._modo == "dicotomico":
                if not criterio.es_exclusion:
                    continue
            fila = self._crear_fila_exclusion(criterio, indice)
            cont_layout.addWidget(fila)
            indice += 1

        cont_layout.addStretch()
        self._scroll.setWidget(contenedor)

    def _crear_fila_exclusion(self, criterio: Criterio, indice: int = 0) -> QWidget:
        from qgis.PyQt.QtWidgets import QButtonGroup as _BG, QRadioButton as _RB
        w, layout = _fila_tabla(indice)

        chk = _casilla(criterio.activo, "Aplicar este criterio de exclusión")
        chk.stateChanged.connect(lambda s, c=criterio: setattr(c, "activo", bool(s)))
        self._checkboxes[criterio.id] = chk
        layout.addWidget(_celda_casilla(chk, self._COL_ACTIVO))

        layout.addWidget(_celda(
            criterio.nombre, self._COL_NOMBRE,
            tooltip=(
                f"<b>{criterio.nombre}</b><br><br>{criterio.descripcion}"
                f"<br><br><i>Referencia: {criterio.referencia_normativa}</i>"
            ),
        ))

        es_buffer = criterio.tipo_exclusion.value == "buffer"
        layout.addWidget(_celda(
            "Buffer" if es_buffer else "Traslape", self._COL_TIPO,
            color=COLOR_SLATE_TEXTO, tamano=FS_META,
        ))

        spin = QDoubleSpinBox()
        spin.setRange(0, 99999)
        spin.setDecimals(0)
        spin.setSuffix(" m")
        spin.setValue(criterio.buffer_m)
        spin.setEnabled(es_buffer)
        spin.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        spin.setFixedWidth(self._COL_BUFFER)
        spin.valueChanged.connect(lambda v, c=criterio: setattr(c, "buffer_m", v))
        self._spinboxes[criterio.id] = spin
        layout.addWidget(spin)

        layout.addWidget(_celda(
            f"{criterio.buffer_min_nom:.0f} m" if criterio.buffer_min_nom > 0 else "—",
            self._COL_MIN_NOM, alineacion=Qt.AlignRight,
            color=COLOR_TEXTO_SUAVE, tamano=FS_META,
            tooltip=(
                "Distancia mínima establecida por la NOM-083-SEMARNAT-2003"
                if criterio.buffer_min_nom > 0
                else "La NOM-083 no fija una distancia para este criterio"
            ),
        ))

        # Rol selector — solo visible en modo ponderado
        if self._modo == "ponderado":
            rol_widget = QWidget()
            rol_widget.setFixedWidth(self._COL_ROL)
            rol_widget.setStyleSheet("background: transparent;")
            rol_layout = QHBoxLayout(rol_widget)
            rol_layout.setContentsMargins(0, 0, 0, 0)
            rol_layout.setSpacing(10)
            rad_excl = _RB("Excluyente")
            rad_excl.setToolTip("Descarta zonas que no cumplen este criterio (máscara binaria).")
            rad_pond = _RB("Ponderado")
            rad_pond.setToolTip(
                "Aporta un puntaje de aptitud; el peso se asigna en el Paso 5."
            )
            grp = _BG(w)
            grp.addButton(rad_excl, 0)
            grp.addButton(rad_pond, 1)
            self._rol_grupos[criterio.id] = grp

            # Restaurar valor previo
            if criterio.rol_ponderado == "ponderado":
                rad_pond.setChecked(True)
                spin.setEnabled(False)   # ponderados no tienen buffer aquí
            else:
                rad_excl.setChecked(True)

            def _on_rol_changed(btn_id, c=criterio, s=spin):
                rol = "ponderado" if btn_id == 1 else "excluyente"
                c.rol_ponderado = rol
                # spinbox de buffer solo aplica a excluyentes con tipo buffer
                s.setEnabled(
                    btn_id == 0 and c.tipo_exclusion.value == "buffer"
                )

            grp.idClicked.connect(_on_rol_changed)

            rol_layout.addWidget(rad_excl)
            rol_layout.addWidget(rad_pond)
            rol_layout.addStretch()
            layout.addWidget(rol_widget)

        layout.addStretch(1)
        return w

    def _toggle_todos_exclusion(self, state: int):
        """Selecciona o deselecciona todos los criterios de exclusión del Paso 4."""
        checked = bool(state)
        for crit_id, chk in self._checkboxes.items():
            chk.blockSignals(True)
            chk.setChecked(checked)
            chk.blockSignals(False)
            # Actualizar el criterio en CRITERIOS_DEFAULT
            for c in CRITERIOS_DEFAULT:
                if c.id == crit_id:
                    c.activo = checked
                    break


# ===========================================================================
# PÁGINA 3 — Configuración de ponderación
# ===========================================================================

class PaginaPonderacion(QWidget):
    """Asignación de pesos para el análisis de ponderación."""

    _ESTILO_SUMA_BASE = f"font-size: {FS_CUERPO}px; font-weight: bold;"
    _ESTILO_SUMA_OK   = _ESTILO_SUMA_BASE + f" color: {COLOR_PRIMARIO};"
    _ESTILO_SUMA_MAL  = _ESTILO_SUMA_BASE + f" color: {COLOR_EXCLUIDO};"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._spinboxes: dict[str, QDoubleSpinBox] = {}
        self._checkboxes: dict[str, QCheckBox] = {}
        self._modo = "dicotomico"
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        layout.addWidget(_etiqueta_titulo(
            "Paso 5 de 7 — Configuración de ponderación"
        ))
        layout.addWidget(_separador())
        self._lbl_desc_pond = QLabel(
            "Asigne un peso (0–100 %) a cada criterio de aptitud. "
            "La suma de los pesos activos debe ser exactamente <b>100 %</b>."
        )
        self._lbl_desc_pond.setWordWrap(True)
        self._lbl_desc_pond.setStyleSheet(
            f"font-size: {FS_CUERPO}px; color: {COLOR_TEXTO};"
        )
        layout.addWidget(self._lbl_desc_pond)

        # Indicador de suma
        self.etq_suma = QLabel("Suma: 0.0 %")
        self.etq_suma.setStyleSheet(self._ESTILO_SUMA_MAL)
        layout.addWidget(self.etq_suma)

        layout.addWidget(_separador())

        # Scroll con filas (reconstruible al cambiar modo)
        self._scroll_pond = QScrollArea()
        self._scroll_pond.setWidgetResizable(True)
        self._scroll_pond.setFrameShape(QFrame.NoFrame)
        layout.addWidget(self._scroll_pond)

        self._rebuild_scroll_pond()

        layout.addWidget(_etiqueta_nota(
            "Los criterios con peso 0 % no participan en la ponderación. "
            "Al menos un criterio debe tener datos cargados y peso > 0 % "
            "para calcular la aptitud relativa."
        ))

    def set_modo(self, modo: str):
        """Cambia el modo y reconstruye la lista de criterios ponderables."""
        self._modo = modo
        if modo == "dicotomico":
            self._lbl_desc_pond.setText(
                "<b>La ponderación no aplica en modo dicotómico.</b><br>"
                "El análisis producirá únicamente zonas excluidas y zonas aptas "
                "(resultado binario). Para calificar la aptitud relativa, cambie el "
                "modo a <i>Ponderado</i> en el <b>Paso 2</b>."
            )
        else:
            self._lbl_desc_pond.setText(
                "Asigne un peso (0–100 %) a los criterios con rol <i>Ponderado</i>. "
                "El rol se define en el <b>Paso 2</b> o el <b>Paso 4</b>. "
                "La suma debe ser exactamente <b>100 %</b>."
            )
        self._rebuild_scroll_pond()

    # Anchos de columna
    _COL_ACTIVO = 54
    _COL_NOMBRE = 300
    _COL_DESC   = 320
    _COL_PESO   = 116

    def _rebuild_scroll_pond(self):
        """Reconstruye el scroll de ponderación según el modo actual."""
        self._spinboxes.clear()
        self._checkboxes.clear()

        contenedor = QWidget()
        cont_layout = QVBoxLayout(contenedor)
        cont_layout.setSpacing(0)
        cont_layout.setContentsMargins(0, 0, 0, 0)

        if self._modo == "dicotomico":
            lbl = QLabel(
                "<i>No hay criterios de ponderación en modo dicotómico.</i>"
            )
            lbl.setStyleSheet(
                f"color: {COLOR_TEXTO_SUAVE}; font-size: {FS_META}px;"
            )
            lbl.setWordWrap(True)
            cont_layout.addWidget(lbl)
        else:
            cont_layout.addWidget(_cabecera_tabla([
                ("Activo",      self._COL_ACTIVO, Qt.AlignHCenter),
                ("Criterio",    self._COL_NOMBRE, Qt.AlignLeft),
                ("Descripción", self._COL_DESC,   Qt.AlignLeft),
                ("Peso",        self._COL_PESO,   Qt.AlignRight),
            ]))
            # Modo ponderado: solo criterios activos con rol_ponderado == "ponderado"
            indice = 0
            for criterio in CRITERIOS_DEFAULT:
                if not criterio.activo:
                    continue
                if criterio.rol_ponderado != "ponderado":
                    continue
                cont_layout.addWidget(
                    self._crear_fila_ponderacion(criterio, indice)
                )
                indice += 1

            if indice == 0:
                lbl = QLabel(
                    "<i>Ningún criterio tiene el rol <b>Ponderado</b>. "
                    "Asígnelo en el Paso 2 o el Paso 4.</i>"
                )
                lbl.setStyleSheet(
                    f"color: {COLOR_TEXTO_SUAVE}; font-size: {FS_META}px; "
                    "padding: 10px 8px;"
                )
                lbl.setWordWrap(True)
                cont_layout.addWidget(lbl)

        cont_layout.addStretch()
        self._scroll_pond.setWidget(contenedor)

    def _crear_fila_ponderacion(self, criterio: Criterio, indice: int = 0) -> QWidget:
        w, layout = _fila_tabla(indice)

        chk = _casilla(criterio.peso > 0, "El criterio participa con peso > 0 %")
        self._checkboxes[criterio.id] = chk
        layout.addWidget(_celda_casilla(chk, self._COL_ACTIVO))

        layout.addWidget(_celda(
            criterio.nombre, self._COL_NOMBRE,
            tooltip=f"<b>{criterio.nombre}</b><br><br>{criterio.descripcion}",
        ))

        layout.addWidget(_celda(
            criterio.descripcion, self._COL_DESC,
            color=COLOR_TEXTO_SUAVE, tamano=FS_META,
            tooltip=criterio.descripcion,
        ))

        spin = QDoubleSpinBox()
        spin.setRange(0, 100)
        spin.setSuffix(" %")
        spin.setDecimals(1)
        spin.setValue(criterio.peso)
        spin.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        spin.setFixedWidth(self._COL_PESO)
        spin.valueChanged.connect(lambda v, c=criterio: self._on_peso_cambio(c, v))
        self._spinboxes[criterio.id] = spin
        layout.addWidget(spin)

        layout.addStretch(1)
        return w

    def _on_peso_cambio(self, criterio: Criterio, valor: float):
        criterio.peso = valor
        self._actualizar_suma()

    def _criterios_activos_pond(self):
        """Retorna los criterios que participan en la ponderación según el modo."""
        if self._modo == "ponderado":
            return [c for c in CRITERIOS_DEFAULT if c.rol_ponderado == "ponderado" and c.peso > 0]
        return [c for c in CRITERIOS_DEFAULT if c.es_ponderacion and c.peso > 0]

    def _actualizar_suma(self):
        suma = sum(c.peso for c in self._criterios_activos_pond())
        self.etq_suma.setText(f"Suma: {suma:.1f} %")
        if abs(suma - 100.0) < 0.5:
            self.etq_suma.setStyleSheet(self._ESTILO_SUMA_OK)
        else:
            self.etq_suma.setStyleSheet(self._ESTILO_SUMA_MAL)

    def pesos_validos(self) -> tuple[bool, str]:
        if self._modo == "dicotomico":
            return True, ""   # no hay fase 2 en dicotómico
        suma = sum(c.peso for c in self._criterios_activos_pond())
        if abs(suma - 100.0) > 0.5:
            return False, f"La suma de los pesos es {suma:.1f} %, debe ser 100 %."
        return True, ""


# ===========================================================================
# PÁGINA 5 — Mapeo de atributos (dedicado)
# ===========================================================================

class PaginaMapeo(QWidget):
    """Paso dedicado para configurar el mapeo de atributos de capas manuales."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._criterios_datos: list = []   # referencia a pag_datos.criterios
        self._modo = "dicotomico"
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        layout.addWidget(_etiqueta_titulo("Paso 6 de 7 — Mapeo de atributos"))
        layout.addWidget(_separador())

        info = QLabel(
            "Configure cómo se usan los atributos de cada capa cargada manualmente.<br>"
            "Seleccione el campo y defina si sus valores son "
            "<b>Prohibitivos</b> o <b>Permisivos</b> "
            "(modo dicotómico) o asigne un <b>puntaje de aptitud</b> (modo ponderado).<br>"
            "Solo se muestran criterios con capa ya cargada."
        )
        info.setWordWrap(True)
        info.setStyleSheet(f"font-size: {FS_CUERPO}px; color: {COLOR_TEXTO};")
        layout.addWidget(info)

        layout.addWidget(_separador())

        # Scroll con filas de mapeo
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        self._contenedor_mapeo = QWidget()
        self._layout_mapeo = QVBoxLayout(self._contenedor_mapeo)
        self._layout_mapeo.setSpacing(0)
        self._layout_mapeo.setContentsMargins(0, 0, 0, 0)
        scroll.setWidget(self._contenedor_mapeo)
        layout.addWidget(scroll)

        self._etq_sin_capas = QLabel(
            "<i>No hay capas manuales cargadas todavía.<br>"
            "Cargue archivos en el Paso 3 (Datos) y luego regrese a este paso.</i>"
        )
        self._etq_sin_capas.setStyleSheet(
            f"color: {COLOR_TEXTO_SUAVE}; font-size: {FS_META}px;"
        )
        self._etq_sin_capas.setWordWrap(True)
        self._layout_mapeo.addWidget(self._etq_sin_capas)
        self._layout_mapeo.addStretch()

        # Nota al pie — explica el marcador † de las filas sin mapeo
        layout.addWidget(_separador())
        self._nota_pie = QLabel(
            f"<span style='color:{COLOR_SLATE_TEXTO};'><b>†</b></span> "
            "Sin mapeo configurado: todas las geometrías de la capa se tratan "
            "como <b>prohibitivas</b>. Configure el mapeo solo si necesita filtrar "
            "por los valores de un campo."
        )
        self._nota_pie.setWordWrap(True)
        self._nota_pie.setStyleSheet(
            f"color: {COLOR_SLATE_TEXTO}; font-size: 10px;"
        )
        layout.addWidget(self._nota_pie)

    def configurar(self, criterios: list, modo: str):
        """Recibe la lista de criterios con capas cargadas y el modo actual."""
        self._criterios_datos = criterios
        self._modo = modo
        self._refrescar()

    def _refrescar(self):
        """Reconstruye la lista mostrando solo criterios con capa vectorial cargada."""
        while self._layout_mapeo.count():
            item = self._layout_mapeo.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        candidatos = [
            c for c in self._criterios_datos
            if c.activo and c.capa is not None and hasattr(c.capa, "fields")
        ]

        if not candidatos:
            self._etq_sin_capas = QLabel(
                "<i>No hay capas vectoriales manuales cargadas todavía.<br>"
                "Cargue archivos en el Paso 3 y luego regrese aquí.</i>"
            )
            self._etq_sin_capas.setStyleSheet(
            f"color: {COLOR_TEXTO_SUAVE}; font-size: {FS_META}px;"
        )
            self._etq_sin_capas.setWordWrap(True)
            self._layout_mapeo.addWidget(self._etq_sin_capas)
        else:
            self._layout_mapeo.addWidget(_cabecera_tabla([
                ("Criterio",       self._COL_NOMBRE, Qt.AlignLeft),
                ("Mapeo aplicado", self._COL_ESTADO, Qt.AlignLeft),
                ("Configuración",  self._COL_ACCION, Qt.AlignLeft),
            ]))
            for i, criterio in enumerate(candidatos):
                self._layout_mapeo.addWidget(self._crear_fila_mapeo(criterio, i))

        self._layout_mapeo.addStretch()

    # ── Estado del mapeo por criterio ────────────────────────────────────
    _COL_NOMBRE = 320
    _COL_ESTADO = 220
    _COL_ACCION = 156

    _ESTILO_CHIP_OK = (
        "background: #E8F1E9; color: #2E7D32; border: 1px solid #C3DEC6; "
        f"border-radius: 9px; padding: 1px 8px; font-size: {FS_MICRO}px;"
    )
    _ESTILO_CHIP_DEFECTO = (
        f"background: transparent; color: {COLOR_SLATE_TEXTO}; "
        f"font-size: {FS_CUERPO}px; font-weight: bold;"
    )

    @staticmethod
    def _estado_mapeo(criterio) -> tuple[str, str, str]:
        """Devuelve (texto, estilo, tooltip) del chip de estado del criterio."""
        if criterio.filtro_tipo == "continuo":
            return (
                f"Continuo · {criterio.filtro_campo}",
                PaginaMapeo._ESTILO_CHIP_OK,
                f"Mapeo continuo sobre el campo «{criterio.filtro_campo}».",
            )
        if criterio.filtro_tipo == "categorico":
            return (
                f"Categórico · {criterio.filtro_campo}",
                PaginaMapeo._ESTILO_CHIP_OK,
                f"Mapeo categórico sobre el campo «{criterio.filtro_campo}».",
            )
        return (
            "†",
            PaginaMapeo._ESTILO_CHIP_DEFECTO,
            "Sin mapeo configurado: todas las geometrías de la capa se tratan "
            "como prohibitivas.",
        )

    def _crear_fila_mapeo(self, criterio, indice: int = 0) -> QWidget:
        w, row = _fila_tabla(indice)

        row.addWidget(_celda(
            criterio.nombre, self._COL_NOMBRE,
            tooltip=f"<b>{criterio.nombre}</b><br><br>{criterio.descripcion}",
        ))

        # Columna de estado — chip alineado a la izquierda en ancho fijo
        texto, estilo, tip = self._estado_mapeo(criterio)
        estado_w = QWidget()
        estado_w.setFixedWidth(self._COL_ESTADO)
        estado_w.setStyleSheet("background: transparent;")
        estado_l = QHBoxLayout(estado_w)
        estado_l.setContentsMargins(0, 0, 0, 0)
        etq_estado = QLabel(texto)
        etq_estado.setStyleSheet(estilo)
        etq_estado.setToolTip(tip)
        estado_l.addWidget(etq_estado)
        estado_l.addStretch()
        row.addWidget(estado_w)

        accion_w = QWidget()
        accion_w.setFixedWidth(self._COL_ACCION)
        accion_w.setStyleSheet("background: transparent;")
        accion_l = QHBoxLayout(accion_w)
        accion_l.setContentsMargins(0, 0, 0, 0)
        btn = QPushButton("Configurar mapeo…")
        btn.setStyleSheet(ESTILO_BOTON_SECUNDARIO)
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(lambda checked=False, c=criterio, e=etq_estado:
                            self._abrir_mapeo(c, e))
        accion_l.addWidget(btn)
        row.addWidget(accion_w)

        row.addStretch(1)
        return w

    def _abrir_mapeo(self, criterio, etq_estado):
        """Abre el diálogo de mapeo y actualiza la etiqueta de estado."""
        from .dialogo_mapeo import DialogoMapeoColumna
        dlg = DialogoMapeoColumna(
            criterio.capa, criterio,
            modo=self._modo,
            parent=self,
        )
        dlg.exec_()
        texto, estilo, tip = self._estado_mapeo(criterio)
        etq_estado.setText(texto)
        etq_estado.setStyleSheet(estilo)
        etq_estado.setToolTip(tip)


# ===========================================================================
# PÁGINA 6 — Ejecución y resultados
# ===========================================================================

class PaginaResultados(QWidget):
    """Ejecuta el análisis y muestra los resultados."""

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self._motor: Optional[MotorAnalisis] = None
        self._hilo: Optional[HiloAnalisis] = None
        self._criterios_usados: list = []
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        layout.addWidget(_etiqueta_titulo("Resultados del análisis"))
        layout.addWidget(_separador())

        self.btn_ejecutar = QPushButton("▶  Ejecutar análisis")
        self.btn_ejecutar.setStyleSheet(ESTILO_BOTON_PRIMARIO)
        self.btn_ejecutar.setMinimumHeight(40)
        self.btn_ejecutar.clicked.connect(self._ejecutar)
        layout.addWidget(self.btn_ejecutar)

        self.barra = QProgressBar()
        self.barra.setRange(0, 100)
        self.barra.setValue(0)
        layout.addWidget(self.barra)

        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setStyleSheet(
            "QTextEdit { font-family: Menlo, Consolas, monospace; "
            f"font-size: {FS_MICRO}px; color: {COLOR_TEXTO}; "
            f"background: #FCFDFD; border: 1px solid {COLOR_BORDE}; "
            "border-radius: 4px; padding: 6px; }"
        )
        self.txt_log.setMinimumHeight(180)
        layout.addWidget(self.txt_log)

        layout.addWidget(_separador())

        # Resumen (oculto hasta terminar)
        self.frame_resumen = QFrame()
        self.frame_resumen.setVisible(False)
        res_layout = QVBoxLayout(self.frame_resumen)
        res_layout.setContentsMargins(0, 0, 0, 0)
        res_layout.setSpacing(8)
        res_layout.addWidget(_etiqueta_seccion("Resumen del análisis"))

        # Tabla de resumen en HTML: seleccionable y copiable por el usuario
        self.txt_resumen = QTextEdit()
        self.txt_resumen.setReadOnly(True)
        self.txt_resumen.setMinimumHeight(280)
        self.txt_resumen.setStyleSheet(
            f"QTextEdit {{ background: #FFFFFF; color: {COLOR_TEXTO}; "
            f"border: 1px solid {COLOR_BORDE}; border-radius: 4px; padding: 4px; }}"
        )
        res_layout.addWidget(self.txt_resumen)

        # Botón exportar reporte
        btn_reporte_layout = QHBoxLayout()
        self.btn_reporte = QPushButton("Exportar reporte HTML…")
        self.btn_reporte.setStyleSheet(ESTILO_BOTON_SECUNDARIO)
        self.btn_reporte.setCursor(Qt.PointingHandCursor)
        self.btn_reporte.setEnabled(False)
        self.btn_reporte.clicked.connect(self._exportar_reporte)
        btn_reporte_layout.addWidget(self.btn_reporte)

        self.btn_copiar_resumen = QPushButton("Copiar resumen")
        self.btn_copiar_resumen.setStyleSheet(ESTILO_BOTON_CONTORNO)
        self.btn_copiar_resumen.setCursor(Qt.PointingHandCursor)
        self.btn_copiar_resumen.setEnabled(False)
        self.btn_copiar_resumen.clicked.connect(self._copiar_resumen)
        btn_reporte_layout.addWidget(self.btn_copiar_resumen)

        btn_reporte_layout.addStretch()
        res_layout.addLayout(btn_reporte_layout)

        layout.addWidget(self.frame_resumen)

        layout.addStretch()
        self._capas_resultado: dict = {}   # almacena capas para el reporte

    def configurar_motor(
        self,
        aoi_layer: QgsVectorLayer,
        criterios: list[Criterio],
        directorio: str,
        modo_analisis: str = "dicotomico",
    ):
        self._motor = MotorAnalisis(aoi_layer, criterios, directorio,
                                    modo_analisis=modo_analisis)
        self._criterios_usados = criterios
        self._capas_resultado = {}

    def _ejecutar(self):
        if self._motor is None:
            QMessageBox.critical(self, "Error", "No se configuró el motor de análisis.")
            return

        self.btn_ejecutar.setEnabled(False)
        self.txt_log.clear()
        self.barra.setValue(0)
        self.frame_resumen.setVisible(False)

        self._hilo = HiloAnalisis(self._motor)
        self._hilo.progreso.connect(self._on_progreso)
        self._hilo.fase1_lista.connect(self._on_fase1_lista)
        self._hilo.fase2_lista.connect(self._on_fase2_lista)
        self._hilo.error.connect(self._on_error)
        self._hilo.finished.connect(self._on_hilo_terminado)
        self._hilo.start()

    def _on_hilo_terminado(self):
        """Publica el resumen y rehabilita el botón al terminar el hilo.

        Se hace aquí y no al concluir la ponderación porque en modo dicotómico
        esa etapa no se ejecuta, y el resumen debe aparecer en ambos modos.
        """
        try:
            self.btn_ejecutar.setEnabled(True)
            self.barra.setValue(100)
            self.txt_log.append("")
            self.txt_log.append("═══ ANÁLISIS TERMINADO ═══")
            self.txt_log.append(
                "Consulte el resumen de abajo; puede copiarlo o exportarlo como reporte."
            )
            self.frame_resumen.setVisible(True)
            self.btn_reporte.setEnabled(True)
            self.btn_copiar_resumen.setEnabled(True)
            self.txt_resumen.setHtml(self._html_resumen())
        except RuntimeError:
            # El diálogo fue cerrado antes de que el hilo terminara; ignorar.
            pass

    def _on_progreso(self, pct: int, msg: str):
        if pct >= 0:
            self.barra.setValue(pct)
        if msg:
            self.txt_log.append(msg)

    def _on_fase1_lista(self, capa_excluida, capa_apta, capas_criterios):
        root = QgsProject.instance().layerTreeRoot()

        # ── Grupo de criterios de exclusión (colapsado, abajo) ──────────────
        if capas_criterios:
            grupo = root.addGroup("Criterios de exclusión — RSU")
            grupo.setExpanded(False)
            for nombre, capa in capas_criterios.items():
                capa.setName(nombre)
                _aplicar_estilo_criterio(capa)
                QgsProject.instance().addMapLayer(capa, False)
                grupo.addLayer(capa)
            self._capas_resultado["criterios"] = capas_criterios

        # ── Capas de resultado (arriba del grupo) ───────────────────────────
        if capa_excluida:
            capa_excluida.setName("Zonas excluidas — RSU")
            aplicar_estilo_excluidas(capa_excluida)
            QgsProject.instance().addMapLayer(capa_excluida)
            self._capas_resultado["excluida"] = capa_excluida
        if capa_apta:
            capa_apta.setName("Zonas aptas (preliminar) — RSU")
            aplicar_estilo_aptas(capa_apta)
            QgsProject.instance().addMapLayer(capa_apta)
            self._capas_resultado["apta"] = capa_apta

        n_crit = len(capas_criterios) if capas_criterios else 0
        self.txt_log.append(
            f"  Capas añadidas al proyecto: {n_crit} de criterios, "
            f"más zonas excluidas y zonas aptas."
        )

    def _on_fase2_lista(self, raster_aptitud):
        if raster_aptitud:
            raster_aptitud.setName("Aptitud ponderada — RSU")
            # Registrar el nodata ANTES de calcular estadísticas para la simbología,
            # de lo contrario QGIS incluye las celdas −9999 y la leyenda muestra
            # valores absurdos (±1.79e+308).
            raster_aptitud.dataProvider().setNoDataValue(1, -9999.0)
            aplicar_pseudocolor_aptitud(raster_aptitud)
            QgsProject.instance().addMapLayer(raster_aptitud)
            self._capas_resultado["aptitud"] = raster_aptitud
            self.txt_log.append(
                "  Ráster de aptitud añadido al proyecto."
            )

    # ------------------------------------------------------------------
    # Resumen final
    # ------------------------------------------------------------------

    _FUENTE_ETIQUETA = {
        "inegi_dl":   "INEGI — descarga directa",
        "conanp":     "CONANP",
        "conabio":    "CONABIO",
        "osm":        "OpenStreetMap (Overpass)",
        "copernicus": "Copernicus DEM GLO-30",
        "manual":     "Provisto por el usuario",
    }

    def _html_resumen(self) -> str:
        """Construye la tabla de resumen del análisis en HTML."""
        motor = self._motor
        areas = dict(getattr(motor, "resumen_areas", {}) or {})
        detalle = {d["id"]: d for d in getattr(motor, "detalle_criterios", []) or []}
        criterios = self._criterios_usados or []

        modo = areas.get("modo", getattr(motor, "modo_analisis", "dicotomico"))
        modo_txt = ("Ponderado — aptitud relativa"
                    if modo == "ponderado"
                    else "Dicotómico — prohibido / permitido")

        def num(v, dec=2):
            return f"{v:,.{dec}f}".replace(",", " ")

        # ── Métricas globales ───────────────────────────────────────────
        a_aoi = areas.get("aoi_km2", 0.0)
        a_exc = areas.get("excluida_km2", 0.0)
        a_apt = areas.get("apta_km2", 0.0)
        # Acotar a [0, 100]: el motor ya lo hace, pero el resumen puede recibir
        # un dict de una corrida previa sin ese recorte.
        p_exc = min(max(areas.get("pct_excluida", 0.0), 0.0), 100.0)
        p_apt = 100.0 - p_exc if areas else 0.0

        metricas = [
            ("Tipo de análisis", modo_txt, ""),
            ("Área de interés", f"{num(a_aoi)} km²", "100 %"),
            ("Área prohibida", f"{num(a_exc)} km²", f"{num(p_exc, 1)} %"),
            ("Área permitida", f"{num(a_apt)} km²", f"{num(p_apt, 1)} %"),
            ("Criterios evaluados como exclusión",
             str(areas.get("n_criterios", len(detalle))), ""),
        ]
        filas_met = "".join(
            f"<tr><td class='k'>{k}</td>"
            f"<td class='v'>{v}</td><td class='p'>{p}</td></tr>"
            for k, v, p in metricas
        )

        aviso = ""
        if areas and a_apt <= 0.0:
            aviso = (
                "<div class='alerta'><b>No queda superficie apta.</b> "
                "Los criterios activos cubren el área de interés por completo. "
                "Amplíe el área de interés, revise las distancias de buffer o "
                "desactive criterios que no apliquen al caso.</div>"
            )

        # ── Tabla de criterios ──────────────────────────────────────────
        filas_crit = []
        for c in criterios:
            if not c.activo:
                continue
            cargado = c.capa is not None
            origen = getattr(c, "origen_carga", "")
            if origen == "manual":
                origen_txt = "<span class='man'>Manual</span>"
                fuente_txt = "Archivo del usuario"
                ruta = getattr(c, "ruta_dato", None)
                if ruta:
                    fuente_txt = f"Archivo del usuario<br><span class='ruta'>{ruta}</span>"
            elif origen == "automatica":
                origen_txt = "<span class='aut'>Automática</span>"
                fuente_txt = self._FUENTE_ETIQUETA.get(
                    getattr(c.fuente, "value", ""), "—")
            else:
                origen_txt = "<span class='non'>Sin cargar</span>"
                fuente_txt = self._FUENTE_ETIQUETA.get(
                    getattr(c.fuente, "value", ""), "—")

            d = detalle.get(c.id)
            if d:
                regla = d["parametro"]
                aporte = (f"{num(d['area_km2'])} km²"
                          if d["area_km2"] > 0 else "—")
                estado = d["resultado"]
            else:
                regla = "—"
                aporte = "—"
                if not cargado:
                    estado = "sin datos cargados"
                elif modo == "ponderado" and c.rol_ponderado == "ponderado":
                    estado = f"ponderado, peso {num(c.peso, 1)} %"
                else:
                    estado = "no participó en la exclusión"

            norma = c.referencia_normativa or "—"
            filas_crit.append(
                f"<tr><td>{c.nombre}</td><td class='sm'>{norma}</td>"
                f"<td class='sm'>{fuente_txt}</td><td class='ctr'>{origen_txt}</td>"
                f"<td class='sm'>{regla}</td><td class='num'>{aporte}</td>"
                f"<td class='sm'>{estado}</td></tr>"
            )

        if not filas_crit:
            filas_crit.append(
                "<tr><td colspan='7' class='sm'>Ningún criterio activo.</td></tr>")

        # ── Capas generadas ─────────────────────────────────────────────
        capas = []
        if "excluida" in self._capas_resultado:
            capas.append("<b>Zonas excluidas — RSU</b>: superficie donde la "
                         "instalación queda descartada por al menos un criterio.")
        if "apta" in self._capas_resultado:
            capas.append("<b>Zonas aptas (preliminar) — RSU</b>: superficie que "
                         "no incumple ningún criterio de exclusión evaluado.")
        if "criterios" in self._capas_resultado:
            capas.append(f"<b>Criterios de exclusión — RSU</b>: grupo con "
                         f"{len(self._capas_resultado['criterios'])} capas, una por "
                         f"criterio que aportó exclusión.")
        if "aptitud" in self._capas_resultado:
            capas.append("<b>Aptitud ponderada — RSU</b>: ráster de aptitud "
                         "relativa (0–1) recortado al área permitida.")
        lista_capas = "".join(f"<li>{c}</li>" for c in capas) or "<li>—</li>"

        return f"""<html><head><style>
body {{ font-family: -apple-system, 'Segoe UI', sans-serif;
        font-size: {FS_META}px; color: {COLOR_TEXTO}; }}
h3   {{ font-size: {FS_MICRO}px; text-transform: uppercase;
        color: {COLOR_SLATE_TEXTO}; margin: 14px 0 5px 0;
        letter-spacing: 0.4px; }}
table {{ border-collapse: collapse; width: 100%; }}
th   {{ background: {COLOR_CABECERA}; color: {COLOR_SLATE_TEXTO};
        font-size: {FS_MICRO}px; text-transform: uppercase; text-align: left;
        padding: 5px 7px; border-bottom: 1px solid {COLOR_BORDE}; }}
td   {{ padding: 5px 7px; border-bottom: 1px solid #EDF0F2;
        vertical-align: top; }}
td.k {{ color: {COLOR_SLATE_TEXTO}; width: 46%; }}
td.v {{ font-weight: bold; width: 30%; }}
td.p {{ color: {COLOR_TEXTO_SUAVE}; text-align: right; }}
td.sm   {{ font-size: {FS_MICRO}px; color: {COLOR_SLATE_TEXTO}; }}
td.num  {{ text-align: right; font-weight: bold; }}
td.ctr  {{ text-align: center; }}
span.ruta {{ font-size: 9px; color: {COLOR_TEXTO_SUAVE}; }}
span.aut {{ color: {COLOR_PRIMARIO}; font-size: {FS_MICRO}px; font-weight: bold; }}
span.man {{ color: {COLOR_INFO}; font-size: {FS_MICRO}px; font-weight: bold; }}
span.non {{ color: {COLOR_TEXTO_SUAVE}; font-size: {FS_MICRO}px; }}
div.alerta {{ background: #FDECEA; border-left: 3px solid {COLOR_EXCLUIDO};
        padding: 8px 10px; margin-bottom: 10px; font-size: {FS_META}px; }}
ul   {{ margin: 4px 0 0 16px; padding: 0; }}
li   {{ margin-bottom: 3px; }}
</style></head><body>

{aviso}
<h3>Balance general</h3>
<table>{filas_met}</table>

<h3>Criterios y procedencia de los datos</h3>
<table>
<tr><th>Criterio</th><th>Referencia</th><th>Fuente</th><th>Carga</th>
    <th>Regla aplicada</th><th>Área aportada</th><th>Resultado</th></tr>
{''.join(filas_crit)}
</table>

<h3>Capas añadidas al proyecto</h3>
<ul>{lista_capas}</ul>

</body></html>"""

    def _copiar_resumen(self):
        """Copia el resumen al portapapeles como texto plano."""
        QApplication.clipboard().setText(self.txt_resumen.toPlainText())
        self.btn_copiar_resumen.setText("Copiado ✓")
        QTimer.singleShot(
            2000, lambda: self.btn_copiar_resumen.setText("Copiar resumen")
        )

    def _on_error(self, msg: str):
        self.txt_log.append(f"[ERROR] {msg}")
        QMessageBox.critical(self, "Error en el análisis", msg)

    def _exportar_reporte(self):
        """Genera y abre un reporte HTML del análisis."""
        from ..utils.reportes import generar_reporte_html
        ruta, _ = QFileDialog.getSaveFileName(
            self, "Guardar reporte",
            os.path.expanduser("~"),
            "Reporte HTML (*.html)"
        )
        if not ruta:
            return
        try:
            generar_reporte_html(
                ruta_salida=ruta,
                criterios=self._criterios_usados,
                capas_resultado=self._capas_resultado,
            )
            import webbrowser
            webbrowser.open(f"file://{ruta}")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Error al exportar", str(exc))


# ===========================================================================
# ASISTENTE PRINCIPAL
# ===========================================================================

class AsistenteEvaluacionRSU(QDialog):
    """Diálogo principal multi-paso del plugin."""

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.setWindowTitle("Evaluación multicriterio — Selección de sitios RSU (NOM-083)")
        self.setMinimumSize(820, 640)
        self.resize(900, 700)

        self._pagina_actual = 0
        self._build_ui()
        self._actualizar_navegacion()

    def _build_ui(self):
        layout_principal = QVBoxLayout(self)
        layout_principal.setContentsMargins(0, 0, 0, 0)

        # Barra de progreso del wizard (indicador de pasos)
        barra_pasos = self._crear_barra_pasos()
        layout_principal.addWidget(barra_pasos)

        # Contenido (QStackedWidget)
        self.stack = QStackedWidget()
        self.pag_aoi = PaginaAOI(self.iface)
        self.pag_criterios = PaginaCriterios()
        self.pag_datos = PaginaDatos(self.iface)
        self.pag_exclusion = PaginaExclusion()
        self.pag_ponderacion = PaginaPonderacion()
        self.pag_mapeo = PaginaMapeo()
        self.pag_resultados = PaginaResultados(self.iface)

        for pag in (
            self.pag_aoi,
            self.pag_criterios,
            self.pag_datos,
            self.pag_exclusion,
            self.pag_ponderacion,
            self.pag_mapeo,
            self.pag_resultados,
        ):
            w = QWidget()
            w_layout = QVBoxLayout(w)
            w_layout.setContentsMargins(20, 16, 20, 8)
            w_layout.addWidget(pag)
            self.stack.addWidget(w)

        layout_principal.addWidget(self.stack)

        # Navegación inferior — dos filas:
        #   fila 1: Cerrar | <espacio> | Atrás | Siguiente
        #   fila 2: Copiar referencia bibliográfica (centrado)
        nav = QWidget()
        nav.setStyleSheet(
            f"background-color: {COLOR_FONDO}; "
            f"border-top: 1px solid {COLOR_BORDE};"
        )
        nav_vbox = QVBoxLayout(nav)
        nav_vbox.setContentsMargins(20, 10, 20, 10)
        nav_vbox.setSpacing(6)

        # — Fila 1: botones de navegación —
        nav_layout = QHBoxLayout()
        nav_layout.setContentsMargins(0, 0, 0, 0)

        self.btn_atras = QPushButton("◀  Atrás")
        self.btn_siguiente = QPushButton("Siguiente  ▶")
        self.btn_siguiente.setStyleSheet(ESTILO_BOTON_PRIMARIO)
        self.btn_cerrar = QPushButton("Cerrar")

        self.btn_atras.clicked.connect(self._pagina_anterior)
        self.btn_siguiente.clicked.connect(self._pagina_siguiente)
        self.btn_cerrar.clicked.connect(self.reject)

        nav_layout.addWidget(self.btn_cerrar)
        nav_layout.addStretch()
        nav_layout.addWidget(self.btn_atras)
        nav_layout.addWidget(self.btn_siguiente)
        nav_vbox.addLayout(nav_layout)

        # — Fila 2: referencia bibliográfica —
        cita_layout = QHBoxLayout()
        cita_layout.setContentsMargins(0, 0, 0, 0)
        self.btn_cita = QPushButton("📋  Copiar referencia bibliográfica")
        self.btn_cita.setToolTip(
            "Copia la referencia académica (APA + BibTeX) al portapapeles"
        )
        self.btn_cita.setStyleSheet(
            f"QPushButton {{ padding: 4px 12px; font-size: {FS_META}px; "
            "background: #E8F1E9; color: #1B5E20; border: 1px solid #C3DEC6; "
            "border-radius: 4px; }} "
            "QPushButton:hover { background: #DCEADE; } "
            "QPushButton:pressed { background: #C3DEC6; }"
        )
        self.btn_cita.setCursor(Qt.PointingHandCursor)
        self.btn_cita.clicked.connect(self._copiar_cita)
        cita_layout.addStretch()
        cita_layout.addWidget(self.btn_cita)
        cita_layout.addStretch()
        nav_vbox.addLayout(cita_layout)

        layout_principal.addWidget(nav)

    # Estilos de la barra de pasos
    _ESTILO_PASO_INACTIVO = (
        f"color: rgba(255,255,255,0.55); font-size: {FS_MICRO}px; "
        "padding: 3px 9px;"
    )
    _ESTILO_PASO_HECHO = (
        f"color: rgba(255,255,255,0.85); font-size: {FS_MICRO}px; "
        "padding: 3px 9px;"
    )
    _ESTILO_PASO_ACTIVO = (
        f"color: white; font-size: {FS_MICRO}px; font-weight: bold; "
        "background-color: rgba(255,255,255,0.22); "
        "border-radius: 10px; padding: 3px 9px;"
    )

    def _crear_barra_pasos(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet(f"background-color: {COLOR_PRIMARIO};")
        layout = QHBoxLayout(w)
        layout.setContentsMargins(16, 8, 16, 8)
        layout.setSpacing(4)

        self._indicadores_pasos: list[QLabel] = []
        pasos = ["Área de interés", "Criterios", "Datos", "Exclusión", "Ponderación", "Mapeo", "Resultados"]

        for i, nombre in enumerate(pasos):
            lbl = QLabel(f"{i+1}. {nombre}")
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet(self._ESTILO_PASO_INACTIVO)
            self._indicadores_pasos.append(lbl)
            layout.addWidget(lbl)
            if i < len(pasos) - 1:
                sep = QLabel("›")
                sep.setStyleSheet("color: rgba(255,255,255,0.3);")
                layout.addWidget(sep)

        return w

    def _actualizar_navegacion(self):
        n = self._pagina_actual
        total = self.stack.count() - 1

        self.btn_atras.setEnabled(n > 0)
        self.btn_siguiente.setText("Siguiente  ▶" if n < total else "Finalizar")

        # Resaltar paso actual
        for i, lbl in enumerate(self._indicadores_pasos):
            if i == n:
                lbl.setStyleSheet(self._ESTILO_PASO_ACTIVO)
            elif i < n:
                lbl.setStyleSheet(self._ESTILO_PASO_HECHO)
            else:
                lbl.setStyleSheet(self._ESTILO_PASO_INACTIVO)

    def _pagina_siguiente(self):
        n = self._pagina_actual

        # Validaciones al avanzar
        if n == 0:
            # Paso 1 (AOI) → Paso 2 (Criterios)
            ok, msg = self.pag_aoi.es_valido()
            if not ok:
                QMessageBox.warning(self, "Datos incompletos", msg)
                return

        elif n == 1:
            # Paso 2 (Criterios) → Paso 3 (Datos)
            # Inicializar descargador (idempotente — no borra capas ya cargadas)
            self.pag_datos.inicializar_descargador(
                self.pag_aoi.aoi_layer(),
                self.pag_aoi.directorio_trabajo(),
            )
            # Refrescar la lista de datos para mostrar solo criterios activos
            self.pag_datos.refrescar_criterios()

        elif n == 2:
            # Paso 3 (Datos) → Paso 4 (Exclusión)
            self.pag_exclusion.set_modo(self.pag_criterios.modo_analisis())

        elif n == 3:
            # Paso 4 (Exclusión) → Paso 5 (Ponderación)
            self.pag_ponderacion.set_modo(self.pag_criterios.modo_analisis())

        elif n == 4:
            # Paso 5 (Ponderación) → Paso 6 (Mapeo)
            ok, msg = self.pag_ponderacion.pesos_validos()
            if not ok:
                QMessageBox.warning(self, "Pesos incorrectos", msg)
                return
            self.pag_mapeo.configurar(
                self.pag_datos.criterios,
                self.pag_criterios.modo_analisis(),
            )

        elif n == 5:
            # Paso 6 (Mapeo) → Paso 7 (Resultados)
            # Sincronizar activo / peso / buffer_m / rol_ponderado desde CRITERIOS_DEFAULT
            # (PaginaExclusion, PaginaPonderacion y PaginaCriterios lo modifican directamente)
            # hacia pag_datos.criterios (la copia profunda que tiene .capa cargada).
            src_por_id = {c.id: c for c in CRITERIOS_DEFAULT}
            for c in self.pag_datos.criterios:
                src = src_por_id.get(c.id)
                if src is not None:
                    c.activo = src.activo
                    c.peso = src.peso
                    c.buffer_m = src.buffer_m
                    c.rol_ponderado = src.rol_ponderado

            # Configurar motor de análisis con modo seleccionado
            self.pag_resultados.configurar_motor(
                self.pag_aoi.aoi_layer(),
                self.pag_datos.criterios,
                self.pag_aoi.directorio_trabajo(),
                modo_analisis=self.pag_criterios.modo_analisis(),
            )

        elif n == 6:  # Última página (Resultados)
            self.accept()
            return

        self._pagina_actual += 1
        self.stack.setCurrentIndex(self._pagina_actual)
        self._actualizar_navegacion()

    def _copiar_cita(self):
        """Copia la referencia académica (APA + BibTeX) al portapapeles."""
        cita_apa = (
            "López Olvera, S. (2024). EvaluaciónRSU: Plugin QGIS para la selección "
            "multicriterio de sitios de disposición final de RSU conforme a la "
            "NOM-083-SEMARNAT-2003 (Versión 1.0.0) [Software]. "
            "https://github.com/sergio-lopez-olvera/evaluacion-rsu"
        )
        cita_bibtex = (
            "@software{LopezOlvera2024EvaluacionRSU,\n"
            "  author    = {López Olvera, Sergio},\n"
            "  title     = {{EvaluaciónRSU}: {Plugin QGIS} para la selección\n"
            "               multicriterio de sitios de disposición final de\n"
            "               {RSU} conforme a la {NOM-083-SEMARNAT-2003}},\n"
            "  year      = {2024},\n"
            "  version   = {1.0.0},\n"
            "  license   = {GPL-3.0-or-later},\n"
            "  url       = {https://github.com/sergio-lopez-olvera/evaluacion-rsu},\n"
            "}"
        )
        texto = f"=== Referencia APA ===\n{cita_apa}\n\n=== BibTeX ===\n{cita_bibtex}"
        QApplication.clipboard().setText(texto)
        # Retroalimentación visual
        self.btn_cita.setText("✅  ¡Referencia copiada!")
        QTimer.singleShot(2500, lambda: self.btn_cita.setText("📋  Copiar referencia bibliográfica"))

    def _pagina_anterior(self):
        if self._pagina_actual > 0:
            self._pagina_actual -= 1
            self.stack.setCurrentIndex(self._pagina_actual)
            self._actualizar_navegacion()
