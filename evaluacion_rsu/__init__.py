# -*- coding: utf-8 -*-
# EvaluaciónRSU — Plugin QGIS para la selección de sitios de disposición final de RSU
# Copyright (C) 2024-2025  Sergio López Olvera <lopezolverasergio@gmail.com>
# SPDX-License-Identifier: GPL-3.0-or-later
# Repository: https://github.com/sergio-lopez-olvera/evaluacion-rsu

"""
Evaluación RSU — QGIS Plugin
Evaluación multicriterio para selección de sitios de disposición final de RSU en México
"""


def classFactory(iface):
    """Punto de entrada requerido por QGIS.

    Args:
        iface: QgsInterface — referencia a la interfaz principal de QGIS.

    Returns:
        Instancia del plugin.
    """
    from .plugin import EvaluacionRSUPlugin
    return EvaluacionRSUPlugin(iface)
