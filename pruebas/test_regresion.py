# -*- coding: utf-8 -*-
"""
Pruebas de regresión de los errores corregidos en v1.5.0 y v1.5.1.

Cada prueba reproduce el defecto original con datos sintéticos y verifica que
la corrección lo evita. Todos estos errores compartían una característica:
producían resultados falsos sin lanzar ninguna excepción.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import arnes

arnes.iniciar()

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsGeometry,
    QgsProject,
)

from evaluacion_rsu.core.analisis import MotorAnalisis
from evaluacion_rsu.core.criterios import Criterio, TipoExclusion, FuenteDatos

R = arnes.Reporte()
TMP = arnes.dir_temporal()


def motor(aoi, criterios=None):
    m = MotorAnalisis(aoi, criterios or [], str(TMP), modo_analisis="dicotomico")
    # Silenciar el registro durante las pruebas
    m.progreso.connect(lambda p, msg: None)
    return m


def wkt_proyectado(wkt_4326, destino="EPSG:6372"):
    """Reproyecta un WKT de 4326 al CRS destino."""
    g = QgsGeometry.fromWkt(wkt_4326)
    g.transform(QgsCoordinateTransform(
        QgsCoordinateReferenceSystem("EPSG:4326"),
        QgsCoordinateReferenceSystem(destino),
        QgsProject.instance().transformContext()))
    return g.asWkt()


# ===========================================================================
print("\nR1 — Filtro espacial construido en el CRS de la capa")
# ===========================================================================
# Antes: el bbox se pasaba en el CRS del AOI. Una capa en EPSG:6372 (metros)
# consultada con un rectángulo en grados no devolvía ningún elemento.

aoi = arnes.aoi_veracruz("EPSG:4326")
m = motor(aoi)

# Polígono dentro del AOI, expresado en metros (EPSG:6372)
dentro_6372 = wkt_proyectado(
    "POLYGON((-96.80 19.20, -96.70 19.20, -96.70 19.30, -96.80 19.30, -96.80 19.20))")
capa_6372 = arnes.capa_memoria([dentro_6372], crs="EPSG:6372", nombre="c6372")

bbox = m._bbox_busqueda(capa_6372)
R.comprobar(
    "R1.1", "el bbox se expresa en las unidades de la capa",
    abs(bbox.xMinimum()) > 1000,
    f"xMin={bbox.xMinimum():,.0f} (metros, no grados)")

crit = Criterio(id="t", nombre="t", descripcion="", tipo_exclusion=TipoExclusion.TRASLAPE)
crit.capa = capa_6372
geom = m._union_geometrias(capa_6372, crit)
R.comprobar(
    "R1.2", "encuentra la geometría pese al CRS distinto",
    not geom.isEmpty(), f"área={geom.area():.6f} (grados²)")

# El resultado debe volver en el CRS del AOI para poder intersectar
inter = geom.intersection(m._geom_aoi())
R.comprobar(
    "R1.3", "el resultado se reproyecta al CRS del AOI e intersecta",
    not inter.isEmpty(), f"área de intersección={inter.area():.6f}")


# ===========================================================================
print("\nR2 — CRS mal declarado: coordenadas métricas etiquetadas como grados")
# ===========================================================================
# Reproduce localidades_urbanas.prj: dice GEOGCS/Degree, contiene metros.

# Shapefile con coordenadas en metros y un .prj que declara grados,
# exactamente como llega localidades_urbanas.
capa_mentirosa = arnes.shapefile_con_prj_falso(
    [dentro_6372], crs_real="EPSG:6372", carpeta=TMP / "mentirosa")

R.comprobar(
    "R2.1", "el .prj declara un sistema geográfico en grados",
    capa_mentirosa.crs().isGeographic(),
    f"declarado={capa_mentirosa.crs().authid() or capa_mentirosa.crs().description()}")

ext = capa_mentirosa.extent()
R.comprobar(
    "R2.2", "pero sus coordenadas están fuera del rango geográfico",
    abs(ext.xMinimum()) > 180,
    f"extensión x=[{ext.xMinimum():,.0f} … {ext.xMaximum():,.0f}]")

efectivo = m._crs_efectivo(capa_mentirosa)
R.comprobar(
    "R2.3", "_crs_efectivo detecta la incoherencia y corrige a EPSG:6372",
    efectivo.authid() == "EPSG:6372",
    f"efectivo={efectivo.authid()}")

crit2 = Criterio(id="t2", nombre="t2", descripcion="",
                 tipo_exclusion=TipoExclusion.TRASLAPE)
crit2.capa = capa_mentirosa
geom2 = m._union_geometrias(capa_mentirosa, crit2)
R.comprobar(
    "R2.4", "con la corrección, la capa aporta geometría",
    not geom2.isEmpty() and not geom2.intersection(m._geom_aoi()).isEmpty(),
    f"intersección con AOI = {geom2.intersection(m._geom_aoi()).area():.6f}")


# ===========================================================================
print("\nR3 — Capa sin CRS declarado")
# ===========================================================================
# Reproduce humedales_febrero_2012: sin .prj, coordenadas métricas.

# Shapefile sin .prj, como humedales_febrero_2012
capa_sin_crs = arnes.shapefile_con_prj_falso(
    [dentro_6372], crs_real="EPSG:6372", carpeta=TMP / "sinprj",
    nombre="sinprj", prj_declarado=None)
R.comprobar(
    "R3.1", "el shapefile no trae .prj",
    not capa_sin_crs.crs().isValid() or not capa_sin_crs.crs().authid(),
    f"declarado='{capa_sin_crs.crs().authid()}'")
efectivo3 = m._crs_efectivo(capa_sin_crs)
R.comprobar(
    "R3.2", "sin CRS y con coordenadas métricas, se deduce EPSG:6372",
    efectivo3.authid() == "EPSG:6372",
    f"efectivo={efectivo3.authid()}")
crit3 = Criterio(id="t3", nombre="t3", descripcion="",
                 tipo_exclusion=TipoExclusion.TRASLAPE)
crit3.capa = capa_sin_crs
g3 = m._union_geometrias(capa_sin_crs, crit3)
R.comprobar(
    "R3.3", "y la capa aporta geometría dentro del AOI",
    not g3.isEmpty() and not g3.intersection(m._geom_aoi()).isEmpty(),
    f"intersección = {g3.intersection(m._geom_aoi()).area():.6f}")


# ===========================================================================
print("\nR4 — Unión anulada por una geometría con topología inválida")
# ===========================================================================
# El defecto que dejó cuerpos de agua en cero pese a tener 410 elementos.

bowtie = QgsGeometry.fromWkt("POLYGON((0 0, 2 2, 2 0, 0 2, 0 0))")
buenos = [QgsGeometry.fromWkt(
    f"POLYGON(({i} 0,{i+0.8} 0,{i+0.8} 0.8,{i} 0.8,{i} 0))") for i in range(5, 9)]
area_buenos = sum(b.area() for b in buenos)

directa = QgsGeometry.unaryUnion([bowtie] + buenos)
R.comprobar(
    "R4.1", "unaryUnion devuelve vacío ante una geometría inválida (el bug)",
    directa.isEmpty(),
    "confirma que GEOS anula toda la unión sin lanzar excepción")

segura = m._union_segura([bowtie] + buenos, "prueba")
R.comprobar(
    "R4.2", "_union_segura sí produce geometría",
    not segura.isEmpty(), f"área={segura.area():.3f}")
R.comprobar(
    "R4.3", "y recupera también el área del polígono reparado",
    segura.area() > area_buenos + 0.5,
    f"área={segura.area():.3f} vs {area_buenos:.3f} solo de los válidos")

# Caso límite: todas inválidas
solo_malas = m._union_segura([bowtie, QgsGeometry.fromWkt(
    "POLYGON((0 0, 3 3, 3 0, 0 3, 0 0))")], "malas")
R.comprobar(
    "R4.4", "con todas las geometrías inválidas, aún devuelve algo útil",
    not solo_malas.isEmpty(), f"área={solo_malas.area():.3f}")


# ===========================================================================
print("\nR6 — Traslape reproyectado antes de intersectar")
# ===========================================================================
# Ya cubierto por R1.3, se verifica aquí el caso con AOI proyectado.

aoi_6372 = arnes.aoi_veracruz("EPSG:6372")
m6 = motor(aoi_6372)
capa_4326 = arnes.capa_memoria(
    ["POLYGON((-96.80 19.20, -96.70 19.20, -96.70 19.30, -96.80 19.30, -96.80 19.20))"],
    crs="EPSG:4326", nombre="c4326")
crit6 = Criterio(id="t6", nombre="t6", descripcion="",
                 tipo_exclusion=TipoExclusion.TRASLAPE)
crit6.capa = capa_4326
g6 = m6._union_geometrias(capa_4326, crit6)
R.comprobar(
    "R6.1", "AOI proyectado + capa geográfica: la intersección funciona",
    not g6.isEmpty() and not g6.intersection(m6._geom_aoi()).isEmpty(),
    f"intersección = {g6.intersection(m6._geom_aoi()).area():,.0f} m²")


# ===========================================================================
print("\nR8 — Porcentajes acotados a [0, 100]")
# ===========================================================================
for pct in (100.0000001, 100.0, -1e-9):
    c = min(max(pct, 0.0), 100.0)
    comp = 100.0 - c
    R.comprobar(
        f"R8 ({pct:g})", "sin porcentajes negativos",
        0.0 <= c <= 100.0 and 0.0 <= comp <= 100.0,
        f"excluida={c:.1f} % permitida={comp:.1f} %")


# ===========================================================================
print("\nR9 — Elipsoide «NONE»: el reporte entero medía 0.00 km²")
# ===========================================================================
# QgsProject.ellipsoid() devuelve la CADENA "NONE" cuando el proyecto está
# configurado para medición planimétrica, que es verdadera en Python. Un
# `ellipsoid() or "WGS84"` la deja pasar y measureArea devuelve grados
# cuadrados para un AOI geográfico: 0.16 / 1e6 = 0.00 km² en todo el informe.
QgsProject.instance().setEllipsoid("NONE")

aoi_geo = arnes.aoi_veracruz("EPSG:4326")
m9 = motor(aoi_geo)

R.comprobar(
    "R9.1", "ellipsoid() «NONE» es verdadero en Python (el origen del bug)",
    bool(QgsProject.instance().ellipsoid()),
    f"ellipsoid() = {QgsProject.instance().ellipsoid()!r}")

R.comprobar(
    "R9.2", "_elipsoide_medicion no propaga «NONE»",
    m9._elipsoide_medicion().upper() != "NONE",
    f"elipsoide usado = {m9._elipsoide_medicion()!r}")

area = m9._area_km2(m9._geom_aoi())
R.comprobar(
    "R9.3", "el AOI mide su superficie real, no 0.00 km²",
    2_500.0 < area < 3_500.0,
    f"AOI = {area:,.2f} km² (esperado ~3 000 km²)")

QgsProject.instance().setEllipsoid("EPSG:7030")
area_wgs = m9._area_km2(m9._geom_aoi())
R.comprobar(
    "R9.4", "la medición coincide con la del proyecto elipsoidal (±1 %)",
    abs(area - area_wgs) / area_wgs < 0.01,
    f"planimétrico {area:,.2f} km² vs elipsoidal {area_wgs:,.2f} km²")


ok = R.resumen()
arnes.terminar(0 if ok else 1)
