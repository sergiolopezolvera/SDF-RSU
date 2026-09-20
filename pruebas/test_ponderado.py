# -*- coding: utf-8 -*-
"""
Pruebas del modo ponderado — la ruta que ``ejecutar_fase2`` recorre.

Hasta ahora el conjunto de pruebas corría todo en modo dicotómico, de modo que
la rasterización, la normalización, la suma ponderada y el recorte final nunca
se habían ejecutado fuera de la interfaz.

La aptitud se contrasta contra aritmética de numpy calculada aparte: no basta
con que el ráster exista, los valores tienen que ser los que el peso dicta.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import arnes

arnes.iniciar()

from osgeo import gdal
from qgis.core import QgsRasterLayer

from evaluacion_rsu.core.analisis import MotorAnalisis
from evaluacion_rsu.core.criterios import Criterio, TipoExclusion, TipoScore

R = arnes.Reporte()

AOI_WKT = "POLYGON((-96.9 19.1, -96.5 19.1, -96.5 19.5, -96.9 19.5, -96.9 19.1))"


def crit_pond(cid, nombre, wkts, peso, score=TipoScore.MAYOR_ES_MEJOR,
              tipo="Point", crs="EPSG:4326"):
    """Criterio que solo aporta puntaje, sin excluir."""
    c = Criterio(id=cid, nombre=nombre, descripcion="",
                 es_exclusion=False, es_ponderacion=True,
                 tipo_score=score, peso=peso,
                 rol_ponderado="ponderado", activo=True)
    c.capa = arnes.capa_memoria(wkts, crs=crs, nombre=nombre, tipo=tipo)
    c.origen_carga = "automatica"
    return c


def crit_excl(cid, nombre, wkts, crs="EPSG:4326"):
    c = Criterio(id=cid, nombre=nombre, descripcion="",
                 tipo_exclusion=TipoExclusion.TRASLAPE,
                 es_exclusion=True, activo=True,
                 rol_ponderado="excluyente")
    c.capa = arnes.capa_memoria(wkts, crs=crs, nombre=nombre)
    c.origen_carga = "automatica"
    return c


def leer(ruta):
    """Devuelve (array, nodata) de la banda 1."""
    ds = gdal.Open(str(ruta))
    banda = ds.GetRasterBand(1)
    datos = banda.ReadAsArray().astype(np.float32)
    nodata = banda.GetNoDataValue()
    ds = None
    return datos, nodata


def validos(datos, nodata):
    m = np.isfinite(datos)
    if nodata is not None:
        m &= (datos != float(nodata))
    return datos[m]


# ===========================================================================
print("\nSalidas tempranas: cuándo NO debe calcular aptitud")
# ===========================================================================

aoi = arnes.capa_memoria([AOI_WKT], crs="EPSG:4326", nombre="aoi")

# Sin ningún criterio con rol ponderado, fase2 debe rendirse limpiamente.
TMP_V = arnes.dir_temporal("pond_vacio")
m_v = MotorAnalisis(aoi, [crit_excl("e", "Excluyente",
                                    ["POLYGON((-96.9 19.1, -96.8 19.1, "
                                     "-96.8 19.2, -96.9 19.2, -96.9 19.1))"])],
                    str(TMP_V), modo_analisis="ponderado")
m_v.progreso.connect(lambda p, msg: None)
_, apta_v = m_v.ejecutar_fase1()
emitido = {"fase2": "sin emitir"}
m_v.fase2_lista.connect(lambda r: emitido.update(fase2=r))
r_v = m_v.ejecutar_fase2(apta_v)

R.comprobar(
    "P1.1", "sin criterios ponderados devuelve None en lugar de fallar",
    r_v is None, f"devolvió {r_v!r}")
R.comprobar(
    "P1.2", "y aun así emite fase2_lista para que la interfaz no se quede colgada",
    emitido["fase2"] is None, f"emitido = {emitido['fase2']!r}")

# Pesos que no suman 100: debe rechazar antes de rasterizar nada.
TMP_P = arnes.dir_temporal("pond_pesos")
c_mal_1 = crit_pond("x1", "Peso 30", ["POINT(-96.8 19.2)"], 30.0)
c_mal_2 = crit_pond("x2", "Peso 30", ["POINT(-96.6 19.4)"], 30.0)
m_p = MotorAnalisis(aoi, [c_mal_1, c_mal_2], str(TMP_P), modo_analisis="ponderado")
m_p.progreso.connect(lambda p, msg: None)
errores: list[str] = []
m_p.error.connect(errores.append)
_, apta_p = m_p.ejecutar_fase1()
r_p = m_p.ejecutar_fase2(apta_p)

R.comprobar(
    "P2.1", "pesos que no suman 100 % se rechazan",
    r_p is None, f"devolvió {r_p!r}")
R.comprobar(
    "P2.2", "y el mensaje de error dice cuánto suman",
    any("60" in e for e in errores), f"errores = {errores}")
R.comprobar(
    "P2.3", "no deja rásters a medio escribir tras el rechazo",
    not (Path(TMP_P) / "resultados_rsu" / "aptitud_ponderada.tif").exists())


# ===========================================================================
print("\nCamino completo: rasterización, normalización y suma ponderada")
# ===========================================================================

TMP = arnes.dir_temporal("pond_ok")
SAL = TMP / "resultados_rsu"   # el motor escribe en este subdirectorio

# Dos criterios de distancia en esquinas opuestas, con pesos desiguales para
# que un intercambio de pesos produzca un ráster distinto.
# El punto se coloca holgadamente dentro del área apta: sobre la línea de
# corte (y = 19.15) la rasterización no quema ninguna celda.
c1 = crit_pond("cerca", "Cercanía a vía", ["POINT(-96.85 19.30)"], 70.0,
               score=TipoScore.MENOR_ES_MEJOR)
c2 = crit_pond("lejos", "Lejanía a poblado", ["POINT(-96.55 19.45)"], 30.0,
               score=TipoScore.MAYOR_ES_MEJOR)
# La exclusión se pone en una ESQUINA, no en una franja de ancho completo:
# así el área apta no es un rectángulo y su bbox sigue siendo el AOI entero,
# que es la única forma de comprobar que el cutline recorta de verdad.
excl = crit_excl("z", "Esquina prohibida",
                 ["POLYGON((-96.9 19.1, -96.75 19.1, -96.75 19.25, "
                  "-96.9 19.25, -96.9 19.1))"])

motor = MotorAnalisis(aoi, [excl, c1, c2], str(TMP), modo_analisis="ponderado")
LOG: list[str] = []
motor.progreso.connect(lambda p, msg: LOG.append(msg) if msg else None)
_, apta = motor.ejecutar_fase1()
raster = motor.ejecutar_fase2(apta)

R.comprobar(
    "P3.1", "produce una capa ráster de aptitud",
    isinstance(raster, QgsRasterLayer) and raster.isValid(),
    f"tipo={type(raster).__name__}, "
    f"válida={raster.isValid() if raster is not None else None}")

R.comprobar(
    "P3.2", "escribe los dos archivos esperados en el directorio de salida",
    (SAL / "aptitud_ponderada.tif").exists() and (SAL / "aptitud_final.tif").exists(),
    f"aptitud_ponderada.tif={(SAL / 'aptitud_ponderada.tif').exists()}, "
    f"aptitud_final.tif={(SAL / 'aptitud_final.tif').exists()}")

datos, nd = leer(SAL / "aptitud_ponderada.tif")
v = validos(datos, nd)

R.comprobar(
    "P3.3", "el ráster tiene celdas válidas",
    v.size > 0, f"{v.size:,} celdas válidas de {datos.size:,}")

R.comprobar(
    "P3.4", "la aptitud cae en [0, 1] — los pesos suman 1 sobre insumos [0,1]",
    v.size > 0 and float(v.min()) >= -1e-6 and float(v.max()) <= 1.0 + 1e-6,
    f"rango observado = [{float(v.min()):.4f}, {float(v.max()):.4f}]")

R.comprobar(
    "P3.5", "la aptitud varía en el territorio (no es una constante)",
    v.size > 0 and float(v.max()) - float(v.min()) > 0.1,
    f"amplitud = {float(v.max()) - float(v.min()):.4f}")


# ===========================================================================
print("\nAritmética de la suma ponderada, contrastada aparte")
# ===========================================================================
# Se recalcula 0.70·n1 + 0.30·n2 a partir de los rásters normalizados que el
# motor dejó en disco, y se compara celda por celda con su resultado.

n1, nd1 = leer(SAL / "pond_cerca_norm.tif")
n2, nd2 = leer(SAL / "pond_lejos_norm.tif")

m1 = np.isfinite(n1) & (n1 != float(nd1)) if nd1 is not None else np.isfinite(n1)
m2 = np.isfinite(n2) & (n2 != float(nd2)) if nd2 is not None else np.isfinite(n2)
esperado = np.where(m1, n1 * 0.70, 0.0) + np.where(m2, n2 * 0.30, 0.0)
mask = m1 | m2

dif = np.abs(esperado[mask] - datos[mask])
R.comprobar(
    "P4.1", "cada celda es exactamente 0.70·c1 + 0.30·c2",
    dif.size > 0 and float(dif.max()) < 1e-5,
    f"diferencia máxima = {float(dif.max()):.3e} sobre {dif.size:,} celdas")

R.comprobar(
    "P4.2", "los insumos normalizados están en [0, 1]",
    float(validos(n1, nd1).min()) >= -1e-6 and float(validos(n1, nd1).max()) <= 1 + 1e-6
    and float(validos(n2, nd2).min()) >= -1e-6 and float(validos(n2, nd2).max()) <= 1 + 1e-6,
    f"c1=[{validos(n1, nd1).min():.3f}, {validos(n1, nd1).max():.3f}]  "
    f"c2=[{validos(n2, nd2).min():.3f}, {validos(n2, nd2).max():.3f}]")

# MENOR_ES_MEJOR debe invertir: la celda más cercana al punto puntúa más alto.
ds1 = gdal.Open(str(SAL / "pond_cerca_dist.tif"))
dist = ds1.GetRasterBand(1).ReadAsArray().astype(np.float32)
ds1 = None
finita = np.isfinite(dist) & (dist != -9999) & m1
i_cerca = np.argmin(np.where(finita, dist, np.inf))
i_lejos = np.argmax(np.where(finita, dist, -np.inf))
R.comprobar(
    "P4.3", "MENOR_ES_MEJOR invierte la escala: más cerca puntúa más alto",
    n1.flat[i_cerca] > n1.flat[i_lejos],
    f"a {dist.flat[i_cerca]:.4f}° puntúa {n1.flat[i_cerca]:.3f}; "
    f"a {dist.flat[i_lejos]:.4f}° puntúa {n1.flat[i_lejos]:.3f}")


# ===========================================================================
print("\nRecorte al área permitida")
# ===========================================================================
# aptitud_final.tif se recorta con la capa apta como cutline: la franja
# prohibida del sur no debe conservar valores.

fin, ndf = leer(SAL / "aptitud_final.tif")
vf = validos(fin, ndf)
R.comprobar(
    "P5.1", "el ráster recortado conserva celdas válidas",
    vf.size > 0, f"{vf.size:,} celdas válidas")
R.comprobar(
    "P5.2", "el recorte descarta celdas: cubre menos que el ráster sin recortar",
    vf.size < v.size,
    f"recortado {vf.size:,} celdas vs sin recortar {v.size:,}")
R.comprobar(
    "P5.3", "los valores conservados siguen en [0, 1]",
    float(vf.min()) >= -1e-6 and float(vf.max()) <= 1.0 + 1e-6,
    f"rango = [{float(vf.min()):.4f}, {float(vf.max()):.4f}]")


# ===========================================================================
print("\nInvariancia frente al CRS del área de interés")
# ===========================================================================
# El mismo análisis con el AOI proyectado debe producir un ráster comparable.
# La resolución se fija en metros, así que la conversión a unidades de mapa
# tiene que atender el CRS: dividir siempre entre 111 320 da 0.9 mm de celda
# cuando el AOI ya está en metros, y el ráster resultante es inmanejable.

TMP_6372 = arnes.dir_temporal("pond_6372")
aoi_6372 = arnes.aoi_veracruz("EPSG:6372")
c1b = crit_pond("cerca", "Cercanía", ["POINT(3040000 810000)"], 70.0,
                score=TipoScore.MENOR_ES_MEJOR, crs="EPSG:6372")
c2b = crit_pond("lejos", "Lejanía", ["POINT(3070000 845000)"], 30.0,
                score=TipoScore.MAYOR_ES_MEJOR, crs="EPSG:6372")

m6 = MotorAnalisis(aoi_6372, [c1b, c2b], str(TMP_6372), modo_analisis="ponderado")
m6.progreso.connect(lambda p, msg: None)
_, apta6 = m6.ejecutar_fase1()

ext6 = apta6.extent()
res6 = m6._resolucion_en_unidades_mapa(m6.RESOLUCION_M, aoi_6372.crs())
cols6 = max(1, int(ext6.width() / res6))
rows6 = max(1, int(ext6.height() / res6))
celdas = cols6 * rows6

R.comprobar(
    "P6.1", "la resolución se traduce a las unidades del AOI, no siempre a grados",
    celdas < 50_000_000,
    f"AOI de {ext6.width():,.0f} × {ext6.height():,.0f} unidades "
    f"con celda de {res6:g} → {cols6:,} × {rows6:,} = {celdas:,} celdas")

R.comprobar(
    "P6.2", "la celda mide ~100 m en las unidades del AOI proyectado",
    50.0 < res6 < 200.0,
    f"celda = {res6:g} unidades de mapa (se esperan ~100 m)")

# La comprobación decisiva: la fase 2 completa debe TERMINAR con el AOI
# proyectado. Con la resolución mal convertida no fallaba con un error, se
# quedaba intentando asignar un ráster de billones de celdas.
import time
t0 = time.time()
r6 = m6.ejecutar_fase2(apta6)
transcurrido = time.time() - t0

R.comprobar(
    "P6.3", "la ponderación completa termina con el AOI proyectado",
    isinstance(r6, QgsRasterLayer) and r6.isValid(),
    f"válida={r6.isValid() if r6 is not None else None} "
    f"en {transcurrido:.1f} s")

d6, nd6 = leer(TMP_6372 / "resultados_rsu" / "aptitud_ponderada.tif")
v6 = validos(d6, nd6)
R.comprobar(
    "P6.4", "y produce aptitud en [0, 1] igual que con el AOI geográfico",
    v6.size > 0 and float(v6.min()) >= -1e-6 and float(v6.max()) <= 1.0 + 1e-6,
    f"{v6.size:,} celdas, rango = [{float(v6.min()):.4f}, {float(v6.max()):.4f}]")


# ===========================================================================
print("\nCriterio ponderado que no cubre el área: el peso perdido se avisa")
# ===========================================================================
# Si la capa de un criterio queda fuera del área permitida, su ráster sale
# entero como nodata y su peso se evapora. El techo de aptitud deja de ser 1.00
# y el mapa miente sobre su propia escala. Debe quedar constancia en el log.

TMP_F = arnes.dir_temporal("pond_fuera")
SAL_F = TMP_F / "resultados_rsu"
dentro = crit_pond("dentro", "Dentro", ["POINT(-96.7 19.3)"], 40.0)
fuera = crit_pond("fuera", "Fuera del AOI", ["POINT(-90.0 25.0)"], 60.0)

m_f = MotorAnalisis(aoi, [dentro, fuera], str(TMP_F), modo_analisis="ponderado")
LOG_F: list[str] = []
m_f.progreso.connect(lambda p, msg: LOG_F.append(msg) if msg else None)
_, apta_f = m_f.ejecutar_fase1()
m_f.ejecutar_fase2(apta_f)
texto_f = "\n".join(LOG_F)

R.comprobar(
    "P7.1", "avisa qué criterio no aportó ninguna celda",
    "no aportó ninguna celda válida" in texto_f,
    [l.strip() for l in LOG_F if "no aportó" in l][:1])
R.comprobar(
    "P7.2", "avisa que el máximo del mapa ya no es 1.00",
    "el máximo posible del mapa es" in texto_f,
    [l.strip() for l in LOG_F if "máximo posible" in l][:1])

d_f, nd_f = leer(SAL_F / "aptitud_ponderada.tif")
v_f = validos(d_f, nd_f)
R.comprobar(
    "P7.3", "y el techo observado coincide con el peso que sí aportó",
    v_f.size > 0 and abs(float(v_f.max()) - 0.40) < 0.02,
    f"máximo observado = {float(v_f.max()):.4f} (el criterio válido pesa 40 %)")


ok = R.resumen()
arnes.terminar(0 if ok else 1)
