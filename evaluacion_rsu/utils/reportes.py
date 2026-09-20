# -*- coding: utf-8 -*-
# EvaluaciónRSU — Plugin QGIS para la selección de sitios de disposición final de RSU
# Copyright (C) 2024-2025  Sergio López Olvera <lopezolverasergio@gmail.com>
# SPDX-License-Identifier: GPL-3.0-or-later
# Repository: https://github.com/sergio-lopez-olvera/evaluacion-rsu

"""
Generación de reportes HTML del análisis multicriterio RSU.

Produce un documento HTML autocontenido con:
  - Encabezado normativo (NOM-083-SEMARNAT-2003)
  - Tabla de criterios de exclusión activos (con buffers)
  - Tabla de criterios de ponderación activos (con pesos)
  - Resumen de resultados (área excluida y área apta)
  - Fecha de generación y versión del plugin
"""

from __future__ import annotations

import datetime
from typing import Optional

try:
    from qgis.core import QgsDistanceArea, QgsProject
    _QGIS_DISPONIBLE = True
except ImportError:
    _QGIS_DISPONIBLE = False


# ---------------------------------------------------------------------------
# Función principal
# ---------------------------------------------------------------------------

def generar_reporte_html(
    ruta_salida: str,
    criterios: list,
    capas_resultado: Optional[dict] = None,
) -> str:
    """Genera un reporte HTML del análisis y lo escribe en ruta_salida.

    Parameters
    ----------
    ruta_salida:
        Ruta completa del archivo .html de salida.
    criterios:
        Lista de objetos Criterio utilizados en el análisis.
    capas_resultado:
        Dict opcional con claves 'excluida', 'apta', 'aptitud'
        apuntando a QgsVectorLayer / QgsRasterLayer.

    Returns
    -------
    str — Ruta del archivo generado.
    """
    capas_resultado = capas_resultado or {}
    fecha = datetime.datetime.now().strftime("%d de %B de %Y, %H:%M h")

    html = _construir_html(criterios, capas_resultado, fecha)

    with open(ruta_salida, "w", encoding="utf-8") as f:
        f.write(html)

    return ruta_salida


# ---------------------------------------------------------------------------
# Etiquetas de fuente de datos
# ---------------------------------------------------------------------------

def _fuente_label(fuente) -> str:
    etiquetas = {
        "inegi_dl":   "INEGI (descarga directa)",
        "conanp":     "CONANP",
        "conabio":    "CONABIO",
        "osm":        "OpenStreetMap (Overpass)",
        "copernicus": "Copernicus DEM GLO-30",
        "manual":     "Carga manual",
    }
    return etiquetas.get(fuente.value if hasattr(fuente, "value") else str(fuente), "—")


# ---------------------------------------------------------------------------
# Construcción del documento
# ---------------------------------------------------------------------------

