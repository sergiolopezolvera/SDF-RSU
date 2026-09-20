# -*- coding: utf-8 -*-
"""
Pruebas de la carga de archivos del usuario — la ruta «Seleccionar archivo…».

Ninguna prueba anterior tocaba esta ruta: todas construían las capas a mano.
Pero es la que usa cualquiera que traiga sus propios datos, y la única
disponible para los criterios sin descarga automática.

Se ejercita ``Descargador.cargar_archivo_local``, que es lo que el asistente
invoca de verdad: ``inicializar_descargador`` corre al pasar del Paso 2 al 3 y
el botón de archivo vive en el Paso 3, así que cuando el usuario puede pulsarlo
el descargador ya existe y la rama alternativa de la interfaz nunca se alcanza.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import arnes

arnes.iniciar()

from osgeo import gdal, osr
from qgis.core import (
    QgsProject,
    QgsRasterLayer,
    QgsVectorFileWriter,
    QgsVectorLayer,
)

from evaluacion_rsu.core.criterios import Criterio, TipoExclusion
from evaluacion_rsu.core.descargador import Descargador

R = arnes.Reporte()
TMP = arnes.dir_temporal("carga")

AOI_WKT = "POLYGON((-96.9 19.1, -96.5 19.1, -96.5 19.5, -96.9 19.5, -96.9 19.1))"
aoi = arnes.capa_memoria([AOI_WKT], crs="EPSG:4326", nombre="aoi")

# Un polígono cualquiera dentro del área de interés, para escribirlo en
# cada formato que el diálogo de archivos ofrece.
POLI = "POLYGON((-96.8 19.2, -96.7 19.2, -96.7 19.3, -96.8 19.3, -96.8 19.2))"


def escribir(formato: str, extension: str) -> Path:
    """Escribe el mismo polígono en el formato pedido y devuelve su ruta."""
    destino = TMP / f"muestra{extension}"
    capa = arnes.capa_memoria([POLI], crs="EPSG:4326", nombre="muestra")
    opciones = QgsVectorFileWriter.SaveVectorOptions()
    opciones.driverName = formato
    opciones.fileEncoding = "UTF-8"
    QgsVectorFileWriter.writeAsVectorFormatV3(
        capa, str(destino), QgsProject.instance().transformContext(), opciones)
    return destino


def raster_pendiente(ruta: Path, valor: float = 8.0) -> Path:
    """Ráster de elevación, del tipo que el usuario cargaría a mano (MDE)."""
    cols = rows = 64
    drv = gdal.GetDriverByName(
        {".tif": "GTiff", ".img": "HFA", ".asc": "AAIGrid"}[ruta.suffix.lower()])
    if ruta.suffix.lower() == ".asc":
        # AAIGrid sólo admite CreateCopy: se construye en memoria primero.
        mem = gdal.GetDriverByName("MEM").Create("", cols, rows, 1, gdal.GDT_Float32)
        _preparar(mem, cols, rows, valor)
        drv.CreateCopy(str(ruta), mem)
        mem = None
    else:
        ds = drv.Create(str(ruta), cols, rows, 1, gdal.GDT_Float32)
        _preparar(ds, cols, rows, valor)
        ds.FlushCache()
        ds = None
    return ruta


def _preparar(ds, cols, rows, valor):
    ds.SetGeoTransform([-96.9, 0.4 / cols, 0.0, 19.5, 0.0, -0.4 / rows])
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(4326)
    ds.SetProjection(srs.ExportToWkt())
    banda = ds.GetRasterBand(1)
    banda.WriteArray(np.full((rows, cols), valor, dtype=np.float32))


def criterio(cid="generico", nombre="Genérico"):
    return Criterio(id=cid, nombre=nombre, descripcion="",
                    tipo_exclusion=TipoExclusion.TRASLAPE, activo=True)


desc = Descargador(aoi, str(TMP))


# ===========================================================================
print("\nFormatos vectoriales ofrecidos en el diálogo de archivos")
# ===========================================================================
# El filtro dice: Vectoriales (*.shp *.gpkg *.geojson *.kml)

for etiqueta, formato, extension in [
    ("Shapefile", "ESRI Shapefile", ".shp"),
    ("GeoPackage", "GPKG", ".gpkg"),
    ("GeoJSON", "GeoJSON", ".geojson"),
    ("KML", "KML", ".kml"),
]:
    try:
        ruta = escribir(formato, extension)
        existe = ruta.exists()
    except Exception as exc:  # noqa: BLE001
        R.comprobar(f"V.{extension}", f"{etiqueta}: se puede escribir el archivo",
                    False, f"{type(exc).__name__}: {exc}")
        continue

    c = criterio(f"v{extension.strip('.')}", etiqueta)
    capa = desc.cargar_archivo_local(c, str(ruta))
    R.comprobar(
        f"V.{extension}", f"{etiqueta} ({extension}) se carga y queda válida",
        capa is not None and capa.isValid() and capa.featureCount() > 0,
        f"válida={capa.isValid() if capa else None}, "
        f"objetos={capa.featureCount() if capa else 0}")
    R.comprobar(
        f"V.{extension}.origen", f"{etiqueta}: queda marcado como carga manual",
        c.origen_carga == "manual" and c.ruta_dato == str(ruta),
        f"origen_carga={c.origen_carga!r}")


# ===========================================================================
print("\nFormatos ráster ofrecidos en el MISMO diálogo")
# ===========================================================================
# El filtro también ofrece: Ráster (*.tif *.img *.asc), y la descripción del
# criterio de pendiente le dice al usuario que cargue ahí su ráster.

for etiqueta, extension in [("GeoTIFF", ".tif"), ("Erdas IMAGINE", ".img"),
                            ("ASCII Grid", ".asc")]:
    ruta = raster_pendiente(TMP / f"pendiente{extension}")
    R.comprobar(
        f"Rprev.{extension}", f"{etiqueta}: el archivo de prueba es un ráster válido",
        QgsRasterLayer(str(ruta), "x").isValid(),
        f"{ruta.name}, {ruta.stat().st_size:,} bytes")

    c = criterio("pendiente", "Pendiente del terreno (%)")
    capa = desc.cargar_archivo_local(c, str(ruta))
    R.comprobar(
        f"R.{extension}", f"{etiqueta} ({extension}) se carga como capa ráster",
        capa is not None and capa.isValid(),
        f"devolvió {type(capa).__name__ if capa is not None else 'None'}"
        + (f", válida={capa.isValid()}" if capa is not None else ""))
    R.comprobar(
        f"R.{extension}.tipo", f"{etiqueta}: la capa es QgsRasterLayer, no vectorial",
        isinstance(capa, QgsRasterLayer),
        f"tipo = {type(capa).__name__ if capa is not None else 'None'}")


# ===========================================================================
print("\nPendiente cargada a mano: lo que necesita la ponderación")
# ===========================================================================
# La ponderación por rango óptimo lee criterio.ruta_raster_continuo. Si la
# carga manual no lo fija, el criterio de pendiente queda sin valores continuos
# y la aptitud por rango óptimo no puede calcularse.

c_pend = criterio("pendiente", "Pendiente del terreno (%)")
ruta_tif = raster_pendiente(TMP / "pendiente_pond.tif")
desc.cargar_archivo_local(c_pend, str(ruta_tif))
R.comprobar(
    "P.continuo", "la carga manual de pendiente fija ruta_raster_continuo",
    bool(getattr(c_pend, "ruta_raster_continuo", None)),
    f"ruta_raster_continuo = {getattr(c_pend, 'ruta_raster_continuo', None)!r}")


# ===========================================================================
print("\nArchivos que deben rechazarse con claridad")
# ===========================================================================

basura = TMP / "no_es_una_capa.txt"
basura.write_text("esto no es una capa geográfica\n", encoding="utf-8")
c_mal = criterio("malo", "Archivo inválido")
errores: list[tuple] = []
desc.error.connect(lambda cid, msg: errores.append((cid, msg)))
capa_mal = desc.cargar_archivo_local(c_mal, str(basura))

R.comprobar(
    "E1", "un archivo que no es una capa devuelve None",
    capa_mal is None, f"devolvió {capa_mal!r}")
R.comprobar(
    "E2", "y avisa por la señal de error",
    any(cid == "malo" for cid, _ in errores),
    f"errores = {[m[:60] for _, m in errores][:1]}")
R.comprobar(
    "E3", "un archivo inexistente no revienta",
    desc.cargar_archivo_local(criterio("ausente"), str(TMP / "no_existe.gpkg")) is None)


# ===========================================================================
print("\nShapefile con .prj incoherente cargado a mano")
# ===========================================================================
# El defecto real de localidades_urbanas: coordenadas en metros con un .prj que
# declara grados. La detección debe funcionar igual por la ruta manual.

from evaluacion_rsu.core.analisis import MotorAnalisis

carpeta = TMP / "mentirosa"
capa_m = arnes.shapefile_con_prj_falso(
    ["POLYGON((3040000 800000, 3060000 800000, 3060000 820000, "
     "3040000 820000, 3040000 800000))"],
    "EPSG:6372", carpeta, nombre="loc_urbanas")

c_m = criterio("localidades_urbanas", "Localidades urbanas")
capa_cargada = desc.cargar_archivo_local(c_m, str(carpeta / "loc_urbanas.shp"))
R.comprobar(
    "M1", "el shapefile con .prj incoherente se carga",
    capa_cargada is not None and capa_cargada.isValid())

m = MotorAnalisis(aoi, [c_m], str(TMP / "motor_m"), modo_analisis="dicotomico")
m.progreso.connect(lambda p, msg: None)
crs_ef = m._crs_efectivo(capa_cargada)
R.comprobar(
    "M2", "y el motor corrige su CRS pese a venir por la ruta manual",
    crs_ef.authid() == "EPSG:6372",
    f"declarado={capa_cargada.crs().authid() or 'sin EPSG'} → "
    f"efectivo={crs_ef.authid()}")


ok = R.resumen()
arnes.terminar(0 if ok else 1)
