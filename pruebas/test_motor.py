# -*- coding: utf-8 -*-
"""
Prueba de extremo a extremo del motor de análisis, con geometría de control.

Se construye un área de interés cuadrada cuya superficie se conoce y criterios
sintéticos que excluyen fracciones exactas. Así el balance de áreas se puede
contrastar contra la aritmética esperada, no solo contra sí mismo.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import arnes

arnes.iniciar()

from qgis.core import QgsGeometry

from evaluacion_rsu.core.analisis import MotorAnalisis
from evaluacion_rsu.core.criterios import Criterio, TipoExclusion

R = arnes.Reporte()
TMP = arnes.dir_temporal("motor")

LOG: list[str] = []


def criterio(cid, nombre, wkts, crs="EPSG:4326", tipo=TipoExclusion.TRASLAPE,
             buffer_m=0.0):
    c = Criterio(id=cid, nombre=nombre, descripcion="", tipo_exclusion=tipo,
                 buffer_m=buffer_m, es_exclusion=True, activo=True)
    c.capa = arnes.capa_memoria(wkts, crs=crs, nombre=nombre)
    c.origen_carga = "automatica"
    return c


# ===========================================================================
print("\nBalance de áreas con geometría de control")
# ===========================================================================
# AOI: cuadrado de 0.4° × 0.4° en el centro de Veracruz.
AOI_WKT = ("POLYGON((-96.9 19.1, -96.5 19.1, -96.5 19.5, -96.9 19.5, -96.9 19.1))")
aoi = arnes.capa_memoria([AOI_WKT], crs="EPSG:4326", nombre="aoi")

# Criterio A: cubre exactamente la cuarta parte inferior izquierda
cuarto = "POLYGON((-96.9 19.1, -96.7 19.1, -96.7 19.3, -96.9 19.3, -96.9 19.1))"
# Criterio B: cubre la cuarta parte superior derecha, sin traslape con A
otro_cuarto = "POLYGON((-96.7 19.3, -96.5 19.3, -96.5 19.5, -96.7 19.5, -96.7 19.3))"
# Criterio C: mitad dentro de A, mitad fuera. Comprueba que la unión suma el
# área nueva sin duplicar la que ya aportaba A.
#   dentro de A: x[-96.8,-96.7] y[19.1,19.2] = 0.01 deg²  (ya cubierto)
#   fuera de A:  x[-96.7,-96.6] y[19.1,19.2] = 0.01 deg²  (aporta)
mitad_de_a = "POLYGON((-96.8 19.1, -96.6 19.1, -96.6 19.2, -96.8 19.2, -96.8 19.1))"

criterios = [
    criterio("a", "Cuarto inferior izquierdo", [cuarto]),
    criterio("b", "Cuarto superior derecho", [otro_cuarto]),
    criterio("c", "Franja traslapada", [mitad_de_a]),
]

motor = MotorAnalisis(aoi, criterios, str(TMP), modo_analisis="dicotomico")
motor.progreso.connect(lambda p, msg: LOG.append(msg) if msg else None)
excluida, apta = motor.ejecutar_fase1()

a = motor.resumen_areas
R.comprobar(
    "M1", "el motor produce un balance de áreas",
    bool(a), f"claves={sorted(a.keys())}")

R.comprobar(
    "M2", "área prohibida + permitida = área de interés (±0.5 %)",
    abs((a["excluida_km2"] + a["apta_km2"]) - a["aoi_km2"]) / a["aoi_km2"] < 0.005,
    f"{a['excluida_km2']:,.2f} + {a['apta_km2']:,.2f} = "
    f"{a['excluida_km2'] + a['apta_km2']:,.2f} vs AOI {a['aoi_km2']:,.2f} km²")

# A y B cubren 1/4 del AOI cada uno (0.04 deg² de 0.16). C aporta 0.01 deg²
# nuevos, ya que su otra mitad cae dentro de A. Total: 0.09/0.16 = 56.25 %.
pct = a["pct_excluida"]
R.comprobar(
    "M3", "la unión suma el área nueva sin duplicar la traslapada",
    abs(pct - 56.25) < 1.0,
    f"prohibida = {pct:.2f} % (esperado 56.25 % = dos cuartos disjuntos "
    f"+ la mitad de C que sobresale)")

R.comprobar(
    "M4", "los porcentajes están acotados",
    0 <= a["pct_excluida"] <= 100 and 0 <= a["pct_apta"] <= 100,
    f"excluida={a['pct_excluida']:.2f} % permitida={a['pct_apta']:.2f} %")

suma_aportes = sum(d["area_km2"] for d in motor.detalle_criterios)
R.comprobar(
    "M5", "la suma de aportes es mayor o igual al total prohibido",
    suma_aportes >= a["excluida_km2"] - 0.01,
    f"suma de aportes = {suma_aportes:,.2f} km² ≥ "
    f"total prohibido {a['excluida_km2']:,.2f} km²")

R.comprobar(
    "M6", "ningún aporte individual excede el área de interés",
    all(d["area_km2"] <= a["aoi_km2"] + 0.01 for d in motor.detalle_criterios),
    f"máximo aporte = {max(d['area_km2'] for d in motor.detalle_criterios):,.2f} km²")

R.comprobar(
    "M7", "se generan las capas de salida",
    excluida is not None and apta is not None and excluida.isValid() and apta.isValid(),
    f"excluida válida={excluida.isValid() if excluida else None}, "
    f"apta válida={apta.isValid() if apta else None}")

R.comprobar(
    "M8", "hay un registro de detalle por criterio",
    len(motor.detalle_criterios) == 3,
    f"{len(motor.detalle_criterios)} registros")


# ===========================================================================
print("\nCriterio de buffer con distancia métrica")
# ===========================================================================
# Un punto en el centro del AOI con buffer de 5 000 m debe excluir
# aproximadamente π·5² = 78.54 km².
TMP2 = arnes.dir_temporal("buffer")
punto = criterio("p", "Punto con buffer", ["POINT(-96.7 19.3)"],
                 tipo=TipoExclusion.BUFFER, buffer_m=5000.0)
punto.capa = arnes.capa_memoria(["POINT(-96.7 19.3)"], crs="EPSG:4326",
                                nombre="punto", tipo="Point")

motor2 = MotorAnalisis(aoi, [punto], str(TMP2), modo_analisis="dicotomico")
motor2.progreso.connect(lambda p, msg: None)
motor2.ejecutar_fase1()
a2 = motor2.resumen_areas
esperado = 3.14159 * 25  # π r² con r = 5 km

R.comprobar(
    "B1", "el buffer de 5 000 m produce el área de un círculo de 5 km",
    abs(a2["excluida_km2"] - esperado) / esperado < 0.02,
    f"{a2['excluida_km2']:,.2f} km² vs π·5² = {esperado:,.2f} km² "
    f"({abs(a2['excluida_km2'] - esperado) / esperado * 100:.2f} % de diferencia)")


# ===========================================================================
print("\nInvariancia frente al CRS del área de interés")
# ===========================================================================
# El mismo análisis con el AOI en otro CRS debe dar la misma superficie.
resultados = {}
for crs in ("EPSG:4326", "EPSG:6372", "EPSG:32614"):
    aoi_n = arnes.aoi_veracruz(crs)
    crits = [criterio("a", "Traslape", [cuarto])]
    mm = MotorAnalisis(aoi_n, crits, str(arnes.dir_temporal("crs")),
                       modo_analisis="dicotomico")
    mm.progreso.connect(lambda p, msg: None)
    mm.ejecutar_fase1()
    resultados[crs] = mm.resumen_areas

base = resultados["EPSG:4326"]["aoi_km2"]
for crs, res in resultados.items():
    R.comprobar(
        f"C[{crs}]", "el área de interés mide lo mismo en cualquier CRS",
        abs(res["aoi_km2"] - base) / base < 0.01,
        f"AOI = {res['aoi_km2']:,.2f} km² (referencia {base:,.2f})")

pcts = [r["pct_excluida"] for r in resultados.values()]
R.comprobar(
    "C.pct", "el porcentaje excluido es estable entre CRS",
    max(pcts) - min(pcts) < 1.0,
    f"porcentajes = {', '.join(f'{p:.2f} %' for p in pcts)}")


# ===========================================================================
print("\nContenido del registro")
# ===========================================================================
texto = "\n".join(LOG)
R.comprobar(
    "L1", "el registro imprime la versión del plugin",
    "EvaluaciónRSU v" in texto,
    [l for l in LOG if "EvaluaciónRSU" in l][:1])
R.comprobar(
    "L2", "el registro reporta el CRS de cada capa",
    "CRS de la capa" in texto)
R.comprobar(
    "L3", "el registro incluye el balance de áreas",
    "Balance de áreas" in texto)
R.comprobar(
    "L4", "no queda rastro del wording «Fase 1» / «Fase 2»",
    "Fase 1" not in texto and "Fase 2" not in texto)


ok = R.resumen()
arnes.terminar(0 if ok else 1)
