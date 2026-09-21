# -*- coding: utf-8 -*-
"""
Diálogo para definir un criterio propio, fuera de los que trae el plugin.

La NOM-083 fija un mínimo, no un máximo: un estudio real suele necesitar
restricciones que la norma no lista —un derecho de vía, un polígono ejidal, un
área de amortiguamiento acordada con la comunidad— y que el plugin no puede
anticipar. Este diálogo permite añadirlas sin tocar el código.

El usuario elige cómo se evalúa el criterio, que es la decisión que determina
el resultado:

  Traslape        la geometría de la capa es prohibitiva tal cual.
  Buffer          prohíbe lo que quede a MENOS de una distancia de la capa.
  Distancia máx.  prohíbe lo que quede a MÁS de una distancia de la capa.

Las dos últimas son opuestas y confundirlas produce el mapa complementario del
correcto, así que el diálogo las describe con una frase completa en lugar de
con un nombre técnico.
"""

from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path

from qgis.core import QgsMapLayerProxyModel, QgsProject, QgsVectorLayer
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from ..core.criterios import Criterio, FuenteDatos, TipoExclusion


def _id_desde_nombre(nombre: str, usados) -> str:
    """Identificador estable a partir del nombre visible.

    El id viaja a nombres de archivo (``excl_<id>.gpkg``), así que se quitan
    acentos y se deja solo lo que cualquier sistema de archivos acepta.
    """
    base = unicodedata.normalize("NFKD", nombre)
    base = base.encode("ascii", "ignore").decode("ascii").lower()
    base = re.sub(r"[^a-z0-9]+", "_", base).strip("_") or "criterio"
    base = f"usr_{base}"[:40]
    candidato, n = base, 2
    while candidato in usados:
        candidato = f"{base}_{n}"
        n += 1
    return candidato


