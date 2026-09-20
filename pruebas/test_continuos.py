# -*- coding: utf-8 -*-
"""
Criterios de superficie continua: exclusión por lejanía y escala de puntaje.

La red vial y la pendiente no se evalúan por traslape. Cada punto del
territorio tiene un valor —su distancia a la vía más cercana, su pendiente— y
el criterio se resuelve comparando ese valor contra un umbral.

La exclusión por lejanía es la que más merece prueba: invierte el sentido de
un buffer. Si se implementara al revés se excluiría exactamente el complemento
de lo correcto, y el análisis terminaría sin error alguno entregando el mapa
opuesto al que el usuario pidió.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import arnes

arnes.iniciar()

from osgeo import gdal, osr
from qgis.core import QgsGeometry

from evaluacion_rsu.core.analisis import MotorAnalisis
from evaluacion_rsu.core.criterios import Criterio, TipoExclusion, TipoScore

R = arnes.Reporte()

# AOI de 0.4° × 0.4° en el centro de Veracruz.
AOI_WKT = "POLYGON((-96.9 19.1, -96.5 19.1, -96.5 19.5, -96.9 19.5, -96.9 19.1))"
aoi = arnes.capa_memoria([AOI_WKT], crs="EPSG:4326", nombre="aoi")


def motor(criterios, carpeta="continuos"):
    m = MotorAnalisis(aoi, criterios, str(arnes.dir_temporal(carpeta)),
                      modo_analisis="dicotomico")
    m.progreso.connect(lambda p, msg: None)
    return m


def crit_vial(umbral_m, wkts=None, rol="excluyente"):
    c = Criterio(id="vialidad", nombre="Red vial (accesibilidad)", descripcion="",
                 es_exclusion=True, tipo_exclusion=TipoExclusion.LEJANIA,
                 umbral_exclusion=umbral_m, unidad_umbral="m",
                 activo=True, rol_ponderado=rol)
    # Una vía vertical que cruza el AOI por el centro.
    c.capa = arnes.capa_memoria(
        wkts or ["LINESTRING(-96.7 19.1, -96.7 19.5)"],
        crs="EPSG:4326", nombre="vialidad", tipo="LineString")
    c.origen_carga = "automatica"
    return c


# ===========================================================================
print("\nExclusión por lejanía: se descarta lo LEJOS, no lo cerca")
# ===========================================================================

m = motor([crit_vial(5000.0)])
excluida, apta = m.ejecutar_fase1()
a = m.resumen_areas

R.comprobar(
    "L1", "produce un balance de áreas",
    bool(a), f"prohibida = {a.get('excluida_km2', 0):,.2f} km²")

# La vía recorre el AOI de sur a norte por el centro. Con 5 km de alcance, la
# franja accesible mide ~10 km de ancho sobre un AOI de ~42 km: queda
# accesible cerca de la cuarta parte y se excluye el resto.
pct = a["pct_excluida"]
R.comprobar(
    "L2", "se excluye la MAYOR parte del área, no la menor",
    pct > 50.0,
    f"prohibida = {pct:.1f} % (una franja de 10 km sobre un AOI de ~42 km "
    f"de ancho deja accesible ~24 %)")

# La comprobación que distingue lejanía de buffer: un punto pegado a la vía
# debe quedar PERMITIDO, y uno en la esquina opuesta PROHIBIDO.
geom_excl = QgsGeometry.fromWkt(excluida.getFeature(1).geometry().asWkt()) \
    if excluida.featureCount() else QgsGeometry()
if geom_excl.isEmpty():
    feats = list(excluida.getFeatures())
    geom_excl = feats[0].geometry() if feats else QgsGeometry()

sobre_via = QgsGeometry.fromWkt("POINT(-96.7 19.3)")
esquina = QgsGeometry.fromWkt("POINT(-96.89 19.49)")

R.comprobar(
    "L3", "un punto SOBRE la vía queda permitido",
    not geom_excl.contains(sobre_via),
    "si estuviera excluido, la lógica sería la de un buffer, no la de lejanía")
R.comprobar(
    "L4", "un punto en la esquina opuesta queda prohibido",
    geom_excl.contains(esquina),
    "a ~20 km de la vía, muy por encima del umbral de 5 km")

# Con un umbral enorme no debe excluirse nada.
m2 = motor([crit_vial(500_000.0)], "lejania_amplia")
m2.ejecutar_fase1()
R.comprobar(
    "L5", "con un umbral mayor que el área, no se excluye nada",
    m2.resumen_areas["pct_excluida"] < 1.0,
    f"prohibida = {m2.resumen_areas['pct_excluida']:.2f} %")

# Umbral menor → más superficie excluida. La relación debe ser monótona.
anteriores = []
for umbral in (2000.0, 5000.0, 15000.0):
    mm = motor([crit_vial(umbral)], f"lejania_{int(umbral)}")
    mm.ejecutar_fase1()
    anteriores.append((umbral, mm.resumen_areas["pct_excluida"]))

R.comprobar(
    "L6", "a mayor umbral, menor superficie excluida",
    anteriores[0][1] > anteriores[1][1] > anteriores[2][1],
    " · ".join(f"{u:,.0f} m → {p:.1f} %" for u, p in anteriores))


# ===========================================================================
print("\nCapa vacía: se omite en lugar de excluirlo todo")
# ===========================================================================
# Sin una sola vía en el entorno, «todo está lejos» es literalmente cierto,
# pero es indistinguible de una capa que no se descargó. Excluir el área
# entera en silencio sería el peor resultado posible.

c_vacio = crit_vial(5000.0, wkts=["LINESTRING(-80.0 25.0, -80.0 25.1)"])
LOG: list[str] = []
m3 = MotorAnalisis(aoi, [c_vacio], str(arnes.dir_temporal("lejania_vacia")),
                   modo_analisis="dicotomico")
m3.progreso.connect(lambda p, msg: LOG.append(msg) if msg else None)
m3.ejecutar_fase1()

R.comprobar(
    "V1", "no se declara toda el área inaccesible",
    m3.resumen_areas["pct_excluida"] < 1.0,
    f"prohibida = {m3.resumen_areas['pct_excluida']:.2f} %")
R.comprobar(
    "V2", "y queda constancia del motivo en el registro",
    any("no se encontró ninguna geometría" in l for l in LOG),
    [l.strip() for l in LOG if "no se encontró" in l][:1])


# ===========================================================================
print("\nUmbral de pendiente configurable")
# ===========================================================================

def raster_pendiente_variable(ruta: Path) -> Path:
    """MDE cuya mitad oriental es plana y la occidental muy empinada."""
    cols = rows = 80
    ds = gdal.GetDriverByName("GTiff").Create(
        str(ruta), cols, rows, 1, gdal.GDT_Float32)
    ds.SetGeoTransform([-96.9, 0.4 / cols, 0.0, 19.5, 0.0, -0.4 / rows])
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(4326)
    ds.SetProjection(srs.ExportToWkt())
    # Elevación que crece con rapidez hacia el oeste y es constante al este.
    x = np.arange(cols, dtype=np.float32)
    perfil = np.where(x < cols / 2, (cols / 2 - x) * 60.0, 0.0)
    ds.GetRasterBand(1).WriteArray(np.tile(perfil, (rows, 1)))
    ds.FlushCache()
    ds = None
    return ruta


TMP_P = arnes.dir_temporal("pendiente_umbral")
dem = raster_pendiente_variable(TMP_P / "dem.tif")

from qgis.core import QgsRasterLayer

resultados = {}
for umbral in (10.0, 60.0):
    c = Criterio(id="pendiente", nombre="Pendiente del terreno (%)",
                 descripcion="", es_exclusion=True,
                 tipo_exclusion=TipoExclusion.UMBRAL_RASTER,
                 umbral_exclusion=umbral, unidad_umbral="%", activo=True)
    c.capa = QgsRasterLayer(str(dem), "dem")
    mm = motor([c], f"pend_{int(umbral)}")
    mm.ejecutar_fase1()
    resultados[umbral] = mm.resumen_areas["pct_excluida"]

R.comprobar(
    "P1", "el umbral de pendiente ya no está fijo en 25 %",
    abs(resultados[10.0] - resultados[60.0]) > 1.0,
    f"umbral 10 % → {resultados[10.0]:.1f} % excluido; "
    f"umbral 60 % → {resultados[60.0]:.1f} %")
R.comprobar(
    "P2", "un umbral más permisivo excluye menos superficie",
    resultados[60.0] < resultados[10.0],
    f"{resultados[60.0]:.1f} % < {resultados[10.0]:.1f} %")


# ===========================================================================
print("\nEscala continua de puntaje")
# ===========================================================================
# El puntaje debe anclarse a los valores que el usuario fija, no al mínimo y
# máximo observados: si depende de los datos, dos municipios distintos no se
# pueden comparar porque en ambos el mejor sitio puntúa 1.00.

TMP_E = arnes.dir_temporal("escala")
ruta_val = TMP_E / "valores.tif"
cols = rows = 40
ds = gdal.GetDriverByName("GTiff").Create(str(ruta_val), cols, rows, 1,
                                          gdal.GDT_Float32)
ds.SetGeoTransform([-96.9, 0.4 / cols, 0.0, 19.5, 0.0, -0.4 / rows])
_srs = osr.SpatialReference(); _srs.ImportFromEPSG(4326)
ds.SetProjection(_srs.ExportToWkt())
# Valores 0, 5, 10, … 195 a lo ancho.
ds.GetRasterBand(1).WriteArray(
    np.tile(np.arange(cols, dtype=np.float32) * 5.0, (rows, 1)))
ds.FlushCache(); ds = None

m_esc = motor([], "escala_motor")
salida = TMP_E / "norm.tif"
m_esc._normalizar_escala(str(ruta_val), str(salida), optimo=0.0, peor=100.0)

dsn = gdal.Open(str(salida))
norm = dsn.GetRasterBand(1).ReadAsArray().astype(float)
dsn = None

R.comprobar(
    "E1", "el valor óptimo puntúa 1.00",
    abs(norm[0, 0] - 1.0) < 1e-6, f"valor 0 → {norm[0, 0]:.4f}")
R.comprobar(
    "E2", "el valor peor puntúa 0.00",
    abs(norm[0, 20] - 0.0) < 1e-6, f"valor 100 → {norm[0, 20]:.4f}")
R.comprobar(
    "E3", "el punto medio puntúa 0.50",
    abs(norm[0, 10] - 0.5) < 1e-6, f"valor 50 → {norm[0, 10]:.4f}")
R.comprobar(
    "E4", "más allá del peor se recorta a 0, no se vuelve negativo",
    float(norm.min()) >= -1e-9,
    f"mínimo observado = {float(norm.min()):.4f} (hay valores hasta 195)")
R.comprobar(
    "E5", "el puntaje nunca supera 1.00",
    float(norm.max()) <= 1.0 + 1e-9, f"máximo = {float(norm.max()):.4f}")

# La escala invertida —óptimo mayor que el peor— debe funcionar sin casos
# especiales: es lo que necesita un criterio que mejora al crecer.
salida_inv = TMP_E / "norm_inv.tif"
m_esc._normalizar_escala(str(ruta_val), str(salida_inv), optimo=100.0, peor=0.0)
dsi = gdal.Open(str(salida_inv))
inv = dsi.GetRasterBand(1).ReadAsArray().astype(float)
dsi = None

R.comprobar(
    "E6", "la escala invertida funciona con la misma fórmula",
    abs(inv[0, 0] - 0.0) < 1e-6 and abs(inv[0, 20] - 1.0) < 1e-6,
    f"valor 0 → {inv[0, 0]:.4f}; valor 100 → {inv[0, 20]:.4f}")

# Una escala degenerada no debe usarse: dividiría entre cero.
c_degen = Criterio(id="x", nombre="x", descripcion="",
                   escala_optimo=50.0, escala_peor=50.0)
R.comprobar(
    "E7", "una escala con ambos extremos iguales se rechaza",
    MotorAnalisis._escala(c_degen) == (None, None),
    f"_escala devolvió {MotorAnalisis._escala(c_degen)}")

c_sin = Criterio(id="y", nombre="y", descripcion="")
R.comprobar(
    "E8", "sin escala definida se recurre a la normalización por defecto",
    MotorAnalisis._escala(c_sin) == (None, None))


# ===========================================================================
print("\nUmbral con respaldo")
# ===========================================================================

c_vacio_u = Criterio(id="z", nombre="z", descripcion="", umbral_exclusion=None)
R.comprobar(
    "U1", "sin umbral definido se usa el valor por defecto",
    MotorAnalisis._umbral(c_vacio_u, 25.0) == 25.0)
c_cero = Criterio(id="z2", nombre="z2", descripcion="", umbral_exclusion=0.0)
R.comprobar(
    "U2", "un umbral de cero no se toma como válido",
    MotorAnalisis._umbral(c_cero, 25.0) == 25.0,
    "un umbral de 0 excluiría todo o nada según el criterio; se ignora")
c_ok = Criterio(id="z3", nombre="z3", descripcion="", umbral_exclusion=12.5)
R.comprobar(
    "U3", "un umbral válido se respeta",
    MotorAnalisis._umbral(c_ok, 25.0) == 12.5)


ok = R.resumen()
arnes.terminar(0 if ok else 1)
