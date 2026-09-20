# -*- coding: utf-8 -*-
"""
Arnés de pruebas para EvaluaciónRSU — corre sin interfaz, contra QGIS real.

Uso:
    QT_QPA_PLATFORM=offscreen python3.12 pruebas/arnes.py

Levanta una QgsApplication sin interfaz, inicializa el marco de processing y
expone utilidades para construir capas sintéticas en memoria o en disco. Esto
permite ejercitar el motor de análisis sin depender de las descargas ni de la
GUI, y reproducir de forma determinista los errores que se corrigieron.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/usr/share/qgis/python/plugins")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qgis.core import (
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsProject,
    QgsVectorFileWriter,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QVariant


# ---------------------------------------------------------------------------
# Ciclo de vida de la aplicación
# ---------------------------------------------------------------------------

_APP = None


def iniciar():
    """Inicializa QGIS y el marco de processing una sola vez."""
    global _APP
    if _APP is not None:
        return _APP
    QgsApplication.setPrefixPath("/usr", True)
    _APP = QgsApplication([], False)
    _APP.initQgis()
    from processing.core.Processing import Processing
    Processing.initialize()
    return _APP


def terminar(codigo: int = 0):
    """Termina el proceso sin desmontar QGIS.

    Llamar a ``exitQgis()`` y dejar que el intérprete libere después los objetos
    de QGIS que siguen referenciados produce un segmentation fault durante el
    cierre. El proceso acaba con código 139 aunque todas las pruebas hayan
    pasado, lo que rompería cualquier integración continua.

    Se sale con ``os._exit`` para saltarse esa fase de liberación: el sistema
    operativo recupera la memoria igual y el código de salida es el real.
    """
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(codigo)


# ---------------------------------------------------------------------------
# Construcción de capas sintéticas
# ---------------------------------------------------------------------------

def capa_memoria(wkts, crs="EPSG:4326", nombre="prueba", campos=None,
                 atributos=None, tipo="Polygon"):
    """Capa vectorial en memoria a partir de una lista de WKT.

    ``campos`` es una lista de (nombre, QVariant); ``atributos`` una lista de
    listas paralela a ``wkts``.
    """
    capa = QgsVectorLayer(f"{tipo}?crs={crs}", nombre, "memory")
    dp = capa.dataProvider()
    if campos:
        dp.addAttributes([QgsField(n, t) for n, t in campos])
        capa.updateFields()
    feats = []
    for i, wkt in enumerate(wkts):
        f = QgsFeature(capa.fields())
        f.setGeometry(QgsGeometry.fromWkt(wkt))
        if atributos:
            f.setAttributes(atributos[i])
        feats.append(f)
    dp.addFeatures(feats)
    capa.updateExtents()
    return capa


def capa_en_disco(wkts, crs, ruta, nombre="prueba", campos=None, atributos=None):
    """Escribe una capa a GeoPackage conservando su CRS real."""
    capa = capa_memoria(wkts, crs=crs, nombre=nombre, campos=campos,
                        atributos=atributos)
    opciones = QgsVectorFileWriter.SaveVectorOptions()
    opciones.driverName = "GPKG"
    opciones.fileEncoding = "UTF-8"
    QgsVectorFileWriter.writeAsVectorFormatV3(
        capa, str(ruta), QgsProject.instance().transformContext(), opciones
    )
    return QgsVectorLayer(str(ruta), nombre, "ogr")


# WKT que localidades_urbanas.prj declara realmente: un sistema geográfico en
# grados, mientras el .shp contiene coordenadas en metros.
PRJ_MENTIROSO = (
    'GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",'
    'SPHEROID["WGS_1984",6378137.0,298.257223563]],'
    'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]]'
)


def shapefile_con_prj_falso(wkts, crs_real, carpeta, nombre="mentirosa",
                            prj_declarado=PRJ_MENTIROSO):
    """Shapefile cuyas coordenadas y cuyo .prj se contradicen.

    Reproduce el defecto real de localidades_urbanas y humedales: el archivo
    se escribe con coordenadas en ``crs_real`` (metros) y después se sobrescribe
    el ``.prj`` con una declaración geográfica. Pasar ``prj_declarado=None``
    elimina el ``.prj``, que es el caso de humedales_febrero_2012.
    """
    carpeta = Path(carpeta)
    carpeta.mkdir(parents=True, exist_ok=True)
    base = carpeta / f"{nombre}.shp"

    capa = capa_memoria(wkts, crs=crs_real, nombre=nombre)
    opciones = QgsVectorFileWriter.SaveVectorOptions()
    opciones.driverName = "ESRI Shapefile"
    opciones.fileEncoding = "UTF-8"
    QgsVectorFileWriter.writeAsVectorFormatV3(
        capa, str(base), QgsProject.instance().transformContext(), opciones
    )

    prj = base.with_suffix(".prj")
    if prj_declarado is None:
        if prj.exists():
            prj.unlink()
    else:
        prj.write_text(prj_declarado, encoding="utf-8")

    return QgsVectorLayer(str(base), nombre, "ogr")


def aoi_veracruz(crs="EPSG:4326"):
    """Área de interés de referencia: costa centro de Veracruz, ~3 000 km²."""
    wkt = ("POLYGON((-96.95 19.05, -96.45 19.05, -96.45 19.60, "
           "-96.95 19.60, -96.95 19.05))")
    capa = capa_memoria([wkt], crs="EPSG:4326", nombre="aoi")
    if crs == "EPSG:4326":
        return capa
    # Reproyectar a otro CRS conservando la misma superficie en el terreno
    destino = QgsCoordinateReferenceSystem(crs)
    xform = QgsCoordinateTransform(
        QgsCoordinateReferenceSystem("EPSG:4326"), destino,
        QgsProject.instance().transformContext())
    g = QgsGeometry.fromWkt(wkt)
    g.transform(xform)
    return capa_memoria([g.asWkt()], crs=crs, nombre="aoi")


def dir_temporal(nombre="rsu_pruebas"):
    d = Path(tempfile.mkdtemp(prefix=nombre + "_"))
    return d


# ---------------------------------------------------------------------------
# Reporte de resultados
# ---------------------------------------------------------------------------

class Reporte:
    """Acumulador de resultados con salida legible."""

    def __init__(self):
        self.casos = []

    def comprobar(self, ident, descripcion, condicion, detalle=""):
        ok = bool(condicion)
        self.casos.append((ident, descripcion, ok, detalle))
        marca = "PASA" if ok else "FALLA"
        print(f"  [{marca}] {ident}  {descripcion}")
        if detalle:
            print(f"         {detalle}")
        return ok

    def resumen(self):
        total = len(self.casos)
        pasan = sum(1 for c in self.casos if c[2])
        fallan = total - pasan
        print("\n" + "=" * 62)
        print(f"  {pasan} de {total} comprobaciones pasan"
              + (f", {fallan} FALLAN" if fallan else ""))
        if fallan:
            print("\n  Fallos:")
            for ident, desc, ok, det in self.casos:
                if not ok:
                    print(f"    {ident}  {desc}")
                    if det:
                        print(f"          {det}")
        print("=" * 62)
        return fallan == 0
