# -*- coding: utf-8 -*-
# EvaluaciónRSU — Plugin QGIS para la selección de sitios de disposición final de RSU
# Copyright (C) 2024-2025  Sergio López Olvera <lopezolverasergio@gmail.com>
# SPDX-License-Identifier: GPL-3.0-or-later
# Repository: https://github.com/sergio-lopez-olvera/evaluacion-rsu

"""
Diálogo de mapeo de columna para capas cargadas manualmente.

Permite al usuario:
  - Seleccionar el campo de la capa que contiene el atributo de interés.
  - Para campos numéricos (continuo): definir un rango [min, max] que se
    considera "prohibitivo" para el análisis de exclusión.
  - Para campos de texto (categórico): asignar a cada valor único la
    etiqueta "prohibitivo" o "permisivo".

El resultado se almacena directamente en los atributos filtro_* del Criterio.
"""

from __future__ import annotations

from typing import Optional

try:
    from qgis.PyQt.QtWidgets import (
        QDialog, QDialogButtonBox, QVBoxLayout, QHBoxLayout,
        QLabel, QComboBox, QDoubleSpinBox, QSpinBox, QRadioButton,
        QButtonGroup, QGroupBox, QScrollArea, QWidget,
        QSizePolicy, QFrame,
    )
    from qgis.PyQt.QtCore import Qt
    from qgis.core import QgsVectorLayer
    _QGIS_DISPONIBLE = True
except ImportError:
    _QGIS_DISPONIBLE = False


# ---------------------------------------------------------------------------
# Diálogo principal
# ---------------------------------------------------------------------------

