# -*- coding: utf-8 -*-
# EvaluaciónRSU — Plugin QGIS para la selección de sitios de disposición final de RSU
# Copyright (C) 2024-2025  Sergio López Olvera <lopezolverasergio@gmail.com>
# SPDX-License-Identifier: GPL-3.0-or-later
# Repository: https://github.com/sergio-lopez-olvera/evaluacion-rsu

"""
Motor de análisis multicriterio para selección de sitios RSU.

Análisis de exclusión (resultado dicotómico)
============================================
Para cada criterio de exclusión:
  * Si tipo_exclusion == BUFFER  → genera un área de amortiguamiento alrededor
    de la capa y la une al polígono de exclusión acumulado.
  * Si tipo_exclusion == TRASLAPE → el polígono/área se usa directamente.
  * Caso especial "pendiente": se rasteriza el MDT, se deriva la pendiente con
    GDAL y se vectoriza la máscara de exclusión (pendiente > umbral).

El resultado es:
  - ``exclusion_union`` : capa vectorial con todas las zonas excluidas (unión).
  - ``apto``            : diferencia entre el área de interés y ``exclusion_union``.

Análisis de ponderación (suma ponderada)
=========================================
Se ejecuta sólo sobre las zonas identificadas como APTAS por la exclusión.
Para cada criterio de ponderación:
  1. Se rasteriza la capa (o se calcula la distancia euclidiana, según el caso).
  2. Se normaliza el ráster a [0, 1].
  3. Se multiplica por el peso del criterio.
El resultado es un ráster de aptitud relativa dentro del área apta.

Parámetros de resolución del ráster: 100 m (configurable).
"""

from __future__ import annotations

import math
import os
import tempfile
from pathlib import Path
from typing import Optional

import numpy as np
from osgeo import gdal, ogr, osr

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsDistanceArea,
    QgsFeature,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsProcessingContext,
    QgsProcessingFeedback,
    QgsProject,
    QgsRasterLayer,
    QgsVectorFileWriter,
    QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QObject, QVariant, pyqtSignal

import processing  # algoritmos nativos de QGIS (native:*)

from .criterios import (
    Criterio,
    TipoExclusion,
    TipoScore,
    USV_CLAVES_EXCLUIDAS,
    PUNTAJE_EDAFOLOGIA,
)


# ---------------------------------------------------------------------------
# Identificación del build
# ---------------------------------------------------------------------------

def escala_pendiente(crs) -> float:
    """Factor SCALE para gdal:slope según el CRS del modelo de elevación.

    gdal:slope divide el desnivel entre la distancia horizontal expresada en
    las unidades del ráster. Si el MDE está en coordenadas geográficas, esas
    unidades son GRADOS mientras la elevación está en metros, y una celda de
    0.005° se toma como 0.005 m: la pendiente sale exagerada unas 111 000
    veces. Un terreno de 10 % se reporta como 1 200 000 %, supera cualquier
    umbral razonable y el área entera queda descartada por «demasiado
    empinada», sin que nada falle de forma visible.

    SCALE es el número de unidades horizontales por unidad vertical: 111 120
    para grados con elevación en metros (valor que documenta GDAL), 1 cuando
    el ráster ya está en metros.
    """
    try:
        if crs is not None and crs.isValid() and crs.isGeographic():
            return 111_120.0
    except Exception:  # noqa: BLE001
        pass
    return 1.0


def _leer_version() -> str:
    """Lee version= de metadata.txt para identificar el build en el registro.

    Se imprime al inicio de cada análisis: sin esto es imposible distinguir un
    resultado producido por una versión ya corregida de uno producido por una
    copia anterior que quedó instalada en QGIS.
    """
    try:
        ruta = Path(__file__).resolve().parent.parent / "metadata.txt"
        for linea in ruta.read_text(encoding="utf-8").splitlines():
            if linea.strip().startswith("version="):
                return linea.split("=", 1)[1].strip()
    except Exception:  # noqa: BLE001
        pass
    return "desconocida"


VERSION_PLUGIN = _leer_version()


# ---------------------------------------------------------------------------
# Señal de retroalimentación
# ---------------------------------------------------------------------------

class FeedbackBridge(QgsProcessingFeedback):
    """Redirige los mensajes de QgsProcessingFeedback a una señal Qt."""

    def __init__(self, signal):
        super().__init__()
        self._signal = signal

    def setProgress(self, progress):
        self._signal.emit(int(progress), "")

    def pushInfo(self, info):
        self._signal.emit(-1, info)

    def reportError(self, error, fatalError=False):
        self._signal.emit(-1, f"[Error] {error}")


# ---------------------------------------------------------------------------
# Motor principal
# ---------------------------------------------------------------------------

