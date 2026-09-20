# -*- coding: utf-8 -*-
# EvaluaciónRSU — Plugin QGIS para la selección de sitios de disposición final de RSU
# Copyright (C) 2024-2025  Sergio López Olvera <lopezolverasergio@gmail.com>
# SPDX-License-Identifier: GPL-3.0-or-later
# Repository: https://github.com/sergio-lopez-olvera/evaluacion-rsu

"""
Utilidades para aplicar estilos simbología a las capas de resultados.

Aplica automáticamente:
  - Rojo semitransparente → zonas excluidas
  - Verde semitransparente → zonas aptas
  - Escala de color pseudocolor → ráster de aptitud ponderada
"""

from qgis.core import (
    QgsColorRampShader,
    QgsFillSymbol,
    QgsRasterBandStats,
    QgsRasterLayer,
    QgsRasterShader,
    QgsSingleBandPseudoColorRenderer,
    QgsStyle,
    QgsVectorLayer,
)
from qgis.PyQt.QtGui import QColor


def aplicar_estilo_excluidas(capa: QgsVectorLayer) -> None:
    """Aplica relleno rojo semitransparente a la capa de zonas excluidas."""
    simbolo = QgsFillSymbol.createSimple({
        "color": "210,0,0,160",        # rojo, 63 % opacidad
        "outline_color": "180,0,0",
        "outline_width": "0.5",
    })
    capa.renderer().setSymbol(simbolo)
    capa.triggerRepaint()


def aplicar_estilo_aptas(capa: QgsVectorLayer) -> None:
    """Aplica relleno verde semitransparente a la capa de zonas aptas."""
    simbolo = QgsFillSymbol.createSimple({
        "color": "46,125,50,140",      # verde, 55 % opacidad
        "outline_color": "27,94,32",
        "outline_width": "0.7",
    })
    capa.renderer().setSymbol(simbolo)
    capa.triggerRepaint()


def aplicar_estilo_criterio(capa: QgsVectorLayer) -> None:
    """Aplica relleno naranja semitransparente a las capas individuales de criterios."""
    simbolo = QgsFillSymbol.createSimple({
        "color": "230,81,0,120",       # naranja oscuro, ~47 % opacidad
        "outline_color": "191,54,12",
        "outline_width": "0.4",
        "style": "solid",
    })
    capa.renderer().setSymbol(simbolo)
    capa.triggerRepaint()


def aplicar_pseudocolor_aptitud(raster: QgsRasterLayer) -> None:
    """Aplica una rampa de color (amarillo→verde) al ráster de aptitud."""
    proveedor = raster.dataProvider()
    estadisticas = proveedor.bandStatistics(1, QgsRasterBandStats.All)
    # El ráster está normalizado a [0,1]; si las estadísticas incluyen celdas nodata
    # (porque el proveedor aún no las reconoce), los valores serán absurdos.
    # Se clampa como red de seguridad.
    min_val = max(0.0, estadisticas.minimumValue)
    max_val = min(1.0, estadisticas.maximumValue)
    if max_val <= min_val:
        min_val, max_val = 0.0, 1.0

    items = [
        QgsColorRampShader.ColorRampItem(min_val, QColor("#F9A825"), "Baja aptitud"),
        QgsColorRampShader.ColorRampItem((min_val + max_val) / 2, QColor("#FDD835"), "Media"),
        QgsColorRampShader.ColorRampItem(max_val, QColor("#1B5E20"), "Alta aptitud"),
    ]
    shader_fn = QgsColorRampShader(min_val, max_val)
    shader_fn.setColorRampType(QgsColorRampShader.Interpolated)
    shader_fn.setColorRampItemList(items)

    shader = QgsRasterShader()
    shader.setRasterShaderFunction(shader_fn)

    renderer = QgsSingleBandPseudoColorRenderer(proveedor, 1, shader)
    raster.setRenderer(renderer)
    raster.triggerRepaint()