class DialogoCriterioPersonalizado(QDialog):
    """Captura los datos de un criterio definido por el usuario."""

    def __init__(self, ids_existentes, parent=None):
        super().__init__(parent)
        self._ids = set(ids_existentes)
        self._ruta_archivo = None
        self.criterio = None
        self.setWindowTitle("Nuevo criterio")
        self.setMinimumWidth(560)
        self._build_ui()

    # ------------------------------------------------------------------
    def _build_ui(self):
        v = QVBoxLayout(self)

        intro = QLabel(
            "Defina una restricción que la NOM-083 no contempla pero que su "
            "estudio necesita. El criterio se suma a los demás y aparece en el "
            "reporte con su nombre y su regla."
        )
        intro.setWordWrap(True)
        v.addWidget(intro)

        form = QFormLayout()
        form.setSpacing(8)

        self.txt_nombre = QLineEdit()
        self.txt_nombre.setPlaceholderText("p. ej. Derecho de vía del gasoducto")
        form.addRow("Nombre:", self.txt_nombre)

        self.txt_desc = QLineEdit()
        self.txt_desc.setPlaceholderText("Opcional: de dónde viene el dato, qué representa")
        form.addRow("Descripción:", self.txt_desc)

        # ── Capa: del proyecto o de un archivo ───────────────────────────
        capa_w = QWidget()
        capa_h = QHBoxLayout(capa_w)
        capa_h.setContentsMargins(0, 0, 0, 0)
        self.combo_capa = QComboBox()
        self.combo_capa.addItem("— Seleccione una capa del proyecto —", None)
        for capa in QgsProject.instance().mapLayers().values():
            if isinstance(capa, QgsVectorLayer) and capa.isValid():
                self.combo_capa.addItem(capa.name(), capa.id())
        btn_archivo = QPushButton("Desde archivo…")
        btn_archivo.clicked.connect(self._elegir_archivo)
        capa_h.addWidget(self.combo_capa, 1)
        capa_h.addWidget(btn_archivo)
        form.addRow("Capa:", capa_w)

        self.etq_archivo = QLabel("")
        self.etq_archivo.setWordWrap(True)
        form.addRow("", self.etq_archivo)

        v.addLayout(form)

        # ── Cómo se evalúa ───────────────────────────────────────────────
        v.addWidget(QLabel("<b>¿Cómo se evalúa este criterio?</b>"))

        self.grupo_tipo = QButtonGroup(self)
        self.rad_traslape = QRadioButton(
            "Prohíbe donde la capa se traslapa con el terreno")
        self.rad_buffer = QRadioButton(
            "Prohíbe lo que quede a MENOS de una distancia de la capa")
        self.rad_lejania = QRadioButton(
            "Prohíbe lo que quede a MÁS de una distancia de la capa")
        for i, rad in enumerate((self.rad_traslape, self.rad_buffer,
                                 self.rad_lejania)):
            self.grupo_tipo.addButton(rad, i)
            v.addWidget(rad)
        self.rad_traslape.setChecked(True)

        ayuda = QLabel(
            "La segunda opción es la habitual: alejarse de algo que estorba. "
            "La tercera es la inversa —acercarse a algo que hace falta, como "
            "una vía de acceso— y solo tiene sentido para criterios de "
            "conectividad o servicio."
        )
        ayuda.setWordWrap(True)
        ayuda.setStyleSheet("color: #78868F; font-size: 11px;")
        v.addWidget(ayuda)

        dist_form = QFormLayout()
        self.spin_dist = QDoubleSpinBox()
        self.spin_dist.setRange(0.0, 200_000.0)
        self.spin_dist.setDecimals(0)
        self.spin_dist.setSingleStep(100.0)
        self.spin_dist.setSuffix(" m")
        self.spin_dist.setValue(500.0)
        self.spin_dist.setEnabled(False)
        dist_form.addRow("Distancia:", self.spin_dist)
        v.addLayout(dist_form)

        self.grupo_tipo.idClicked.connect(
            lambda i: self.spin_dist.setEnabled(i in (1, 2)))

        botones = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        botones.accepted.connect(self._aceptar)
        botones.rejected.connect(self.reject)
        v.addWidget(botones)

    # ------------------------------------------------------------------
    def _elegir_archivo(self):
        ruta, _ = QFileDialog.getOpenFileName(
            self, "Capa del criterio", os.path.expanduser("~"),
            "Vectoriales (*.shp *.gpkg *.geojson *.kml)")
        if not ruta:
            return
        capa = QgsVectorLayer(ruta, Path(ruta).stem, "ogr")
        if not capa.isValid():
            QMessageBox.warning(self, "Archivo inválido",
                                f"No se pudo abrir como capa vectorial:\n{ruta}")
            return
        self._ruta_archivo = ruta
        self.combo_capa.setCurrentIndex(0)
        self.etq_archivo.setText(f"Archivo: {Path(ruta).name}")
        if not self.txt_nombre.text().strip():
            self.txt_nombre.setText(Path(ruta).stem.replace("_", " ").capitalize())

    # ------------------------------------------------------------------
    def _aceptar(self):
        nombre = self.txt_nombre.text().strip()
        if not nombre:
            QMessageBox.warning(self, "Falta el nombre",
                                "El criterio necesita un nombre para poder "
                                "identificarlo en el reporte.")
            return

        id_capa = self.combo_capa.currentData()
        if id_capa is None and not self._ruta_archivo:
            QMessageBox.warning(
                self, "Falta la capa",
                "Elija una capa del proyecto o cargue un archivo: sin datos el "
                "criterio no puede evaluarse.")
            return

        tipo_id = self.grupo_tipo.checkedId()
        tipo = (TipoExclusion.TRASLAPE if tipo_id == 0
                else TipoExclusion.BUFFER if tipo_id == 1
                else TipoExclusion.LEJANIA)

        distancia = self.spin_dist.value() if tipo_id in (1, 2) else 0.0
        if tipo_id in (1, 2) and distancia <= 0:
            QMessageBox.warning(
                self, "Distancia inválida",
                "Una distancia de cero deja el criterio sin efecto. Indique un "
                "valor mayor que cero o elija evaluación por traslape.")
            return

        if tipo == TipoExclusion.TRASLAPE:
            regla = "traslape directo"
        elif tipo == TipoExclusion.BUFFER:
            regla = f"prohíbe a menos de {distancia:,.0f} m"
        else:
            regla = f"prohíbe a más de {distancia:,.0f} m"

        descripcion = self.txt_desc.text().strip() or (
            f"Criterio definido por el usuario. Regla: {regla}.")

        criterio = Criterio(
            id=_id_desde_nombre(nombre, self._ids),
            nombre=nombre,
            descripcion=descripcion,
            referencia_normativa="Criterio del usuario",
            fuente=FuenteDatos.MANUAL,
            es_exclusion=True,
            tipo_exclusion=tipo,
            buffer_m=distancia,
            buffer_min_nom=0.0,
            umbral_exclusion=distancia if tipo == TipoExclusion.LEJANIA else None,
            unidad_umbral="m",
            activo=True,
            obligatorio=False,
            es_personalizado=True,
            rol_ponderado="excluyente",
        )

        # La ruta se guarda, no la capa: la lista de criterios se copia en
        # profundidad al abrir el asistente, y una QgsVectorLayer dentro de esa
        # copia no sobrevive el proceso. El Paso 3 la carga desde aquí.
        if self._ruta_archivo:
            criterio.ruta_dato = self._ruta_archivo
        else:
            capa = QgsProject.instance().mapLayer(id_capa)
            fuente = capa.source().split("|")[0] if capa else None
            if fuente and os.path.exists(fuente):
                criterio.ruta_dato = fuente
            else:
                QMessageBox.warning(
                    self, "Capa sin archivo",
                    "Esa capa no tiene un archivo en disco —puede ser temporal "
                    "o estar en memoria—. Guárdela como GeoPackage y cárguela "
                    "con «Desde archivo…».")
                return

        self.criterio = criterio
        self.accept()