class MotorAnalisis(QObject):
    """Ejecuta el análisis multicriterio: exclusión y, opcionalmente, ponderación.

    Señales
    -------
    progreso(int, str)
        Porcentaje (-1 = sin cambio) y mensaje de estado.
    fase1_lista(QgsVectorLayer, QgsVectorLayer, dict)
        Emitida al terminar la exclusión: (capa_excluida, capa_apta, capas_criterios).
    fase2_lista(QgsRasterLayer)
        Emitida al terminar la ponderación: ráster de aptitud.
    error(str)
        Mensaje de error crítico.

    Atributos poblados durante la ejecución
    ---------------------------------------
    detalle_criterios : list[dict]
        Un registro por criterio de exclusión evaluado, con su parámetro,
        el origen del dato y el área que aportó.
    resumen_areas : dict
        Balance de superficies del área de interés (km² y porcentajes).
    """

    progreso = pyqtSignal(int, str)
    fase1_lista = pyqtSignal(object, object, object)  # (excluida, apta, dict capas criterios)
    fase2_lista = pyqtSignal(object)                  # QgsRasterLayer
    error = pyqtSignal(str)

    RESOLUCION_M = 100      # resolución del ráster de ponderación (metros)
    CRS_MEXICO = QgsCoordinateReferenceSystem("EPSG:6372")  # ITRF2008 / LCC

    def __init__(
        self,
        aoi_layer: QgsVectorLayer,
        criterios: list[Criterio],
        directorio_salida: str,
        modo_analisis: str = "dicotomico",
        parent=None,
    ):
        super().__init__(parent)
        self.aoi = aoi_layer
        self.criterios = criterios
        self.dir_salida = Path(directorio_salida) / "resultados_rsu"
        self.dir_salida.mkdir(parents=True, exist_ok=True)
        self._feedback = FeedbackBridge(self.progreso)
        self._ctx = QgsProcessingContext()
        # "dicotomico" → análisis 100 % binario (excluido / apto).
        # "ponderado"  → excluyentes como máscara + ponderados con puntaje.
        self.modo_analisis = modo_analisis
        # Poblados durante ejecutar_fase1(); consumidos por el resumen de la GUI
        self.detalle_criterios: list[dict] = []
        self.resumen_areas: dict = {}

    # ------------------------------------------------------------------
    # Exclusión — zonas prohibidas y zonas aptas
    # ------------------------------------------------------------------

    def ejecutar_fase1(self) -> tuple[Optional[QgsVectorLayer], Optional[QgsVectorLayer]]:
        """Genera las capas de zonas excluidas y zonas aptas.

        Returns
        -------
        (capa_excluida, capa_apta) — ambas en el CRS del proyecto.
        """
        self.progreso.emit(0, "═══ ANÁLISIS DE EXCLUSIÓN ═══")
        self.progreso.emit(-1, f"EvaluaciónRSU v{VERSION_PLUGIN}")
        etiqueta_modo = ("Ponderado (aptitud relativa)"
                         if self.modo_analisis == "ponderado"
                         else "Dicotómico (prohibido / permitido)")
        self.progreso.emit(-1, f"Modo de análisis: {etiqueta_modo}")
        self.progreso.emit(-1, f"Resultados en: {self.dir_salida}")

        if self.modo_analisis == "ponderado":
            # En modo ponderado solo aplican como excluyentes los criterios
            # cuyo rol_ponderado == "excluyente". Los demás aportan puntaje.
            criterios_excl = [
                c for c in self.criterios
                if c.es_exclusion and c.activo and c.capa is not None
                and c.rol_ponderado == "excluyente"
            ]
        else:
            # Modo dicotómico: todos los criterios de exclusión activos
            criterios_excl = [
                c for c in self.criterios
                if c.es_exclusion and c.activo and c.capa is not None
            ]

        if not criterios_excl:
            self.progreso.emit(
                100,
                "Ningún criterio de exclusión tiene datos cargados: "
                "el área de interés completa se reporta como apta.")
            aoi_copia = self._copiar_como_vectorial(self.aoi, "area_apta")
            self.fase1_lista.emit(None, aoi_copia, {})
            return None, aoi_copia

        # Acumular geometrías de exclusión
        zonas_exclusion: list[QgsGeometry] = []
        # Dict {nombre_criterio: QgsVectorLayer} para exponer al usuario
        capas_criterios: dict[str, "QgsVectorLayer"] = {}
        total = len(criterios_excl)

        self.progreso.emit(
            -1, f"Criterios de exclusión con datos cargados: {total}")

        # Trazabilidad por criterio, para el resumen final
        self.detalle_criterios: list[dict] = []

        for i, criterio in enumerate(criterios_excl):
            pct = int(i / total * 80)
            es_buffer = criterio.tipo_exclusion == TipoExclusion.BUFFER
            param = (f"buffer {criterio.buffer_m:.0f} m" if es_buffer
                     else "traslape directo")
            origen = {"automatica": "descarga automática",
                      "manual": "archivo del usuario"}.get(
                          getattr(criterio, "origen_carga", ""), "origen no registrado")
            if criterio.filtro_tipo and criterio.filtro_campo:
                mapeo = f"mapeo {criterio.filtro_tipo} sobre «{criterio.filtro_campo}»"
            else:
                mapeo = "sin mapeo: toda la geometría es prohibitiva"

            self.progreso.emit(pct, f"[{i + 1}/{total}] {criterio.nombre}")
            self.progreso.emit(-1, f"        Norma: {criterio.referencia_normativa or '—'}")
            self.progreso.emit(-1, f"        Dato:  {origen}")
            self.progreso.emit(-1, f"        Regla: {param}; {mapeo}")

            registro = {
                "nombre": criterio.nombre,
                "id": criterio.id,
                "parametro": param,
                "origen": origen,
                "area_km2": 0.0,
                "resultado": "sin geometría en el área de interés",
            }

            try:
                geom_excl = self._geometria_exclusion(criterio)
                if geom_excl and not geom_excl.isEmpty():
                    # Intersectar con el área de interés para limitar al área de estudio
                    geom_excl = geom_excl.intersection(self._geom_aoi())
                    if geom_excl and not geom_excl.isEmpty():
                        zonas_exclusion.append(geom_excl)
                        area = self._area_km2(geom_excl)
                        registro["area_km2"] = area
                        if area < 0.005:   # por debajo de la resolución reportada
                            registro["resultado"] = "traslape despreciable (< 0.01 km²)"
                            self.progreso.emit(
                                -1,
                                "        → traslapa con el área de interés, pero "
                                "la superficie es despreciable (< 0.01 km²)")
                        else:
                            registro["resultado"] = "aporta exclusión"
                            self.progreso.emit(
                                -1,
                                f"        → excluye {area:,.2f} km² dentro del "
                                f"área de interés")
                        # Guardar capa individual del criterio
                        ruta_crit = str(self.dir_salida / f"excl_{criterio.id}.gpkg")
                        capa_crit = self._geom_a_capa(geom_excl, criterio.id, ruta_crit)
                        if capa_crit and capa_crit.isValid():
                            capas_criterios[criterio.nombre] = capa_crit
                    else:
                        self.progreso.emit(
                            -1, "        → sin traslape con el área de interés")
                else:
                    self.progreso.emit(
                        -1, "        → la capa no aportó geometría prohibitiva")
            except Exception as exc:  # noqa: BLE001
                registro["resultado"] = f"error: {exc}"
                self.progreso.emit(-1, f"        → ERROR: {exc}")

            self.detalle_criterios.append(registro)

        # Unir todas las zonas de exclusión con unaryUnion (O(n log n))
        self.progreso.emit(82, "Uniendo las zonas de exclusión de todos los criterios…")
        if zonas_exclusion:
            geom_excluida = self._union_segura(
                zonas_exclusion, "unión de criterios")
        else:
            geom_excluida = QgsGeometry()

        # Guardar zonas de exclusión como capa temporal para usar en processing
        self.progreso.emit(88, "Guardando la capa de zonas excluidas…")
        capa_excluida = self._geom_a_capa(
            geom_excluida, "zona_excluida",
            str(self.dir_salida / "zona_excluida.gpkg")
        ) if not geom_excluida.isEmpty() else None

        # Zona apta = área de interés − zonas de exclusión
        # Usar processing.run("native:difference") que usa el backend C++ con índice espacial
        self.progreso.emit(90, "Restando las zonas excluidas del área de interés…")
        capa_aoi = self._copiar_como_vectorial(self.aoi, "_aoi_tmp")
        ruta_apta = str(self.dir_salida / "zona_apta.gpkg")

        if capa_excluida is not None:
            try:
                processing.run(
                    "native:difference",
                    {
                        "INPUT": capa_aoi,
                        "OVERLAY": capa_excluida,
                        "OUTPUT": ruta_apta,
                    },
                )
                capa_apta = QgsVectorLayer(ruta_apta, "zona_apta", "ogr")
            except Exception as exc:  # noqa: BLE001  fallback a método geométrico puro
                self.progreso.emit(-1, f"  ⚠ processing.difference falló ({exc}), usando método geométrico…")
                geom_aoi = self._geom_aoi()
                geom_apta = geom_aoi.difference(geom_excluida)
                capa_apta = self._geom_a_capa(geom_apta, "zona_apta", ruta_apta)
        else:
            # Sin exclusión: el área apta es el AOI completo
            capa_apta = self._copiar_como_vectorial(self.aoi, "zona_apta")

        # ── Balance de áreas ────────────────────────────────────────────
        area_aoi  = self._area_km2(self._geom_aoi())
        area_excl = self._area_km2(geom_excluida) if not geom_excluida.isEmpty() else 0.0
        area_apta = max(area_aoi - area_excl, 0.0)
        # El recorte a [0, 100] evita artefactos como "-0.0 %" cuando la unión
        # de exclusiones cubre el área completa y el redondeo la excede por poco.
        pct_excl  = (area_excl / area_aoi * 100.0) if area_aoi > 0 else 0.0
        pct_excl  = min(max(pct_excl, 0.0), 100.0)

        self.resumen_areas = {
            "aoi_km2": area_aoi,
            "excluida_km2": area_excl,
            "apta_km2": area_apta,
            "pct_excluida": pct_excl,
            "pct_apta": 100.0 - pct_excl,
            "modo": self.modo_analisis,
            "n_criterios": total,
        }

        self.progreso.emit(-1, "")
        self.progreso.emit(-1, "Balance de áreas:")
        self.progreso.emit(-1, f"        Área de interés: {area_aoi:,.2f} km²")
        self.progreso.emit(
            -1, f"        Área prohibida:  {area_excl:,.2f} km²  ({pct_excl:,.1f} %)")
        self.progreso.emit(
            -1, f"        Área permitida:  {area_apta:,.2f} km²  ({100.0 - pct_excl:,.1f} %)")
        if area_apta <= 0.0:
            self.progreso.emit(
                -1,
                "        ⚠ No queda superficie apta: los criterios activos "
                "cubren el área de interés por completo. Amplíe el área, "
                "revise los buffers o desactive criterios no aplicables.",
            )
        self.progreso.emit(100, "Análisis de exclusión terminado.")
        self.fase1_lista.emit(capa_excluida, capa_apta, capas_criterios)
        return capa_excluida, capa_apta

    def _geometria_exclusion(self, criterio: Criterio) -> QgsGeometry:
        """Retorna la geometría de exclusión para un criterio dado."""
        capa = criterio.capa

        if criterio.tipo_exclusion == TipoExclusion.TRASLAPE:
            # Criterio especial: USV — filtrar clases excluidas
            if criterio.id == "uso_suelo_vegetacion":
                return self._excluir_usv(capa)
            # Criterio especial: pendiente — generar desde ráster
            if criterio.id == "pendiente" and isinstance(capa, QgsRasterLayer):
                return self._excluir_pendiente(
                    capa, umbral=self._umbral(criterio, 25.0))
            # Caso general: unión de geometrías (con filtro de atributos si aplica)
            return self._union_geometrias(capa, criterio)

        elif criterio.tipo_exclusion == TipoExclusion.UMBRAL_RASTER:
            if isinstance(capa, QgsRasterLayer):
                return self._excluir_pendiente(
                    capa, umbral=self._umbral(criterio, 25.0))
            # La capa ya viene vectorizada (p. ej. el descargador entregó los
            # polígonos que superan el umbral): se usa tal cual.
            return self._union_geometrias(capa, criterio)

        elif criterio.tipo_exclusion == TipoExclusion.LEJANIA:
            return self._excluir_por_lejania(criterio)

        elif criterio.tipo_exclusion == TipoExclusion.BUFFER:
            buffer_m = criterio.buffer_m
            geom_union = self._union_geometrias(capa, criterio)
            if geom_union.isEmpty():
                return geom_union
            # _union_geometrias ya devuelve en el CRS del AOI
            return self._buffer_metrico(geom_union, buffer_m, self.aoi.crs())

        return QgsGeometry()

    # ------------------------------------------------------------------
    # Filtro de atributos para capas manuales
    # ------------------------------------------------------------------

    def _es_prohibitivo(self, feat, criterio: Criterio) -> bool:
        """Evalúa si una feature es prohibitiva según el mapeo del criterio.

        Si no hay filtro configurado, todas las geometrías se consideran
        prohibitivas (comportamiento por defecto).
        """
        if not criterio.filtro_tipo or not criterio.filtro_campo:
            return True  # sin filtro → usar toda la geometría

        try:
            valor = feat[criterio.filtro_campo]
        except (KeyError, Exception):
            return True  # si el campo no existe, incluir por precaución

        if criterio.filtro_tipo == "continuo":
            if valor is None:
                return False
            try:
                v = float(valor)
            except (TypeError, ValueError):
                return False
            vmin = criterio.filtro_continuo_min
            vmax = criterio.filtro_continuo_max
            if vmin is None or vmax is None:
                return True
            # Rango [min, max] inclusive
            lo, hi = (vmin, vmax) if vmin <= vmax else (vmax, vmin)
            return lo <= v <= hi

        elif criterio.filtro_tipo == "categorico":
            mapa = criterio.filtro_categorico or {}
            etiqueta = mapa.get(str(valor), "prohibitivo")
            return etiqueta == "prohibitivo"

        return True

    # ------------------------------------------------------------------
    # Reproyección y filtro espacial
    # ------------------------------------------------------------------

    # CRS de respaldo para capas cuyas coordenadas son métricas pero cuyo .prj
    # falta o declara un sistema geográfico. Es el CRS del Marco Geoestadístico
    # del INEGI y de la cartografía temática nacional.
    CRS_RESPALDO_MX = QgsCoordinateReferenceSystem("EPSG:6372")

    @staticmethod
    def _etiqueta_crs(crs) -> str:
        """Nombre legible de un CRS, aunque no tenga código de autoridad.

        Varios shapefiles del INEGI traen un .prj en dialecto ESRI que QGIS
        interpreta correctamente pero no logra asociar a un código EPSG. En ese
        caso authid() devuelve una cadena vacía, y reportar «sin definir» hace
        creer que la capa no tiene CRS cuando en realidad sí lo tiene.
        """
        if crs is None or not crs.isValid():
            return "sin definir"
        codigo = crs.authid()
        if codigo:
            return codigo
        nombre = (crs.description() or "").strip()
        unidad = "proyectado" if not crs.isGeographic() else "geográfico"
        return f"{nombre or 'sin nombre'} ({unidad}, sin código EPSG)"

    @staticmethod
    def _extension_confiable(capa) -> "Optional[QgsRectangle]":
        """Extensión de la capa en sus propias coordenadas, o None.

        No basta con ``capa.extent()``: algunos proveedores la calculan de forma
        diferida y devuelven un rectángulo vacío en la primera consulta. En ese
        caso se deduce muestreando las primeras geometrías, de modo que la
        validación de CRS nunca quede sin resolver en silencio.
        """
        from qgis.core import QgsFeatureRequest, QgsRectangle

        try:
            ext = capa.extent()
            if not ext.isEmpty():
                return ext
        except Exception:  # noqa: BLE001
            pass

        # Respaldo: envolvente de una muestra de geometrías
        try:
            req = QgsFeatureRequest().setLimit(200).setNoAttributes()
            env = QgsRectangle()
            for feat in capa.getFeatures(req):
                g = feat.geometry()
                if g and not g.isEmpty():
                    if env.isEmpty():
                        env = g.boundingBox()
                    else:
                        env.combineExtentWith(g.boundingBox())
            return None if env.isEmpty() else env
        except Exception:  # noqa: BLE001
            return None

    def _crs_efectivo(self, capa) -> QgsCoordinateReferenceSystem:
        """CRS con el que debe leerse la capa, corrigiendo declaraciones inválidas.

        Varios shapefiles de distribución nacional llegan mal etiquetados:

          * ``humedales_febrero_2012`` no incluye ningún ``.prj``.
          * ``localidades_urbanas.prj`` declara
            ``GEOGCS["GCS_WGS_1984" … UNIT["Degree"]]`` mientras sus coordenadas
            están en metros (x ≈ 1.1–4.1 millones, y ≈ 0.3–2.3 millones).

        En ambos casos QGIS asume grados y todo filtro espacial construido desde
        un área de interés geográfica devuelve cero elementos, de modo que la
        capa parece vacía sin que nada falle de forma visible.

        La comprobación es geométrica, no por nombre de archivo: si la extensión
        de la capa excede el dominio geográfico válido (±180°, ±90°) entonces sus
        unidades no pueden ser grados. Cuando además encaja en el dominio de
        EPSG:6372 se adopta ese sistema y se avisa en el registro.
        """
        crs_declarado = capa.crs()

        ext = self._extension_confiable(capa)
        if ext is None:
            self.progreso.emit(
                -1,
                "        ⚠ No se pudo determinar la extensión de la capa; "
                "no es posible validar su CRS.",
            )
            return crs_declarado

        fuera_de_rango = (
            abs(ext.xMinimum()) > 180.0 or abs(ext.xMaximum()) > 180.0
            or abs(ext.yMinimum()) > 90.0 or abs(ext.yMaximum()) > 90.0
        )

        # Solo intervenir si la capa dice ser geográfica (o no dice nada) pero
        # sus coordenadas no pueden serlo.
        declara_grados = (not crs_declarado.isValid()) or crs_declarado.isGeographic()
        if not (fuera_de_rango and declara_grados):
            return crs_declarado

        etiqueta = self._etiqueta_crs(crs_declarado)
        self.progreso.emit(
            -1,
            f"        ⚠ CRS mal declarado: la capa indica «{etiqueta}» "
            f"(grados) pero su extensión es x[{ext.xMinimum():,.0f}…"
            f"{ext.xMaximum():,.0f}] y[{ext.yMinimum():,.0f}…"
            f"{ext.yMaximum():,.0f}], que no son grados.",
        )

        # ¿Encaja en el dominio de EPSG:6372 (México continental)?
        if (0 < ext.xMinimum() and ext.xMaximum() < 6_000_000
                and 0 < ext.yMinimum() and ext.yMaximum() < 4_000_000):
            self.progreso.emit(
                -1,
                f"        → Se interpreta como {self.CRS_RESPALDO_MX.authid()} "
                f"(LCC México). Corrija el .prj en el origen para eliminar "
                f"esta suposición.",
            )
            return self.CRS_RESPALDO_MX

        self.progreso.emit(
            -1,
            "        → No se pudo deducir el CRS real; la capa se omitirá. "
            "Cárguela manualmente con su CRS correcto asignado.",
        )
        return crs_declarado

    def _bbox_busqueda(
        self, capa: QgsVectorLayer, criterio: "Criterio | None" = None,
        crs_capa_efectivo: Optional[QgsCoordinateReferenceSystem] = None,
    ) -> "QgsRectangle":
        """Bbox de búsqueda expresado en el CRS DE LA CAPA.

        QgsFeatureRequest.setFilterRect() interpreta el rectángulo en el CRS de
        la capa consultada, no en el del área de interés. Si la capa está en un
        CRS distinto (p. ej. el Marco Geoestadístico del INEGI en EPSG:6372,
        metros, frente a un AOI en EPSG:4326, grados) un rectángulo sin
        transformar no intersecta ninguna geometría y la capa parece vacía.

        El bbox se expande al 200 % del AOI y, para criterios de buffer, al
        menos a dos veces la distancia normativa, de modo que ningún elemento
        cercano al borde quede fuera.
        """
        from qgis.core import QgsRectangle, QgsUnitTypes

        crs_capa = crs_capa_efectivo or self._crs_efectivo(capa)
        crs_aoi = self.aoi.crs()
        bbox_aoi = self._geom_aoi_cacheada().boundingBox()

        # 1. Llevar el bbox al CRS de la capa
        if crs_capa.isValid() and crs_aoi.isValid() and crs_capa != crs_aoi:
            xform = QgsCoordinateTransform(
                crs_aoi, crs_capa, QgsProject.instance().transformContext()
            )
            bbox = xform.transformBoundingBox(bbox_aoi)
        else:
            bbox = QgsRectangle(bbox_aoi)   # copia: no mutar el original

        # 2. Expansión, en las unidades de la capa
        delta = max(bbox.width(), bbox.height()) / 2.0

        dist_norm = 0.0
        if criterio is not None:
            dist_norm = getattr(criterio, "buffer_min_nom", 0.0) or 0.0
            dist_norm = max(dist_norm, getattr(criterio, "buffer_m", 0.0) or 0.0)
        if dist_norm > 0:
            # Convertir metros a las unidades del CRS de la capa
            try:
                factor = QgsUnitTypes.fromUnitToUnitFactor(
                    QgsUnitTypes.DistanceMeters, crs_capa.mapUnits()
                )
            except Exception:  # noqa: BLE001
                factor = 1.0 / 111_320.0 if crs_capa.isGeographic() else 1.0
            delta = max(delta, dist_norm * 2.0 * factor)

        bbox.grow(delta)
        return bbox

    def _union_segura(self, geoms: list, etiqueta: str = "") -> QgsGeometry:
        """Une una lista de geometrías tolerando las que tengan errores de topología.

        ``QgsGeometry.unaryUnion()`` delega en GEOS, que ante una sola geometría
        inválida —autointersecciones, anillos mal orientados— devuelve una
        geometría vacía en lugar de lanzar excepción. El resultado es que un
        único polígono defectuoso anula la aportación de la capa completa, y el
        fallo no deja rastro: la capa simplemente parece no aportar nada.

        Esto es frecuente en polígonos derivados de OpenStreetMap, donde las
        vías cerradas se convierten a anillos sin garantía de validez.

        Estrategia en tres niveles:
          1. Unión directa, que es la vía rápida.
          2. Si falla, se reparan las geometrías inválidas con makeValid() y se
             reintenta.
          3. Si todavía falla, se acumula de forma incremental omitiendo las que
             no se puedan unir, de modo que se obtenga un resultado parcial
             correcto en vez de ninguno.
        """
        if not geoms:
            return QgsGeometry()

        pref = f"        [{etiqueta}] " if etiqueta else "        "

        # Nivel 1 — unión directa. PyQGIS suele devolver una geometría vacía
        # ante un error de topología, pero según la versión de GEOS puede
        # propagar la excepción: se cubren ambos comportamientos.
        try:
            union = QgsGeometry.unaryUnion(geoms)
            if union and not union.isEmpty():
                return union
        except Exception:  # noqa: BLE001
            pass

        # Nivel 2 — reparar y reintentar
        saneadas: list[QgsGeometry] = []
        n_reparadas = n_perdidas = 0
        for g in geoms:
            try:
                valida = g.isGeosValid()
            except Exception:  # noqa: BLE001
                valida = False
            if valida:
                saneadas.append(g)
                continue
            try:
                gv = g.makeValid()
            except Exception:  # noqa: BLE001
                gv = None
            if gv and not gv.isEmpty():
                saneadas.append(gv)
                n_reparadas += 1
            else:
                n_perdidas += 1

        self.progreso.emit(
            -1,
            f"{pref}La unión directa falló por errores de topología; "
            f"{n_reparadas} geometrías reparadas"
            + (f", {n_perdidas} irrecuperables" if n_perdidas else ""),
        )

        if saneadas:
            try:
                union = QgsGeometry.unaryUnion(saneadas)
                if union and not union.isEmpty():
                    return union
            except Exception:  # noqa: BLE001
                pass

        # Nivel 3 — acumulación incremental, omitiendo lo que no se pueda unir
        acumulada = QgsGeometry()
        n_omitidas = 0
        for g in saneadas:
            try:
                nueva = g if acumulada.isEmpty() else acumulada.combine(g)
                if nueva and not nueva.isEmpty():
                    acumulada = nueva
                else:
                    n_omitidas += 1
            except Exception:  # noqa: BLE001
                n_omitidas += 1

        if n_omitidas:
            self.progreso.emit(
                -1,
                f"{pref}Unión incremental: {n_omitidas} de {len(saneadas)} "
                f"geometrías omitidas por no poder combinarse",
            )
        return acumulada

    def _a_crs_aoi(self, geom: QgsGeometry, crs_origen) -> QgsGeometry:
        """Reproyecta una geometría al CRS del área de interés."""
        crs_aoi = self.aoi.crs()
        if geom is None or geom.isEmpty():
            return geom
        if not crs_origen.isValid() or not crs_aoi.isValid() or crs_origen == crs_aoi:
            return geom
        xform = QgsCoordinateTransform(
            crs_origen, crs_aoi, QgsProject.instance().transformContext()
        )
        g = QgsGeometry(geom)
        if g.transform(xform) != 0:
            self.progreso.emit(
                -1,
                f"        ⚠ No se pudo reproyectar de {crs_origen.authid()} "
                f"a {crs_aoi.authid()}",
            )
            return QgsGeometry()
        return g

    def _union_geometrias(
        self, capa: QgsVectorLayer, criterio: "Criterio | None" = None
    ) -> QgsGeometry:
        """Une las geometrías de una capa y devuelve el resultado EN EL CRS DEL AOI.

        Aplica el filtro de atributos del criterio si está configurado y
        pre-filtra espacialmente con un bbox ya transformado al CRS de la capa
        (ver _bbox_busqueda). Usa unaryUnion() en lugar de combine() encadenado
        (O(n log n) frente a O(n²)).
        """
        from qgis.core import QgsFeatureRequest

        # Resolver el CRS una sola vez: _crs_efectivo emite avisos al registro
        crs_capa = self._crs_efectivo(capa)
        bbox = self._bbox_busqueda(capa, criterio, crs_capa_efectivo=crs_capa)
        request = QgsFeatureRequest().setFilterRect(bbox)

        n_total = capa.featureCount()
        n_bbox = 0
        n_descartados = 0
        geoms: list[QgsGeometry] = []

        for feat in capa.getFeatures(request):
            n_bbox += 1
            if criterio is not None and not self._es_prohibitivo(feat, criterio):
                n_descartados += 1
                continue
            g = feat.geometry()
            if g and not g.isEmpty():
                geoms.append(g)

        # Diagnóstico: distingue "la capa está vacía" de "no hay nada cerca del
        # AOI" de "el mapeo de atributos descartó todo".
        declarado = self._etiqueta_crs(capa.crs())
        efectivo = self._etiqueta_crs(crs_capa)
        etiqueta_crs = (efectivo if efectivo == declarado
                        else f"{efectivo} (declarado: {declarado})")
        self.progreso.emit(
            -1,
            f"        CRS de la capa: {etiqueta_crs}; "
            f"{n_total} elementos en total, {n_bbox} en el entorno del área",
        )
        if n_descartados:
            self.progreso.emit(
                -1,
                f"        {n_descartados} de {n_bbox} descartados por el mapeo "
                f"de atributos",
            )
        if n_total > 0 and n_bbox == 0:
            self.progreso.emit(
                -1,
                "        ⚠ La capa tiene datos pero ninguno cae cerca del área "
                "de interés. Verifique que cubra esta región.",
            )

        if not geoms:
            return QgsGeometry()

        union = self._union_segura(geoms, "unión de la capa")
        # Devolver siempre en el CRS del AOI: la intersección posterior con el
        # área de interés exige que ambas geometrías estén en el mismo CRS.
        return self._a_crs_aoi(union, crs_capa)

    def _buffer_metrico(
        self, geom: QgsGeometry, distancia_m: float, crs_origen: QgsCoordinateReferenceSystem
    ) -> QgsGeometry:
        """Aplica un buffer en metros sobre una geometría, respetando el CRS."""
        # Transformar a CRS métrico si es necesario. Se trabaja sobre una copia:
        # QgsGeometry.transform() muta en sitio y el llamador puede reutilizarla.
        crs_metrico = self.CRS_MEXICO
        ctx = QgsProject.instance().transformContext()
        geom = QgsGeometry(geom)

        if crs_origen != crs_metrico:
            xform = QgsCoordinateTransform(crs_origen, crs_metrico, ctx)
            geom.transform(xform)

        geom_buf = geom.buffer(distancia_m, segments=16)

        # Volver al CRS del área de interés
        crs_aoi = self.aoi.crs()
        if crs_metrico != crs_aoi:
            xform_back = QgsCoordinateTransform(crs_metrico, crs_aoi, ctx)
            geom_buf.transform(xform_back)

        return geom_buf

    def _excluir_usv(self, capa: QgsVectorLayer) -> QgsGeometry:
        """Retorna geometría de polígonos USV con clases excluidas.

        Pre-filtra espacialmente con el bbox ya transformado al CRS de la capa
        para no recorrer la cobertura nacional completa, y devuelve el
        resultado en el CRS del área de interés.
        """
        from qgis.core import QgsFeatureRequest

        campo_clave = None

        # Detectar campo con clave de clase (CLAVE, CVE_USV, TIPO, etc.)
        nombres_campo = [f.name().upper() for f in capa.fields()]
        for nombre in ["CLAVE", "CVE_USV", "DESCRIP", "TIPO", "USO"]:
            if nombre in nombres_campo:
                campo_clave = nombre
                break

        if campo_clave is None:
            self.progreso.emit(
                -1,
                "        ⚠ No se encontró un campo de clave de clase "
                f"(se buscó: CLAVE, CVE_USV, DESCRIP, TIPO, USO). "
                f"Campos disponibles: {', '.join(nombres_campo[:12]) or '—'}",
            )
            return QgsGeometry()   # No se puede filtrar

        crs_capa = self._crs_efectivo(capa)
        request = QgsFeatureRequest().setFilterRect(
            self._bbox_busqueda(capa, crs_capa_efectivo=crs_capa)
        )

        n_bbox = 0
        geoms: list[QgsGeometry] = []
        for feat in capa.getFeatures(request):
            n_bbox += 1
            valor = str(feat[campo_clave]).strip().upper()
            if any(valor.startswith(c.upper()) for c in USV_CLAVES_EXCLUIDAS):
                g = feat.geometry()
                if g and not g.isEmpty():
                    geoms.append(g)

        self.progreso.emit(
            -1,
            f"        CRS de la capa: {self._etiqueta_crs(crs_capa)}; "
            f"campo de clase «{campo_clave}»; {capa.featureCount()} elementos "
            f"en total, {n_bbox} en el entorno del área, "
            f"{len(geoms)} con clase excluida",
        )

        if not geoms:
            return QgsGeometry()

        return self._a_crs_aoi(self._union_segura(geoms, "USV"), crs_capa)

    @staticmethod
    def _umbral(criterio, por_defecto: float) -> float:
        """Umbral de exclusión del criterio, con respaldo si no está definido."""
        valor = getattr(criterio, "umbral_exclusion", None)
        try:
            valor = float(valor)
        except (TypeError, ValueError):
            return por_defecto
        return valor if valor > 0 else por_defecto

    def _excluir_por_lejania(self, criterio) -> QgsGeometry:
        """Excluye la superficie que queda MÁS LEJOS del umbral, no más cerca.

        Es el inverso de un buffer. Para la red vial, un sitio no se descarta
        por estar junto a una carretera sino por quedar fuera del alcance de
        los camiones recolectores: lo prohibitivo es la lejanía.

        La superficie excluida es, entonces, el área de interés menos la franja
        accesible (el buffer del umbral alrededor de las vías).
        """
        umbral = self._umbral(criterio, 5000.0)
        geom_aoi = self._geom_aoi_cacheada()

        geom_capa = self._union_geometrias(criterio.capa, criterio)
        if geom_capa is None or geom_capa.isEmpty():
            # Sin una sola vía en el entorno, TODA el área queda inaccesible.
            # Es un resultado legítimo, pero es indistinguible de una capa que
            # no se descargó, así que se avisa en lugar de excluirlo todo en
            # silencio.
            self.progreso.emit(
                -1,
                f"        ⚠ {criterio.nombre}: no se encontró ninguna geometría "
                f"en el entorno del área. Se omite el criterio en lugar de "
                f"declarar toda el área inaccesible; verifique que la capa "
                f"cubra esta región.")
            return QgsGeometry()

        accesible = self._buffer_metrico(geom_capa, umbral, self.aoi.crs())
        if accesible is None or accesible.isEmpty():
            return QgsGeometry()

        try:
            excluida = geom_aoi.difference(accesible)
        except Exception:  # noqa: BLE001
            excluida = QgsGeometry()

        if excluida is None:
            return QgsGeometry()

        self.progreso.emit(
            -1,
            f"        Exclusión por lejanía: se descarta lo que quede a más de "
            f"{umbral:,.0f} m de {criterio.nombre.lower()}")
        return excluida

    def _excluir_pendiente(
        self, raster_dem: QgsRasterLayer, umbral: float = 25.0
    ) -> QgsGeometry:
        """Genera geometría de exclusión donde la pendiente supera el umbral."""
        pendiente_tif = str(self.dir_salida / "pendiente_tmp.tif")
        mascara_tif   = str(self.dir_salida / "mascara_pendiente.tif")
        vector_shp    = str(self.dir_salida / "excluir_pendiente.gpkg")

        # Los tres pasos usan las APIs de GDAL en proceso, no los algoritmos
        # «gdal:*» de processing. Esos invocan scripts externos —gdal_calc.py,
        # gdal_polygonize.py— cuya disponibilidad depende del PATH y de a qué
        # intérprete de Python apunta su cabecera. Cuando fallan, lo hacen con
        # un código de error que el bucle de criterios absorbe, y el análisis
        # termina informando 0.00 km² excluidos por pendiente como si el
        # terreno fuera plano.

        # 1. Calcular pendiente con gdal.DEMProcessing
        escala = escala_pendiente(raster_dem.crs())
        if escala != 1.0:
            self.progreso.emit(
                -1,
                f"        MDE en coordenadas geográficas: se aplica "
                f"SCALE={escala:,.0f} para que la pendiente salga en % reales")

        ruta_dem = raster_dem.source().split("|")[0] \
            if hasattr(raster_dem, "source") else str(raster_dem)
        gdal.DEMProcessing(
            pendiente_tif, ruta_dem, "slope",
            options=gdal.DEMProcessingOptions(
                slopeFormat="percent", scale=escala, computeEdges=True),
        )

        # 2. Umbralizar con numpy: pendiente > umbral → 1, resto → 0
        ds_p = gdal.Open(pendiente_tif)
        if ds_p is None:
            self.progreso.emit(
                -1, "        ⚠ No se pudo calcular la pendiente del MDE")
            return QgsGeometry()
        banda_p = ds_p.GetRasterBand(1)
        nodata_p = banda_p.GetNoDataValue()
        pend = banda_p.ReadAsArray().astype(np.float32)
        gt, proj = ds_p.GetGeoTransform(), ds_p.GetProjection()
        filas, columnas = pend.shape
        ds_p = None

        valido = np.isfinite(pend)
        if nodata_p is not None:
            valido &= (pend != float(nodata_p))
        mascara = np.where(valido & (pend > umbral), 1, 0).astype(np.uint8)

        n_celdas = int(mascara.sum())
        self.progreso.emit(
            -1,
            f"        Pendiente: {n_celdas:,} de {int(valido.sum()):,} celdas "
            f"superan el {umbral:g} %")
        if n_celdas == 0:
            return QgsGeometry()

        ds_m = gdal.GetDriverByName("GTiff").Create(
            mascara_tif, columnas, filas, 1, gdal.GDT_Byte)
        ds_m.SetGeoTransform(gt)
        ds_m.SetProjection(proj)
        banda_m = ds_m.GetRasterBand(1)
        banda_m.SetNoDataValue(0)
        banda_m.WriteArray(mascara)
        ds_m.FlushCache()
        ds_m = None

        # 3. Vectorizar la máscara con gdal.Polygonize, usando la propia banda
        #    como máscara: solo se vectorizan las celdas con valor 1, así que
        #    no hace falta filtrar después los polígonos de valor 0.
        ds_m = gdal.Open(mascara_tif)
        banda_m = ds_m.GetRasterBand(1)

        drv_ogr = ogr.GetDriverByName("GPKG")
        if os.path.exists(vector_shp):
            drv_ogr.DeleteDataSource(vector_shp)
        ds_v = drv_ogr.CreateDataSource(vector_shp)
        srs = osr.SpatialReference()
        if proj:
            srs.ImportFromWkt(proj)
        capa_ogr = ds_v.CreateLayer("excluir_pendiente", srs=srs,
                                    geom_type=ogr.wkbPolygon)
        capa_ogr.CreateField(ogr.FieldDefn("valor", ogr.OFTInteger))

        gdal.Polygonize(banda_m, banda_m, capa_ogr, 0, [], callback=None)

        ds_v = None
        ds_m = None

        capa_vector = QgsVectorLayer(vector_shp, "excluir_pendiente", "ogr")
        if not capa_vector.isValid():
            self.progreso.emit(
                -1, "        ⚠ No se pudo abrir la máscara de pendiente vectorizada")
            return QgsGeometry()

        # gdal:polygonize vectoriza TODOS los valores del ráster, es decir tanto
        # las celdas con pendiente > umbral (valor 1) como las que la cumplen
        # (valor 0). Sin este filtro se uniría la extensión completa del MDT y
        # el área de interés quedaría excluida por entero.
        capa_vector.setSubsetString('"valor" = 1')
        n = capa_vector.featureCount()
        self.progreso.emit(
            -1,
            f"        Pendiente > {umbral:.0f} %: {n} polígonos tras filtrar "
            f'la máscara ("valor" = 1)',
        )
        if n == 0:
            return QgsGeometry()

        return self._union_geometrias(capa_vector)

    # ------------------------------------------------------------------
    # Ponderación — aptitud relativa sobre las zonas aptas
    # ------------------------------------------------------------------

    def ejecutar_fase2(self, capa_apta: QgsVectorLayer) -> Optional[QgsRasterLayer]:
        """Genera el ráster de aptitud ponderada sobre las zonas aptas.

        Parameters
        ----------
        capa_apta: capa de zonas aptas producida por el análisis de exclusión.

        Returns
        -------
        QgsRasterLayer con valores 0–1 (aptitud relativa).
        """
        self.progreso.emit(0, "═══ ANÁLISIS DE PONDERACIÓN ═══")

        if self.modo_analisis == "ponderado":
            # Modo ponderado: incluir cualquier criterio activo con capa cargada
            # y rol_ponderado == "ponderado" (independientemente de es_ponderacion).
            criterios_pond = [
                c for c in self.criterios
                if c.activo and c.capa is not None
                and c.rol_ponderado == "ponderado" and c.peso > 0
            ]
        else:
            # Modo dicotómico: usar la lista legacy (es_ponderacion=True)
            criterios_pond = [
                c for c in self.criterios
                if c.es_ponderacion and c.activo and c.capa is not None and c.peso > 0
            ]

        if not criterios_pond:
            self.progreso.emit(
                100,
                "Ningún criterio tiene rol Ponderado con peso > 0 %: "
                "no se calcula aptitud relativa.")
            self.fase2_lista.emit(None)
            return None

        # Verificar que los pesos sumen 100
        suma_pesos = sum(c.peso for c in criterios_pond)
        if abs(suma_pesos - 100.0) > 0.5:
            self.error.emit(
                f"La suma de los pesos es {suma_pesos:.1f} %, debe ser 100 %."
            )
            return None

        # Calcular extensión y resolución del ráster
        extent = capa_apta.extent()
        crs_aoi = capa_apta.crs()
        res = self._resolucion_en_unidades_mapa(self.RESOLUCION_M, crs_aoi)
        cols = max(1, int((extent.width()) / res))
        rows = max(1, int((extent.height()) / res))
        extent_str = (
            f"{extent.xMinimum()},{extent.xMaximum()},"
            f"{extent.yMinimum()},{extent.yMaximum()}"
            f" [{crs_aoi.authid()}]"
        )

        rasters_criterio: list[tuple[str, float]] = []  # (ruta_normalizado, peso)
        total = len(criterios_pond)

        for i, criterio in enumerate(criterios_pond):
            pct = int(i / total * 80)
            self.progreso.emit(
                pct, f"[{i + 1}/{total}] {criterio.nombre} — peso {criterio.peso:.1f} %")

            try:
                ruta_norm = self._raster_criterio(
                    criterio, extent_str, res, crs_aoi.authid(), capa_apta
                )
                if ruta_norm:
                    rasters_criterio.append((ruta_norm, criterio.peso / 100.0))
            except Exception as exc:  # noqa: BLE001
                self.progreso.emit(-1, f"        → ERROR: {exc}")

        if not rasters_criterio:
            self.error.emit("No se pudo generar ningún ráster de ponderación.")
            return None

        # Suma ponderada
        self.progreso.emit(85, "Calculando la suma ponderada de los rásters normalizados…")
        resultado_tif = str(self.dir_salida / "aptitud_ponderada.tif")
        resultado_final = self._suma_ponderada(rasters_criterio, resultado_tif)

        # Recortar al área apta con gdal.Warp (cutline vectorial)
        self.progreso.emit(93, "Recortando el ráster de aptitud al área permitida…")
        recortado_tif = str(self.dir_salida / "aptitud_final.tif")
        cutline_path = self._ogr_ruta(capa_apta)
        gdal.Warp(
            recortado_tif,
            resultado_final,
            format="GTiff",
            cutlineDSName=cutline_path,
            cropToCutline=True,
            dstNodata=-9999,
        )

        raster_final = QgsRasterLayer(recortado_tif, "Aptitud ponderada RSU")
        self.progreso.emit(100, "Análisis de ponderación terminado.")
        self.fase2_lista.emit(raster_final)
        return raster_final

    def _raster_criterio(
        self,
        criterio: Criterio,
        extent_str: str,
        res: float,
        crs_id: str,
        mascara: QgsVectorLayer,
    ) -> Optional[str]:
        """Genera un ráster normalizado [0,1] para un criterio de ponderación.

        Usa osgeo.gdal + numpy directamente (sin binarios externos), compatible
        con macOS, Linux y Windows independientemente del PATH de GDAL.
        """
        base = str(self.dir_salida / f"pond_{criterio.id}")

        # Caso especial: criterio categórico con puntaje_categorico (modo ponderado)
        if criterio.puntaje_categorico and criterio.filtro_campo:
            return self._raster_categorico_ponderado(criterio, extent_str, res, crs_id)

        # Caso especial: edafología — rasterizar por puntaje de impermeabilidad
        if criterio.id == "edafologia":
            return self._raster_edafologia(criterio, extent_str, res, crs_id)

        if criterio.tipo_score in (TipoScore.MAYOR_ES_MEJOR, TipoScore.MENOR_ES_MEJOR):
            xmin, xmax, ymin, ymax = self._parse_extent(extent_str)
            cols = max(1, int((xmax - xmin) / res))
            rows = max(1, int((ymax - ymin) / res))
            crs_wkt = QgsCoordinateReferenceSystem(crs_id).toWkt()

            # 1. Rasterizar presencia (valor = 1 donde hay geometría)
            presencia_tif = base + "_pres.tif"
            ds_pres = self._crear_raster_gdal(
                presencia_tif, cols, rows, xmin, ymax, res, crs_wkt,
                nodata=0, dtype=gdal.GDT_Byte,
            )
            ruta_vec = self._ogr_ruta(criterio.capa)
            ogr_ds = ogr.Open(ruta_vec)
            if ogr_ds is None:
                raise RuntimeError(f"No se pudo abrir la capa: {ruta_vec}")
            ogr_lyr = ogr_ds.GetLayer(0)
            gdal.RasterizeLayer(ds_pres, [1], ogr_lyr, burn_values=[1])
            ds_pres.FlushCache()
            ogr_ds = None
            ds_pres = None

            # 2. Calcular distancia euclidiana (gdal.ComputeProximity en unidades geo)
            dist_tif = base + "_dist.tif"
            src_ds = gdal.Open(presencia_tif)
            src_band = src_ds.GetRasterBand(1)
            drv_tif = gdal.GetDriverByName("GTiff")
            dst_ds = drv_tif.Create(dist_tif, cols, rows, 1, gdal.GDT_Float32)
            dst_ds.SetGeoTransform(src_ds.GetGeoTransform())
            dst_ds.SetProjection(src_ds.GetProjection())
            dst_band = dst_ds.GetRasterBand(1)
            dst_band.SetNoDataValue(-9999)
            gdal.ComputeProximity(
                src_band, dst_band,
                ["VALUES=1", "DISTUNITS=GEO", "NODATA=-9999"],
            )
            src_ds = None
            dst_ds.FlushCache()
            dst_ds = None

            norm_tif = base + "_norm.tif"

            # Si el criterio trae una escala explícita, se usa: anclar el
            # puntaje a valores que el usuario fijó hace comparables dos
            # análisis distintos. La normalización por mínimo y máximo
            # observados, en cambio, depende de los datos de cada corrida: el
            # sitio más cercano a una vía siempre puntúa 1.0, esté a 50 m o a
            # 8 km, lo que hace que dos municipios no se puedan comparar.
            optimo, peor = self._escala(criterio)
            if optimo is not None:
                factor = self._resolucion_en_unidades_mapa(
                    1.0, QgsCoordinateReferenceSystem(crs_id))
                self._normalizar_escala(
                    dist_tif, norm_tif,
                    optimo=optimo * factor, peor=peor * factor,
                )
                self.progreso.emit(
                    -1,
                    f"        Escala continua: {optimo:,.0f} m → 1.00, "
                    f"{peor:,.0f} m → 0.00")
            else:
                self._normalizar_raster(
                    dist_tif, norm_tif,
                    invertir=(criterio.tipo_score == TipoScore.MENOR_ES_MEJOR),
                )
            return norm_tif

        elif criterio.tipo_score == TipoScore.RANGO_OPTIMO:
            norm_tif = base + "_norm.tif"
            optimo, peor = self._escala(criterio)
            if optimo is not None:
                # Escala continua sobre los valores del ráster (p. ej. 5 % de
                # pendiente → 1.00, 30 % → 0.00). Las unidades son las del
                # propio ráster, así que no hay conversión que aplicar.
                ruta = self._ruta_raster_continuo(criterio)
                self._normalizar_escala(ruta, norm_tif, optimo=optimo, peor=peor)
                unidad = getattr(criterio, "unidad_umbral", "") or ""
                self.progreso.emit(
                    -1,
                    f"        Escala continua: {optimo:g} {unidad} → 1.00, "
                    f"{peor:g} {unidad} → 0.00")
            else:
                self._normalizar_rango_optimo(
                    criterio, norm_tif, extent_str, res, crs_id)
            return norm_tif

        return None

    @staticmethod
    def _escala(criterio) -> tuple:
        """Extremos de la escala continua, o (None, None) si no está definida.

        Se exige que ambos existan y sean distintos: una escala con un solo
        extremo, o con los dos iguales, produciría una división entre cero y un
        ráster de aptitud constante que no dice nada.
        """
        optimo = getattr(criterio, "escala_optimo", None)
        peor = getattr(criterio, "escala_peor", None)
        try:
            optimo, peor = float(optimo), float(peor)
        except (TypeError, ValueError):
            return (None, None)
        if abs(optimo - peor) < 1e-9:
            return (None, None)
        return (optimo, peor)

    def _ruta_raster_continuo(self, criterio) -> str:
        """Ruta del ráster de valores continuos de un criterio."""
        ruta = getattr(criterio, "ruta_raster_continuo", None)
        if ruta:
            return str(ruta)
        capa = criterio.capa
        if hasattr(capa, "source"):
            return capa.source().split("|")[0]
        return str(capa)

    def _normalizar_escala(
        self, entrada: str, salida: str, optimo: float, peor: float
    ) -> None:
        """Puntaje lineal anclado a dos valores que el usuario define.

        El valor «óptimo» puntúa 1.0 y el «peor» 0.0; en medio se interpola y
        fuera del intervalo se recorta. La fórmula sirve en ambas direcciones
        sin distinguir casos: si el óptimo es menor que el peor, el criterio
        mejora al decrecer (distancia a una vía); si es mayor, mejora al crecer.
        """
        ds = gdal.Open(entrada)
        if ds is None:
            raise RuntimeError(f"No se pudo abrir el ráster: {entrada}")
        band = ds.GetRasterBand(1)
        nodata_val = band.GetNoDataValue()
        data = band.ReadAsArray().astype(np.float32)
        gt, proj = ds.GetGeoTransform(), ds.GetProjection()
        rows, cols = data.shape
        ds = None

        mask = np.isfinite(data)
        if nodata_val is not None:
            mask &= (data != float(nodata_val))

        score = np.clip((data - peor) / (optimo - peor), 0.0, 1.0)
        out = np.where(mask, score, -9999).astype(np.float32)

        drv = gdal.GetDriverByName("GTiff")
        out_ds = drv.Create(salida, cols, rows, 1, gdal.GDT_Float32)
        out_ds.SetGeoTransform(gt)
        out_ds.SetProjection(proj)
        out_band = out_ds.GetRasterBand(1)
        out_band.SetNoDataValue(-9999)
        out_band.WriteArray(out)
        out_ds.FlushCache()
        out_ds = None

    def _raster_edafologia(
        self,
        criterio: Criterio,
        extent_str: str,
        res: float,
        crs_id: str,
    ) -> Optional[str]:
        """Rasteriza suelos asignando puntaje de impermeabilidad (1–9) por unidad FAO.

        Detecta el campo de símbolo FAO en la capa de suelos, mapea cada polígono
        al puntaje de PUNTAJE_EDAFOLOGIA, escribe un GeoPackage temporal con ese
        campo y lo rasteriza con osgeo.gdal (sin binarios externos).
        """
        capa = criterio.capa
        base = str(self.dir_salida / f"pond_{criterio.id}")

        # Detectar campo con código FAO
        campos_capa = [f.name() for f in capa.fields()]
        campo_fao = None
        for candidato in [
            "SIMBOLOGIA", "Simbologia", "SIMBOLO", "SUBUNIDAD",
            "TIPO_SUELO", "CLAVE", "TIPO", "TYPE", "SOIL_CODE",
        ]:
            if candidato in campos_capa:
                campo_fao = candidato
                break

        self.progreso.emit(-1,
            f"  Edafología: campo FAO detectado → '{campo_fao or 'no encontrado'}'")

        # Construir GeoPackage temporal con campo 'puntaje_rsu'
        tmp_gpkg = base + "_scored.gpkg"
        writer_fields = QgsFields()
        writer_fields.append(QgsField("puntaje_rsu", QVariant.Double))
        escritor = QgsVectorFileWriter(
            tmp_gpkg, "UTF-8", writer_fields,
            QgsWkbTypes.MultiPolygon, self._crs_efectivo(capa), "GPKG",
        )
        for feat in capa.getFeatures():
            puntaje = 5.0  # valor neutro por defecto
            if campo_fao:
                clave = str(feat[campo_fao]).strip()
                # Coincidencia exacta primero, luego primeras 2 letras
                if clave in PUNTAJE_EDAFOLOGIA:
                    puntaje = float(PUNTAJE_EDAFOLOGIA[clave])
                else:
                    clave2 = clave[:2]
                    if clave2 in PUNTAJE_EDAFOLOGIA:
                        puntaje = float(PUNTAJE_EDAFOLOGIA[clave2])
            nuevo = QgsFeature(writer_fields)
            nuevo.setGeometry(feat.geometry())
            nuevo.setAttribute("puntaje_rsu", puntaje)
            escritor.addFeature(nuevo)
        del escritor  # cierra y escribe a disco

        # Rasterizar por campo puntaje_rsu con osgeo.gdal
        xmin, xmax, ymin, ymax = self._parse_extent(extent_str)
        cols = max(1, int((xmax - xmin) / res))
        rows = max(1, int((ymax - ymin) / res))
        crs_wkt = QgsCoordinateReferenceSystem(crs_id).toWkt()

        raw_tif = base + "_raw.tif"
        ds_raw = self._crear_raster_gdal(
            raw_tif, cols, rows, xmin, ymax, res, crs_wkt,
            nodata=-9999, dtype=gdal.GDT_Float32,
        )
        ogr_ds = ogr.Open(tmp_gpkg)
        if ogr_ds is None:
            raise RuntimeError(f"No se pudo abrir la capa de edafología: {tmp_gpkg}")
        ogr_lyr = ogr_ds.GetLayer(0)
        gdal.RasterizeLayer(ds_raw, [1], ogr_lyr, options=["ATTRIBUTE=puntaje_rsu"])
        ds_raw.FlushCache()
        ogr_ds = None
        ds_raw = None

        norm_tif = base + "_norm.tif"
        self._normalizar_raster(raw_tif, norm_tif, invertir=False)
        return norm_tif

    def _raster_categorico_ponderado(
        self,
        criterio: Criterio,
        extent_str: str,
        res: float,
        crs_id: str,
    ) -> Optional[str]:
        """Rasteriza una capa categórica usando puntaje_categorico (0–100 por valor).

        Para criterios en modo ponderado cuyo campo de interés es categórico y el
        usuario ha asignado un puntaje 0–100 a cada valor único del campo. El ráster
        resultante se divide por 100 para obtener valores [0, 1].
        """
        capa = criterio.capa
        campo = criterio.filtro_campo
        puntajes = criterio.puntaje_categorico or {}
        base = str(self.dir_salida / f"pond_{criterio.id}")

        # Construir GeoPackage temporal con campo 'puntaje_rsu'
        tmp_gpkg = base + "_scored.gpkg"
        writer_fields = QgsFields()
        writer_fields.append(QgsField("puntaje_rsu", QVariant.Double))
        escritor = QgsVectorFileWriter(
            tmp_gpkg, "UTF-8", writer_fields,
            QgsWkbTypes.MultiPolygon, self._crs_efectivo(capa), "GPKG",
        )
        for feat in capa.getFeatures():
            try:
                val = str(feat[campo]).strip()
            except Exception:
                val = ""
            puntaje = float(puntajes.get(val, 50))  # neutro si no mapeado
            nuevo = QgsFeature(writer_fields)
            nuevo.setGeometry(feat.geometry())
            nuevo.setAttribute("puntaje_rsu", puntaje)
            escritor.addFeature(nuevo)
        del escritor

        # Rasterizar por campo puntaje_rsu
        xmin, xmax, ymin, ymax = self._parse_extent(extent_str)
        cols = max(1, int((xmax - xmin) / res))
        rows = max(1, int((ymax - ymin) / res))
        crs_wkt = QgsCoordinateReferenceSystem(crs_id).toWkt()

        raw_tif = base + "_raw.tif"
        ds_raw = self._crear_raster_gdal(
            raw_tif, cols, rows, xmin, ymax, res, crs_wkt,
            nodata=-9999, dtype=gdal.GDT_Float32,
        )
        ogr_ds = ogr.Open(tmp_gpkg)
        if ogr_ds is None:
            raise RuntimeError(f"No se pudo abrir capa temporal: {tmp_gpkg}")
        ogr_lyr = ogr_ds.GetLayer(0)
        gdal.RasterizeLayer(ds_raw, [1], ogr_lyr, options=["ATTRIBUTE=puntaje_rsu"])
        ds_raw.FlushCache()
        ogr_ds = None
        ds_raw = None

        # Dividir por 100 → [0, 1]
        norm_tif = base + "_norm.tif"
        self._dividir_por_100(raw_tif, norm_tif)
        return norm_tif

    def _dividir_por_100(self, entrada: str, salida: str) -> None:
        """Divide los valores de un ráster entre 100 para escalar de [0,100] a [0,1].

        Aplica nodata=-9999 como máscara para que las celdas sin datos no se escalen.
        """
        ds = gdal.Open(entrada)
        band = ds.GetRasterBand(1)
        nodata_val = band.GetNoDataValue()
        data = band.ReadAsArray().astype(np.float32)
        gt = ds.GetGeoTransform()
        proj = ds.GetProjection()
        rows, cols = data.shape
        ds = None

        mask = np.isfinite(data)
        if nodata_val is not None:
            mask &= (data != float(nodata_val))

        out = np.where(mask, np.clip(data / 100.0, 0.0, 1.0), -9999).astype(np.float32)

        drv = gdal.GetDriverByName("GTiff")
        out_ds = drv.Create(salida, cols, rows, 1, gdal.GDT_Float32)
        out_ds.SetGeoTransform(gt)
        out_ds.SetProjection(proj)
        out_band = out_ds.GetRasterBand(1)
        out_band.SetNoDataValue(-9999)
        out_band.WriteArray(out)
        out_ds.FlushCache()
        out_ds = None

    def _normalizar_raster(
        self, entrada: str, salida: str, invertir: bool = False
    ) -> None:
        """Normaliza un ráster al rango [0, 1] usando numpy (sin binarios externos).

        Si invertir=True aplica 1 − norm (menor valor = mayor score).
        """
        ds = gdal.Open(entrada)
        band = ds.GetRasterBand(1)
        nodata_val = band.GetNoDataValue()
        data = band.ReadAsArray().astype(np.float32)
        gt = ds.GetGeoTransform()
        proj = ds.GetProjection()
        rows, cols = data.shape
        ds = None

        mask = np.isfinite(data)
        if nodata_val is not None:
            mask &= (data != float(nodata_val))

        valid = data[mask]
        if valid.size == 0:
            out = np.full((rows, cols), -9999, dtype=np.float32)
        else:
            vmin, vmax = float(valid.min()), float(valid.max())
            rng = max(vmax - vmin, 1e-6)
            norm = (data - vmin) / rng
            if invertir:
                norm = 1.0 - norm
            out = np.where(mask, np.clip(norm, 0.0, 1.0), -9999).astype(np.float32)

        drv = gdal.GetDriverByName("GTiff")
        out_ds = drv.Create(salida, cols, rows, 1, gdal.GDT_Float32)
        out_ds.SetGeoTransform(gt)
        out_ds.SetProjection(proj)
        out_band = out_ds.GetRasterBand(1)
        out_band.SetNoDataValue(-9999)
        out_band.WriteArray(out)
        out_ds.FlushCache()
        out_ds = None

    def _normalizar_rango_optimo(
        self,
        criterio: Criterio,
        salida: str,
        extent_str: str,
        res: float,
        crs_id: str,
    ) -> None:
        """Score = 1 dentro del rango óptimo, decrece linealmente fuera (numpy).

        criterio.capa debe ser un QgsRasterLayer de pendiente (u otro ráster continuo).
        """
        r_min = float(criterio.rango_optimo_min or 2.0)
        r_max = float(criterio.rango_optimo_max or 15.0)

        # Obtener ruta del ráster de criterio. Cuando la capa principal es una
        # vectorización (pendiente → polígonos), se usa el ráster continuo que
        # el descargador dejó registrado; abrir el vector con GDAL daría basura.
        ruta_raster = getattr(criterio, "ruta_raster_continuo", None)
        if not ruta_raster:
            if hasattr(criterio.capa, "source"):
                ruta_raster = criterio.capa.source().split("|")[0]
            else:
                ruta_raster = str(criterio.capa)

        ds = gdal.Open(ruta_raster)
        if ds is None:
            raise RuntimeError(
                f"No se pudo abrir como ráster continuo: {ruta_raster}. "
                f"El criterio «{criterio.nombre}» requiere un ráster de valores "
                f"(no una vectorización) para la ponderación por rango óptimo."
            )
        band = ds.GetRasterBand(1)
        nodata_val = band.GetNoDataValue()
        data = band.ReadAsArray().astype(np.float32)
        gt = ds.GetGeoTransform()
        proj = ds.GetProjection()
        rows, cols = data.shape
        ds = None

        mask = np.isfinite(data)
        if nodata_val is not None:
            mask &= (data != float(nodata_val))

        score = np.where(
            mask,
            # dentro del rango → 1; por debajo → rampa ascendente; por arriba → rampa descendente
            ((data >= r_min) & (data <= r_max)).astype(np.float32) * 1.0
            + ((data < r_min).astype(np.float32)) * np.maximum(0.0, data / r_min)
            + ((data > r_max).astype(np.float32)) * np.maximum(0.0, 1.0 - (data - r_max) / r_max),
            -9999,
        ).astype(np.float32)

        drv = gdal.GetDriverByName("GTiff")
        out_ds = drv.Create(salida, cols, rows, 1, gdal.GDT_Float32)
        out_ds.SetGeoTransform(gt)
        out_ds.SetProjection(proj)
        out_band = out_ds.GetRasterBand(1)
        out_band.SetNoDataValue(-9999)
        out_band.WriteArray(score)
        out_ds.FlushCache()
        out_ds = None

    def _suma_ponderada(
        self, rasters: list[tuple[str, float]], salida: str
    ) -> str:
        """Suma ponderada de rásters normalizados usando numpy (sin binarios externos)."""
        acum: Optional[np.ndarray] = None
        valid_mask: Optional[np.ndarray] = None
        gt = proj = None
        rows = cols = 0
        peso_perdido = [0.0]

        for ruta, peso in rasters:
            ds = gdal.Open(ruta)
            band = ds.GetRasterBand(1)
            nodata_val = band.GetNoDataValue()
            data = band.ReadAsArray().astype(np.float32)
            if acum is None:
                gt = ds.GetGeoTransform()
                proj = ds.GetProjection()
                rows, cols = data.shape
                acum = np.zeros((rows, cols), dtype=np.float32)
                valid_mask = np.zeros((rows, cols), dtype=bool)
            ds = None

            m = np.isfinite(data)
            if nodata_val is not None:
                m &= (data != float(nodata_val))

            # Un criterio sin una sola celda válida no aporta nada, pero su peso
            # ya se contó al validar que la suma diera 100 %. El resultado queda
            # con un techo de aptitud menor que 1 y un mapa cuya escala no
            # significa lo que el usuario cree. Antes ocurría en silencio.
            if not m.any():
                peso_perdido[0] += peso
                self.progreso.emit(
                    -1,
                    f"        ⚠ {Path(ruta).stem} no aportó ninguna celda válida; "
                    f"su {peso * 100:.1f} % de peso se pierde")

            acum += np.where(m, data * peso, 0.0)
            valid_mask |= m  # type: ignore[operator]

        if acum is None:
            raise RuntimeError("_suma_ponderada: lista de rásters vacía.")

        if peso_perdido[0] > 0:
            self.progreso.emit(
                -1,
                f"        ⚠ La aptitud se calculó con "
                f"{(1.0 - peso_perdido[0]) * 100:.1f} % del peso asignado: "
                f"el máximo posible del mapa es "
                f"{1.0 - peso_perdido[0]:.2f}, no 1.00. Revise que las capas de "
                f"los criterios ponderados cubran el área permitida.")

        out = np.where(valid_mask, acum, -9999).astype(np.float32)

        drv = gdal.GetDriverByName("GTiff")
        out_ds = drv.Create(salida, cols, rows, 1, gdal.GDT_Float32)
        out_ds.SetGeoTransform(gt)
        out_ds.SetProjection(proj)
        out_band = out_ds.GetRasterBand(1)
        out_band.SetNoDataValue(-9999)
        out_band.WriteArray(out)
        out_ds.FlushCache()
        out_ds = None
        return salida

    # ------------------------------------------------------------------
    # Utilidades
    # ------------------------------------------------------------------

    def _area_km2(self, geom: QgsGeometry) -> float:
        """Superficie de una geometría en km², medida sobre el elipsoide."""
        if geom is None or geom.isEmpty():
            return 0.0
        try:
            da = QgsDistanceArea()
            da.setSourceCrs(self.aoi.crs(), QgsProject.instance().transformContext())
            da.setEllipsoid(self._elipsoide_medicion())
            return float(da.measureArea(geom)) / 1_000_000.0
        except Exception:  # noqa: BLE001 — si la medición falla, no se reporta área
            return 0.0

    def _elipsoide_medicion(self) -> str:
        """Elipsoide con el que medir superficies.

        QGIS devuelve la CADENA ``"NONE"`` cuando el proyecto está configurado
        para medición planimétrica, que es el valor de fábrica de muchos
        proyectos. Esa cadena es verdadera en Python, de modo que un
        ``ellipsoid() or "WGS84"`` la deja pasar y ``measureArea`` acaba
        devolviendo grados cuadrados para un área de interés geográfica: el
        reporte entero muestra 0.00 km² sin ningún aviso.

        Se prefiere el elipsoide del proyecto cuando el usuario lo configuró,
        luego el que declare el CRS del área de interés, y por último WGS84.
        """
        elipsoide = (QgsProject.instance().ellipsoid() or "").strip()
        if elipsoide and elipsoide.upper() != "NONE":
            return elipsoide

        try:
            del_crs = (self.aoi.crs().ellipsoidAcronym() or "").strip()
        except Exception:  # noqa: BLE001
            del_crs = ""
        if del_crs and del_crs.upper() != "NONE":
            return del_crs

        return "EPSG:7030"   # WGS 84

    def _geom_aoi(self) -> QgsGeometry:
        """Retorna la unión de las geometrías del área de interés (cacheada)."""
        return self._geom_aoi_cacheada()

    def _geom_aoi_cacheada(self) -> QgsGeometry:
        """Calcula y cachea la unión del AOI para evitar recalcularla en cada criterio."""
        if not hasattr(self, "_cache_geom_aoi") or self._cache_geom_aoi is None:
            geom = QgsGeometry()
            for feat in self.aoi.getFeatures():
                g = feat.geometry()
                if g and not g.isEmpty():
                    geom = geom.combine(g) if not geom.isEmpty() else g
            self._cache_geom_aoi = geom
        return self._cache_geom_aoi

    def _geom_a_capa(
        self, geom: QgsGeometry, nombre: str, ruta: str
    ) -> QgsVectorLayer:
        """Guarda una geometría como capa vectorial en GeoPackage."""
        crs = self.aoi.crs()
        wkb_type = QgsWkbTypes.MultiPolygon

        campos = QgsFields()
        campos.append(QgsField("tipo", QVariant.String))

        escritor = QgsVectorFileWriter(
            ruta, "UTF-8", campos, wkb_type, crs, "GPKG"
        )
        feat = QgsFeature()
        feat.setFields(campos)
        feat.setGeometry(geom)
        feat.setAttribute("tipo", nombre)
        escritor.addFeature(feat)
        del escritor  # Fuerza escritura y cierre

        return QgsVectorLayer(ruta, nombre, "ogr")

    def _copiar_como_vectorial(
        self, capa: QgsVectorLayer, nombre: str
    ) -> QgsVectorLayer:
        """Copia una capa vectorial a un GeoPackage nuevo."""
        ruta = str(self.dir_salida / f"{nombre}.gpkg")
        processing.run("native:savefeatures", {
            "INPUT": capa,
            "OUTPUT": ruta,
        }, context=self._ctx, feedback=self._feedback)
        return QgsVectorLayer(ruta, nombre, "ogr")

    @staticmethod
    def _resolucion_en_unidades_mapa(metros: float, crs=None) -> float:
        """Convierte una resolución en metros a las unidades de mapa del CRS.

        La versión anterior dividía siempre entre 111 320, que es correcto sólo
        si el área de interés es geográfica. Con un área de interés proyectada
        —EPSG:6372, el CRS del Marco Geoestadístico del INEGI, cuyas unidades
        ya son metros— una celda de 100 m se convertía en 0.0009 m: menos de un
        milímetro. El ráster de aptitud pasaba de unos cientos de miles de
        celdas a billones, y el análisis se quedaba colgado o agotaba la
        memoria sin decir por qué.
        """
        if crs is not None and crs.isValid() and not crs.isGeographic():
            try:
                from qgis.core import QgsUnitTypes
                factor = QgsUnitTypes.fromUnitToUnitFactor(
                    QgsUnitTypes.DistanceMeters, crs.mapUnits())
                if factor > 0:
                    return metros * factor
            except Exception:  # noqa: BLE001
                pass
            return metros   # unidades lineales desconocidas: se asumen metros
        # CRS geográfico: 1° de latitud ≈ 111 320 m en México (~20° N)
        return metros / 111_320.0


    # ------------------------------------------------------------------
    # Auxiliares GDAL / OGR (sin binarios externos)
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_extent(extent_str: str) -> tuple[float, float, float, float]:
        """Parsea 'xmin,xmax,ymin,ymax [CRS]' y devuelve (xmin, xmax, ymin, ymax)."""
        partes = extent_str.split("[")[0].strip().rstrip(",").split(",")
        return float(partes[0]), float(partes[1]), float(partes[2]), float(partes[3])

    @staticmethod
    def _crear_raster_gdal(
        ruta: str,
        cols: int,
        rows: int,
        xmin: float,
        ymax: float,
        res: float,
        crs_wkt: str,
        nodata: float = -9999,
        dtype: int = None,  # gdal.GDT_Float32
    ) -> "gdal.Dataset":
        """Crea un GeoTiff vacío listo para rasterizar.

        GeoTransform: (xmin, res, 0, ymax, 0, -res) — origen en esquina superior izquierda.
        """
        if dtype is None:
            dtype = gdal.GDT_Float32
        drv = gdal.GetDriverByName("GTiff")
        ds = drv.Create(ruta, cols, rows, 1, dtype)
        ds.SetGeoTransform([xmin, res, 0.0, ymax, 0.0, -res])
        ds.SetProjection(crs_wkt)
        band = ds.GetRasterBand(1)
        band.SetNoDataValue(nodata)
        band.Fill(nodata)
        return ds

    def _ogr_ruta(self, capa: QgsVectorLayer) -> str:
        """Devuelve la ruta en disco de una QgsVectorLayer.

        Si es una capa en memoria («memory:»), la guarda primero en un
        GeoPackage temporal dentro del directorio de salida.
        """
        src = capa.source()
        # Las capas de archivo vienen como "/ruta/archivo.gpkg|layername=xxx"
        ruta = src.split("|")[0]
        if ruta.startswith("memory:") or not os.path.exists(ruta):
            tmp = str(self.dir_salida / f"_tmp_ogr_{capa.name()}.gpkg")
            QgsVectorFileWriter.writeAsVectorFormat(
                capa, tmp, "UTF-8", self._crs_efectivo(capa), "GPKG"
            )
            ruta = tmp
        return ruta