def _construir_html(criterios: list, capas_resultado: dict,
                    fecha: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Evaluación RSU — NOM-083-SEMARNAT-2003</title>
<style>
{_css()}
</style>
</head>
<body>
{_encabezado(fecha)}
{_tabla_exclusion(criterios)}
{_tabla_ponderacion(criterios)}
{_seccion_resumen(capas_resultado)}
{_pie()}
</body>
</html>"""


def _css() -> str:
    return """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
    font-family: Arial, Helvetica, sans-serif;
    font-size: 13px;
    background: #FAFAFA;
    color: #212121;
}
header {
    background: #2E7D32;
    color: white;
    padding: 20px 36px 16px;
}
header h1 { font-size: 19px; margin-bottom: 4px; }
header p  { font-size: 11px; opacity: 0.85; margin: 2px 0; }
header p.nota-alcance { font-size: 10px; opacity: 0.7; font-style: italic; }
.aut  { color: #2E7D32; font-weight: bold; font-size: 10px; }
.man  { color: #1565C0; font-weight: bold; font-size: 10px; }
.ruta { color: #78868F; font-size: 9px; word-break: break-all; }
section { margin: 24px 36px; }
h2 {
    font-size: 14px;
    color: #1B5E20;
    border-bottom: 2px solid #A5D6A7;
    padding-bottom: 4px;
    margin-bottom: 12px;
}
table {
    border-collapse: collapse;
    width: 100%;
    font-size: 11.5px;
}
th {
    background: #E8F5E9;
    color: #1B5E20;
    text-align: left;
    padding: 6px 10px;
    border: 1px solid #C8E6C9;
    white-space: nowrap;
}
td {
    padding: 5px 10px;
    border: 1px solid #E0E0E0;
    vertical-align: top;
}
tr:nth-child(even) td { background: #FAFFFE; }
.fuente { color: #666; font-size: 10px; }
.ok  { color: #2E7D32; font-weight: bold; }
.nok { color: #9E9E9E; }
.warn { color: #455A64; font-weight: bold; }
.resumen-box {
    background: #E8F5E9;
    border-left: 5px solid #2E7D32;
    padding: 14px 18px;
    border-radius: 2px;
    line-height: 1.8;
}
footer {
    margin-top: 32px;
    padding: 12px 36px;
    background: #EEEEEE;
    font-size: 10.5px;
    color: #757575;
}
"""


def _encabezado(fecha: str) -> str:
    return f"""
<header>
  <h1>Evaluación multicriterio — Selección de sitios de disposición final de RSU</h1>
  <p>NOM-083-SEMARNAT-2003, § 6.1 &nbsp;&bull;&nbsp; Generado: {fecha}</p>
  <p class="nota-alcance">Las restricciones del § 6.1 aplican a cualquier sitio de
  disposición final, sea tipo A, B, C o D.</p>
</header>
"""


def _origen_label(criterio) -> str:
    """Etiqueta de procedencia del dato: descarga automática o archivo del usuario."""
    origen = getattr(criterio, "origen_carga", "")
    if origen == "automatica":
        return "<span class='aut'>Automática</span>"
    if origen == "manual":
        ruta = getattr(criterio, "ruta_dato", None)
        etiqueta = "<span class='man'>Manual</span>"
        if ruta:
            etiqueta += f"<br><span class='ruta'>{ruta}</span>"
        return etiqueta
    return "<span class='nok'>—</span>"


def _tabla_exclusion(criterios: list) -> str:
    filas = []
    for c in criterios:
        if not c.es_exclusion or not c.activo:
            continue
        tipo = "Buffer" if c.tipo_exclusion.value == "buffer" else "Traslape"
        buf = (f"{c.buffer_m:.0f} m"
               if c.tipo_exclusion.value == "buffer" and c.buffer_m > 0 else "—")
        estado = "<span class='ok'>Cargado</span>" if c.capa is not None \
                 else "<span class='nok'>Sin datos</span>"
        fuente = f"<span class='fuente'>{_fuente_label(c.fuente)}</span>"
        filas.append(
            f"<tr><td>{c.nombre}</td><td>{fuente}</td>"
            f"<td>{_origen_label(c)}</td>"
            f"<td>{tipo}</td><td>{buf}</td>"
            f"<td>{c.referencia_normativa or '—'}</td><td>{estado}</td></tr>"
        )

    if not filas:
        contenido = "<tr><td colspan='7' class='nok'>Sin criterios de exclusión activos.</td></tr>"
    else:
        contenido = "\n".join(filas)

    return f"""
<section>
  <h2>Criterios de exclusión</h2>
  <table>
    <tr>
      <th>Criterio</th><th>Fuente</th><th>Carga</th><th>Tipo</th>
      <th>Buffer / Parámetro</th><th>Referencia normativa</th><th>Estado</th>
    </tr>
    {contenido}
  </table>
</section>"""


def _tabla_ponderacion(criterios: list) -> str:
    filas = []
    for c in criterios:
        if not c.es_ponderacion or not c.activo or c.peso <= 0:
            continue
        estado = "<span class='ok'>Cargado</span>" if c.capa is not None \
                 else "<span class='warn'>Sin datos</span>"
        fuente = f"<span class='fuente'>{_fuente_label(c.fuente)}</span>"
        filas.append(
            f"<tr><td>{c.nombre}</td><td>{fuente}</td>"
            f"<td style='text-align:right'><strong>{c.peso:.1f} %</strong></td>"
            f"<td>{estado}</td></tr>"
        )

    if not filas:
        return ""   # No hubo análisis de ponderación

    suma = sum(c.peso for c in criterios if c.es_ponderacion and c.activo and c.peso > 0)
    suma_td = (
        f"<td style='text-align:right'><strong>{suma:.1f} %</strong></td>"
    )
    filas.append(
        f"<tr style='background:#E8F5E9'><td colspan='2'><strong>Total</strong></td>"
        f"{suma_td}<td></td></tr>"
    )

    return f"""
<section>
  <h2>Criterios de ponderación</h2>
  <table>
    <tr><th>Criterio</th><th>Fuente</th><th style='text-align:right'>Peso</th><th>Estado</th></tr>
    {"".join(filas)}
  </table>
</section>"""


def _seccion_resumen(capas_resultado: dict) -> str:
    lineas_html = []

    capa_excl = capas_resultado.get("excluida")
    capa_apta = capas_resultado.get("apta")
    raster_apt = capas_resultado.get("aptitud")

    ha_excl = _area_ha(capa_excl) if (_QGIS_DISPONIBLE and capa_excl is not None) else 0.0
    ha_apta = _area_ha(capa_apta) if (_QGIS_DISPONIBLE and capa_apta is not None) else 0.0
    ha_total = ha_excl + ha_apta

    if ha_total > 0:
        lineas_html.append(
            f"<strong>Área de interés:</strong> {ha_total:,.2f} ha "
            f"({ha_total / 100.0:,.2f} km²)"
        )
    if capa_excl is not None:
        pct = (ha_excl / ha_total * 100.0) if ha_total > 0 else 0.0
        lineas_html.append(
            f"<strong>Área prohibida:</strong> {ha_excl:,.2f} ha "
            f"({ha_excl / 100.0:,.2f} km² — {pct:,.1f} % del área de interés)"
        )
    if capa_apta is not None:
        pct = (ha_apta / ha_total * 100.0) if ha_total > 0 else 0.0
        lineas_html.append(
            f"<strong>Área permitida:</strong> {ha_apta:,.2f} ha "
            f"({ha_apta / 100.0:,.2f} km² — {pct:,.1f} % del área de interés)"
        )
    if raster_apt is not None:
        lineas_html.append(
            "<strong>Ráster de aptitud ponderada:</strong> "
            "generado correctamente — escala 0 (baja aptitud) a 1 (alta aptitud)."
        )

    if not lineas_html:
        cuerpo = "<p class='warn'>No hay capas de resultado. Ejecute el análisis primero.</p>"
    else:
        items = "".join(f"<p>{l}</p>" for l in lineas_html)
        cuerpo = items + (
            "<p style='font-size:11px;color:#555;margin-top:8px;'>"
            "Las capas han sido añadidas al proyecto QGIS activo.</p>"
        )

    return f"""
<section>
  <h2>Resultados del análisis</h2>
  <div class='resumen-box'>
    {cuerpo}
  </div>
</section>"""


def _pie() -> str:
    return """
<footer>
  <p>Plugin <strong>Evaluación RSU</strong> v1.0.0 &mdash;
     Desarrollado conforme a la NOM-083-SEMARNAT-2003.
     Autor: Sergio López Olvera &lt;lopezolverasergio@gmail.com&gt;.</p>
  <p>Este reporte es de carácter técnico informativo.
     Los resultados deben validarse con levantamiento de campo y consulta oficial.</p>
</footer>"""


# ---------------------------------------------------------------------------
# Utilidad de área
# ---------------------------------------------------------------------------

def _area_ha(capa) -> float:
    """Retorna el área total de una capa vectorial en hectáreas."""
    try:
        da = QgsDistanceArea()
        da.setSourceCrs(capa.crs(), QgsProject.instance().transformContext())
        da.setEllipsoid(QgsProject.instance().ellipsoid() or "WGS84")
        total_m2 = sum(da.measureArea(f.geometry()) for f in capa.getFeatures())
        return total_m2 / 10_000.0
    except Exception:
        return 0.0