class DialogoMapeoColumna(QDialog):
    """Abre un diálogo para configurar el mapeo de atributos de una capa.

    Uso::

        dlg = DialogoMapeoColumna(capa, criterio, parent=self)
        if dlg.exec_() == QDialog.Accepted:
            # criterio.filtro_* ya fue actualizado por el diálogo
            pass
    """

    def __init__(self, capa, criterio, modo: str = "dicotomico", parent=None):
        super().__init__(parent)
        self._capa = capa
        self._criterio = criterio
        self._modo = modo   # "dicotomico" o "ponderado"
        self._radio_grupos: dict[str, QButtonGroup] = {}   # valor → QButtonGroup
        self._spin_puntaje: dict[str, QSpinBox] = {}       # valor → QSpinBox (0-100)
        self._widget_continuo: Optional[QWidget] = None
        self._widget_categorico: Optional[QWidget] = None

        self.setWindowTitle(f"Mapeo de atributos — {criterio.nombre}")
        self.setMinimumWidth(520)
        self.setMinimumHeight(380)

        self._build_ui()
        self._poblar_campos()

    # ------------------------------------------------------------------
    # Construcción de la interfaz
    # ------------------------------------------------------------------

    def _build_ui(self):
        from .wizard import (
            COLOR_BORDE as _COLOR_BORDE,
            COLOR_SLATE_TEXTO as _COLOR_SLATE_TEXTO,
            FS_META as _FS_META,
        )
        layout_principal = QVBoxLayout(self)
        layout_principal.setSpacing(12)

        # Descripción
        lbl_desc = QLabel(
            "Configure cómo se usan los atributos de esta capa en el análisis.\n"
            "Solo aplica cuando la fuente es carga manual."
        )
        lbl_desc.setWordWrap(True)
        lbl_desc.setStyleSheet(f"font-size: {_FS_META}px; color: {_COLOR_SLATE_TEXTO};")
        layout_principal.addWidget(lbl_desc)

        # Separador
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        sep.setStyleSheet(f"color: {_COLOR_BORDE};")
        layout_principal.addWidget(sep)

        # Selector de campo
        fila_campo = QHBoxLayout()
        fila_campo.addWidget(QLabel("Campo de interés:"))
        self._cmb_campo = QComboBox()
        self._cmb_campo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        fila_campo.addWidget(self._cmb_campo)
        layout_principal.addLayout(fila_campo)

        # Selector de tipo de variable
        grp_tipo = QGroupBox("Tipo de variable")
        fila_tipo = QHBoxLayout(grp_tipo)
        self._rad_sin_filtro = QRadioButton("Sin filtro (usar todas las geometrías)")
        self._rad_continua = QRadioButton("Continua (umbral numérico)")
        self._rad_categorica = QRadioButton("Categórica (codificación de valores)")
        fila_tipo.addWidget(self._rad_sin_filtro)
        fila_tipo.addWidget(self._rad_continua)
        fila_tipo.addWidget(self._rad_categorica)
        layout_principal.addWidget(grp_tipo)

        # Panel de configuración continua
        self._widget_continuo = self._crear_panel_continuo()
        layout_principal.addWidget(self._widget_continuo)

        # Panel de configuración categórica (scroll)
        self._grp_categorico = QGroupBox("Asignación de valores")
        layout_cat_ext = QVBoxLayout(self._grp_categorico)
        self._scroll_cat = QScrollArea()
        self._scroll_cat.setWidgetResizable(True)
        self._scroll_cat.setMinimumHeight(180)
        self._scroll_cat.setFrameShape(QFrame.NoFrame)
        self._contenedor_cat = QWidget()
        self._layout_cat = QVBoxLayout(self._contenedor_cat)
        self._layout_cat.setSpacing(0)
        self._layout_cat.setContentsMargins(0, 0, 0, 0)
        self._scroll_cat.setWidget(self._contenedor_cat)
        layout_cat_ext.addWidget(self._scroll_cat)
        layout_principal.addWidget(self._grp_categorico)

        # Botones OK / Cancelar
        botones = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        botones.accepted.connect(self._aceptar)
        botones.rejected.connect(self.reject)
        layout_principal.addWidget(botones)

        # Conexiones de visibilidad
        self._rad_sin_filtro.toggled.connect(self._actualizar_visibilidad)
        self._rad_continua.toggled.connect(self._actualizar_visibilidad)
        self._rad_categorica.toggled.connect(self._actualizar_visibilidad)
        self._cmb_campo.currentIndexChanged.connect(self._on_campo_cambiado)

        # Estado inicial
        self._rad_sin_filtro.setChecked(True)
        self._actualizar_visibilidad()

    def _crear_panel_continuo(self) -> QGroupBox:
        grp = QGroupBox("Rango prohibitivo (ambos extremos inclusive)")
        layout = QHBoxLayout(grp)

        layout.addWidget(QLabel("Valor mínimo:"))
        self._spin_min = QDoubleSpinBox()
        self._spin_min.setRange(-1e9, 1e9)
        self._spin_min.setDecimals(4)
        self._spin_min.setValue(0.0)
        layout.addWidget(self._spin_min)

        layout.addWidget(QLabel("  Valor máximo:"))
        self._spin_max = QDoubleSpinBox()
        self._spin_max.setRange(-1e9, 1e9)
        self._spin_max.setDecimals(4)
        self._spin_max.setValue(50.0)
        layout.addWidget(self._spin_max)

        layout.addStretch()
        lbl_nota = QLabel(
            "<small>Las geometrías cuyos valores queden dentro de este rango<br>"
            "se considerarán <b>prohibitivas</b> (excluidas del análisis).</small>"
        )
        lbl_nota.setTextFormat(Qt.RichText)
        layout.addWidget(lbl_nota)

        return grp

    # ------------------------------------------------------------------
    # Llenado dinámico
    # ------------------------------------------------------------------

    def _poblar_campos(self):
        """Llena el combo con los campos de la capa y restaura el estado previo."""
        self._cmb_campo.clear()
        self._cmb_campo.addItem("— ninguno —", None)

        if self._capa is None:
            return

        campos = self._capa.fields()
        for campo in campos:
            self._cmb_campo.addItem(campo.name(), campo.name())

        # Restaurar configuración previa si existe
        criterio = self._criterio
        if criterio.filtro_campo:
            idx = self._cmb_campo.findData(criterio.filtro_campo)
            if idx >= 0:
                self._cmb_campo.setCurrentIndex(idx)

        if criterio.filtro_tipo == "continuo":
            self._rad_continua.setChecked(True)
            if criterio.filtro_continuo_min is not None:
                self._spin_min.setValue(criterio.filtro_continuo_min)
            if criterio.filtro_continuo_max is not None:
                self._spin_max.setValue(criterio.filtro_continuo_max)
        elif criterio.filtro_tipo == "categorico":
            self._rad_categorica.setChecked(True)
        else:
            self._rad_sin_filtro.setChecked(True)

        self._actualizar_visibilidad()

    def _on_campo_cambiado(self):
        """Reconstruye el panel categórico cuando cambia el campo seleccionado."""
        if self._rad_categorica.isChecked():
            self._rebuild_panel_categorico()

    def _rebuild_panel_categorico(self):
        """Reconstruye la lista de valores únicos con botones Prohibitivo/Permisivo."""
        # Vaciar layout anterior
        while self._layout_cat.count():
            item = self._layout_cat.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._radio_grupos.clear()
        self._spin_puntaje.clear()

        nombre_campo = self._cmb_campo.currentData()
        if nombre_campo is None or self._capa is None:
            return

        # Recoger valores únicos (máx. 200)
        valores = set()
        for f in self._capa.getFeatures():
            val = f[nombre_campo]
            if val is not None and val != "":
                valores.add(str(val))
            if len(valores) >= 200:
                break
        valores = sorted(valores)

        # Estilos compartidos con el asistente
        from .wizard import (
            COLOR_FILA_ALT,
            COLOR_TEXTO,
            FS_CUERPO,
            _cabecera_tabla,
            _celda,
            _fila_tabla,
        )
        from qgis.PyQt.QtCore import Qt as _Qt

        COL_VALOR  = 300
        COL_OPCION = 116

        # Cabecera de columnas — según modo
        if self._modo == "ponderado":
            columnas = [
                ("Valor",          COL_VALOR,  _Qt.AlignLeft),
                ("Puntaje 0–100",  COL_OPCION, _Qt.AlignRight),
            ]
        else:
            columnas = [
                ("Valor",       COL_VALOR,  _Qt.AlignLeft),
                ("Prohibitivo", COL_OPCION, _Qt.AlignHCenter),
                ("Permisivo",   COL_OPCION, _Qt.AlignHCenter),
            ]
        self._layout_cat.addWidget(_cabecera_tabla(columnas))

        def _celda_radio(radio, ancho: int) -> QWidget:
            """Envuelve un radio en un contenedor de ancho fijo y centrado."""
            cont = QWidget()
            cont.setFixedWidth(ancho)
            cont.setStyleSheet("background: transparent;")
            lay = QHBoxLayout(cont)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.addStretch()
            lay.addWidget(radio)
            lay.addStretch()
            return cont

        # Fila por valor
        prev_filtro = self._criterio.filtro_categorico or {}
        prev_puntaje = self._criterio.puntaje_categorico or {}
        for i, val in enumerate(valores):
            widget_fila, fila = _fila_tabla(i)
            fila.addWidget(_celda(
                val, COL_VALOR, color=COLOR_TEXTO, tamano=FS_CUERPO, tooltip=val,
            ))

            if self._modo == "ponderado":
                # Solo spinbox de puntaje
                spin = QSpinBox()
                spin.setRange(0, 100)
                spin.setSuffix(" %")
                spin.setValue(int(prev_puntaje.get(val, 50)))
                spin.setAlignment(_Qt.AlignRight | _Qt.AlignVCenter)
                spin.setFixedWidth(COL_OPCION)
                self._spin_puntaje[val] = spin
                fila.addWidget(spin)
            else:
                # Solo radio Prohibitivo / Permisivo
                rad_proh = QRadioButton()
                rad_perm = QRadioButton()
                for r in (rad_proh, rad_perm):
                    r.setStyleSheet("background: transparent;")
                grupo = QButtonGroup(self)
                grupo.addButton(rad_proh, 0)   # 0 = prohibitivo
                grupo.addButton(rad_perm, 1)   # 1 = permisivo
                self._radio_grupos[val] = grupo

                etiqueta_previa = prev_filtro.get(val, "prohibitivo")
                if etiqueta_previa == "permisivo":
                    rad_perm.setChecked(True)
                else:
                    rad_proh.setChecked(True)

                fila.addWidget(_celda_radio(rad_proh, COL_OPCION))
                fila.addWidget(_celda_radio(rad_perm, COL_OPCION))

            fila.addStretch(1)
            self._layout_cat.addWidget(widget_fila)

        self._layout_cat.addStretch()

    def _actualizar_visibilidad(self):
        """Muestra u oculta los paneles según el tipo de variable elegido."""
        continuo = self._rad_continua.isChecked()
        categorico = self._rad_categorica.isChecked()

        self._widget_continuo.setVisible(continuo)
        self._grp_categorico.setVisible(categorico)

        if categorico:
            self._rebuild_panel_categorico()

    # ------------------------------------------------------------------
    # Guardado
    # ------------------------------------------------------------------

    def _aceptar(self):
        criterio = self._criterio
        campo = self._cmb_campo.currentData()

        if self._rad_sin_filtro.isChecked() or campo is None:
            criterio.filtro_tipo = None
            criterio.filtro_campo = None
            criterio.filtro_continuo_min = None
            criterio.filtro_continuo_max = None
            criterio.filtro_categorico = None

        elif self._rad_continua.isChecked():
            criterio.filtro_tipo = "continuo"
            criterio.filtro_campo = campo
            criterio.filtro_continuo_min = self._spin_min.value()
            criterio.filtro_continuo_max = self._spin_max.value()
            criterio.filtro_categorico = None

        elif self._rad_categorica.isChecked():
            criterio.filtro_tipo = "categorico"
            criterio.filtro_campo = campo
            criterio.filtro_continuo_min = None
            criterio.filtro_continuo_max = None
            if self._modo == "ponderado":
                # Solo puntajes; no hay mapa prohibitivo/permisivo
                puntajes = {val: spin.value() for val, spin in self._spin_puntaje.items()}
                criterio.puntaje_categorico = puntajes if puntajes else None
                criterio.filtro_categorico = None
            else:
                # Solo mapa prohibitivo/permisivo
                mapa = {}
                for val, grupo in self._radio_grupos.items():
                    mapa[val] = "prohibitivo" if grupo.checkedId() == 0 else "permisivo"
                criterio.filtro_categorico = mapa
                criterio.puntaje_categorico = None

        self.accept()
