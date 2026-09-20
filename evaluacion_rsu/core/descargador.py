# -*- coding: utf-8 -*-
# EvaluaciónRSU — Plugin QGIS para la selección de sitios de disposición final de RSU
# Copyright (C) 2024-2025  Sergio López Olvera <lopezolverasergio@gmail.com>
# SPDX-License-Identifier: GPL-3.0-or-later
# Repository: https://github.com/sergio-lopez-olvera/evaluacion-rsu

"""
Módulo de descarga de datos geoespaciales para el plugin EvaluaciónRSU.

Estrategia por tipo de fuente:
  * INEGI_DL, CONANP, CONABIO → descarga del ZIP publicado y extracción del SHP.
  * OSM                       → consulta a la Overpass API, recortada al bbox
                                del área de interés, convertida a GeoJSON.
  * COPERNICUS                → tiles del MDT GLO-30, fusión, cálculo de
                                pendiente y vectorización de la máscara.
  * MANUAL                    → emite una señal para que la GUI pida el archivo.

Toda petición de red pasa por core/red.py, que envuelve
QgsNetworkAccessManager: así se respetan el proxy, la autenticación y los
certificados configurados en QGIS. No se usa urllib ni requests.

Las capas descargadas se guardan en:
  <directorio_trabajo>/datos_evaluacion_rsu/
"""

import json
import math
import os
import urllib.parse       # solo urlencode: no realiza red
import zipfile
from pathlib import Path
from typing import Callable, Optional

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsProject,
    QgsRasterLayer,
    QgsRectangle,
    QgsVectorFileWriter,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QObject, pyqtSignal

from .criterios import Criterio, FuenteDatos
from .red import ErrorRed, descargar_archivo, publicar

# ---------------------------------------------------------------------------
# Constantes Overpass
# ---------------------------------------------------------------------------

_OVERPASS_URL = "https://overpass-api.de/api/interpreter"
_OVERPASS_FALLBACK = "https://overpass.kumi.systems/api/interpreter"

# ---------------------------------------------------------------------------
# Nota: la integración con el ArcGIS REST de CENAPRED (Atlas Nacional de
# Riesgos por Inundación) fue evaluada pero la capa Tr=100 años (Layer 6)
# es de tipo Raster, no consultable como GeoJSON vectorial.
# La fuente oficial se distribuye vía KMZ regional; el plugin descarga el
# shapefile fusionado nacional alojado en el bucket Supabase del proyecto.
# ---------------------------------------------------------------------------

# Tipos de vía OSM que se descargan para el criterio de vialidad.
# Se incluyen desde autopistas hasta caminos rurales (excluye senderos y pistas).
_OSM_HIGHWAY_FILTER = (
    "motorway|trunk|primary|secondary|tertiary|"
    "unclassified|residential|road"
)

# ---------------------------------------------------------------------------
# Consultas Overpass QL por criterio ({bbox} = S,W,N,E — se sustituye en tiempo
# de ejecución con str.format(bbox=...))
# ---------------------------------------------------------------------------

_OSM_QUERIES: dict[str, str] = {
    # Red vial — ways de todo tipo de carretera/camino
    "vialidad": (
        "[out:json][timeout:90][bbox:{bbox}];"
        "way[\"highway\"~\"^(" + _OSM_HIGHWAY_FILTER + ")$\"];"
        "out body;>;out skel qt;"
    ),
    # Corrientes de agua — rivers, streams, canals (líneas)
    "corrientes_agua": (
        "[out:json][timeout:90][bbox:{bbox}];"
        "(way[\"waterway\"~\"^(river|stream|canal|drain)$\"];);"
        "out body;>;out skel qt;"
    ),
    # Cuerpos de agua — lagos, lagunas, embalses (polígonos)
    "cuerpos_agua": (
        "[out:json][timeout:90][bbox:{bbox}];"
        "(way[\"natural\"=\"water\"][\"water\"!~\"^(river|stream)$\"];"
        " relation[\"type\"=\"multipolygon\"][\"natural\"=\"water\"];"
        " way[\"water\"~\"^(lake|pond|reservoir|lagoon)$\"];"
        " relation[\"type\"=\"multipolygon\"][\"water\"~\"^(lake|pond|reservoir|lagoon)$\"];);"
        "out body;>>;out skel qt;"
    ),
    # Humedales/pantanos — wetlands, manglares, marismas (polígonos)
    "zona_pantanosa": (
        "[out:json][timeout:90][bbox:{bbox}];"
        "(way[\"natural\"~\"^(wetland|marsh|mangrove)$\"];"
        " relation[\"type\"=\"multipolygon\"][\"natural\"~\"^(wetland|marsh|mangrove)$\"];);"
        "out body;>>;out skel qt;"
    ),
}

# Criterios OSM que producen geometrías de polígono (en lugar de línea)
_OSM_POLIGONO: frozenset = frozenset({"cuerpos_agua", "zona_pantanosa"})


# ---------------------------------------------------------------------------
# Excepciones propias
# ---------------------------------------------------------------------------

class ErrorDescarga(Exception):
    """Excepción lanzada cuando falla la descarga de un criterio."""


# ---------------------------------------------------------------------------
# Utilidades de validación HTTP
# ---------------------------------------------------------------------------

def _detectar_soft404(ruta_archivo: "Path") -> None:
    """Levanta ErrorDescarga si el archivo descargado es una página HTML (soft-404).

    Algunos servidores devuelven HTTP 200 con cuerpo HTML cuando la URL no
    existe o el servicio está caído. La petición no falla con error HTTP en
    ese caso, de modo que el archivo parecería haberse descargado correctamente
    pero no contiene datos geográficos.

    Parameters
    ----------
    ruta_archivo : Path
        Ruta al archivo recién descargado.

    Raises
    ------
    ErrorDescarga
        Si las primeras líneas del archivo comienzan con HTML.
    """
    try:
        with open(ruta_archivo, "rb") as f:
            inicio = f.read(512).lstrip()
    except OSError:
        return  # Si no se puede leer, dejar que el resto del flujo falle

    marcadores_html = (
        b"<!DOCTYPE", b"<!doctype",
        b"<html",     b"<HTML",
        b"<head",     b"<HEAD",
    )
    if any(inicio.startswith(m) for m in marcadores_html):
        raise ErrorDescarga(
            "El servidor devolvió una página HTML en lugar del archivo esperado.\n"
            "El servicio puede estar temporalmente caído o la URL ha cambiado.\n"
            "Descargue el archivo manualmente usando el botón «Descargar»."
        )


# ---------------------------------------------------------------------------
# Utilidades OSM
# ---------------------------------------------------------------------------

def _detectar_soft404_bytes(contenido: bytes) -> None:
    """Levanta ErrorDescarga si la respuesta es HTML en lugar de JSON/datos."""
    inicio = contenido[:512].lstrip()
    marcadores_html = (b"<!DOCTYPE", b"<!doctype", b"<html", b"<HTML", b"<head", b"<HEAD")
    if any(inicio.startswith(m) for m in marcadores_html):
        raise ErrorDescarga(
            "El servidor Overpass devolvió una página HTML en lugar de datos OSM.\n"
            "El servicio puede estar temporalmente caído. Intente más tarde."
        )


def _osm_json_a_geojson(osm: dict) -> dict:
    """Convierte la respuesta JSON de Overpass al formato GeoJSON.

    Procesa únicamente los ``way`` (líneas), resolviendo las coordenadas
    de sus nodos a partir del bloque ``elements`` de la respuesta.
    """
    # Índice de nodos: id → (lon, lat)
    nodos: dict[int, tuple[float, float]] = {
        el["id"]: (el["lon"], el["lat"])
        for el in osm.get("elements", [])
        if el["type"] == "node" and "lon" in el
    }

    features = []
    for el in osm.get("elements", []):
        if el["type"] != "way":
            continue
        coords = [nodos[nid] for nid in el.get("nodes", []) if nid in nodos]
        if len(coords) < 2:
            continue
        props = el.get("tags", {})
        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {
                "osm_id":   el["id"],
                "highway":  props.get("highway", ""),
                "name":     props.get("name", ""),
                "ref":      props.get("ref", ""),
                "surface":  props.get("surface", ""),
                "oneway":   props.get("oneway", ""),
                "lanes":    props.get("lanes", ""),
                "maxspeed": props.get("maxspeed", ""),
            },
        })

    return {
        "type": "FeatureCollection",
        "features": features,
    }


def _osm_json_a_geojson_poligono(osm: dict) -> dict:
    """Convierte la respuesta JSON de Overpass a GeoJSON con geometrías poligonales.

    Procesa:
    - ``way`` con anillo cerrado (primer nodo == último) → Polygon.
    - ``relation`` de tipo multipolygon → Polygon / MultiPolygon usando
      los miembros con rol ``outer`` e ``inner``.

    Los ways que forman parte de una relation no se duplican como features
    independientes.
    """
    # Índice de nodos: id → [lon, lat]
    nodos: dict[int, list] = {
        el["id"]: [el["lon"], el["lat"]]
        for el in osm.get("elements", [])
        if el["type"] == "node" and "lon" in el
    }
    # Índice de ways: id → elemento completo
    ways: dict[int, dict] = {
        el["id"]: el
        for el in osm.get("elements", [])
        if el["type"] == "way"
    }

    def _anillo(way_el: dict) -> list:
        """Devuelve el anillo cerrado de coordenadas de un way, o lista vacía."""
        pts = [nodos[nid] for nid in way_el.get("nodes", []) if nid in nodos]
        if len(pts) < 3:
            return []
        if pts[0] != pts[-1]:
            pts.append(pts[0])
        return pts if len(pts) >= 4 else []

    features = []
    ids_en_relacion: set = set()

    # ── 1. Relations (multipolygon / boundary) ──────────────────────────────
    for el in osm.get("elements", []):
        if el["type"] != "relation":
            continue
        tags = el.get("tags", {})
        if tags.get("type") not in ("multipolygon", "boundary"):
            continue

        outers, inners = [], []
        for m in el.get("members", []):
            if m.get("type") != "way":
                continue
            way = ways.get(m.get("ref"))
            if way is None:
                continue
            ring = _anillo(way)
            if not ring:
                continue
            if m.get("role", "outer") == "outer":
                outers.append(ring)
            else:
                inners.append(ring)
            ids_en_relacion.add(way["id"])

        if not outers:
            continue

        if len(outers) == 1:
            geom = {"type": "Polygon", "coordinates": [outers[0]] + inners}
        else:
            geom = {
                "type": "MultiPolygon",
                "coordinates": [[ring] for ring in outers],
            }

        features.append({
            "type": "Feature",
            "geometry": geom,
            "properties": {
                "osm_id":   el["id"],
                "osm_type": "relation",
                "natural":  tags.get("natural", ""),
                "water":    tags.get("water", ""),
                "wetland":  tags.get("wetland", ""),
                "name":     tags.get("name", ""),
            },
        })

    # ── 2. Ways cerrados no incluidos en ninguna relation ───────────────────
    for el in osm.get("elements", []):
        if el["type"] != "way" or el["id"] in ids_en_relacion:
            continue
        tags = el.get("tags", {})
        if not tags:
            # Way geométrico sin etiquetas propias (auxiliar de relation)
            continue
        ring = _anillo(el)
        if not ring:
            continue
        features.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [ring]},
            "properties": {
                "osm_id":   el["id"],
                "osm_type": "way",
                "natural":  tags.get("natural", ""),
                "water":    tags.get("water", ""),
                "wetland":  tags.get("wetland", ""),
                "name":     tags.get("name", ""),
            },
        })

    return {"type": "FeatureCollection", "features": features}


# ---------------------------------------------------------------------------
# Clase principal
# ---------------------------------------------------------------------------

class Descargador(QObject):
    """Gestiona la descarga y precarga de datos para cada criterio.

    Señales
    -------
    progreso(int, str)
        Emitida durante la descarga: porcentaje (0-100) y mensaje de estado.
    capa_lista(str, QgsVectorLayer)
        Emitida cuando una capa queda disponible (descargada o cargada).
    error(str, str)
        Emitida cuando falla la descarga de un criterio: id_criterio, mensaje.
    requiere_archivo(str)
        Emitida cuando la fuente es MANUAL o la descarga falló y se necesita
        que el usuario seleccione un archivo local.
    """

    progreso = pyqtSignal(int, str)
    capa_lista = pyqtSignal(str, object)   # (id_criterio, QgsVectorLayer)
    error = pyqtSignal(str, str)           # (id_criterio, mensaje)
    requiere_archivo = pyqtSignal(str)     # (id_criterio)

    # CRS WGS84 — usado para el bbox de las peticiones WFS
    CRS_WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")

    # Tope de tiles del MDT Copernicus por análisis. Cada tile cubre 1°×1° y
    # pesa entre 25 y 60 MB; más allá de esto la descarga deja de ser razonable
    # y conviene dividir el área o aportar un MDT propio.
    MAX_TILES_DEM = 12

    def __init__(self, aoi_layer: QgsVectorLayer, directorio_trabajo: str, parent=None):
        """
        Parameters
        ----------
        aoi_layer:
            Capa vectorial del área de interés.
        directorio_trabajo:
            Ruta local donde se guardarán los datos descargados.
        """
        super().__init__(parent)
        self.aoi_layer = aoi_layer
        self.directorio_trabajo = Path(directorio_trabajo) / "datos_evaluacion_rsu"
        self.directorio_trabajo.mkdir(parents=True, exist_ok=True)
        self._bbox_wgs84: Optional[QgsRectangle] = None

    # ------------------------------------------------------------------
    # BBox del área de interés en WGS84
    # ------------------------------------------------------------------

    def bbox_wgs84(self) -> QgsRectangle:
        """Retorna el bounding box del área de interés reprojectado a WGS84."""
        if self._bbox_wgs84 is None:
            crs_aoi = self.aoi_layer.crs()
            extent = self.aoi_layer.extent()

            if crs_aoi != self.CRS_WGS84:
                xform = QgsCoordinateTransform(
                    crs_aoi, self.CRS_WGS84, QgsProject.instance()
                )
                extent = xform.transformBoundingBox(extent)

            # Añadir 10 % de margen para cubrir buffers de exclusión
            dx = extent.width() * 0.10
            dy = extent.height() * 0.10
            self._bbox_wgs84 = QgsRectangle(
                extent.xMinimum() - dx,
                extent.yMinimum() - dy,
                extent.xMaximum() + dx,
                extent.yMaximum() + dy,
            )
        return self._bbox_wgs84

    def bbox_str(self) -> str:
        """Retorna el bbox como cadena 'minx,miny,maxx,maxy,EPSG:4326'."""
        b = self.bbox_wgs84()
        return f"{b.xMinimum()},{b.yMinimum()},{b.xMaximum()},{b.yMaximum()},EPSG:4326"

    # ------------------------------------------------------------------
    # Descarga de un criterio
    # ------------------------------------------------------------------

    def descargar_criterio(self, criterio: Criterio) -> Optional[QgsVectorLayer]:
        """Intenta descargar o cargar la capa para el criterio dado.

        Returns
        -------
        QgsVectorLayer si la descarga fue exitosa, None en caso contrario.
        Emite las señales correspondientes en todos los casos.
        """
        self.progreso.emit(0, f"Iniciando descarga: {criterio.nombre}…")

        try:
            if criterio.fuente in (
                FuenteDatos.CONANP,
                FuenteDatos.CONABIO,
                FuenteDatos.INEGI_DL,
            ):
                capa = self._descargar_url(criterio)
            elif criterio.fuente == FuenteDatos.OSM:
                capa = self._descargar_osm(criterio)
            elif criterio.fuente == FuenteDatos.COPERNICUS:
                capa = self._descargar_copernicus_pendiente(criterio)
            else:
                # FuenteDatos.MANUAL
                self.requiere_archivo.emit(criterio.id)
                return None

            if capa is None or not capa.isValid():
                raise ErrorDescarga("La capa descargada no es válida.")

            criterio.capa = capa
            criterio.origen_carga = "automatica"
            criterio.ruta_dato = self._ruta_de_capa(capa)
            self.capa_lista.emit(criterio.id, capa)
            self.progreso.emit(100, f"✓ {criterio.nombre} cargado correctamente.")
            return capa

        except ErrorDescarga as exc:
            msg = str(exc)
            self.error.emit(criterio.id, msg)
            self.progreso.emit(0, f"✗ Error en {criterio.nombre}: {msg}")
            # Fallback: pedir archivo manual
            self.requiere_archivo.emit(criterio.id)
            return None

    # Extensiones ráster que el diálogo de archivos ofrece, más .vrt.
    EXTENSIONES_RASTER = (".tif", ".tiff", ".img", ".asc", ".vrt", ".dem", ".bil")

    def cargar_archivo_local(self, criterio: Criterio, ruta: str):
        """Carga una capa desde un archivo local seleccionado por el usuario.

        Admite tanto vectoriales como rásters. Antes construía siempre un
        ``QgsVectorLayer``, de modo que cualquier ráster —incluido el modelo de
        elevación que la descripción del criterio de pendiente pide cargar a
        mano— se rechazaba con «Archivo inválido», pese a que el diálogo de
        archivos ofrece el filtro Ráster (*.tif *.img *.asc).
        """
        extension = Path(ruta).suffix.lower()

        if extension in self.EXTENSIONES_RASTER:
            capa = QgsRasterLayer(ruta, criterio.nombre)
            if not capa.isValid():
                self.error.emit(
                    criterio.id,
                    f"No se pudo abrir como ráster: {ruta}")
                return None
            criterio.capa = capa
            criterio.origen_carga = "manual"
            criterio.ruta_dato = ruta
            # La ponderación por rango óptimo lee los valores continuos de
            # aquí. Sin esto, un criterio ráster cargado a mano quedaba sin
            # fuente para la aptitud y sólo servía para la exclusión.
            criterio.ruta_raster_continuo = ruta
            self.capa_lista.emit(criterio.id, capa)
            return capa

        capa = QgsVectorLayer(ruta, criterio.nombre, "ogr")
        if not capa.isValid():
            # Puede ser un ráster con una extensión que no está en la lista.
            posible_raster = QgsRasterLayer(ruta, criterio.nombre)
            if posible_raster.isValid():
                criterio.capa = posible_raster
                criterio.origen_carga = "manual"
                criterio.ruta_dato = ruta
                criterio.ruta_raster_continuo = ruta
                self.capa_lista.emit(criterio.id, posible_raster)
                return posible_raster
            self.error.emit(criterio.id, f"No se pudo abrir el archivo: {ruta}")
            return None

        criterio.capa = capa
        criterio.origen_carga = "manual"
        criterio.ruta_dato = ruta
        self.capa_lista.emit(criterio.id, capa)
        return capa

    # ------------------------------------------------------------------
    # Descarga WFS
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Descarga directa de URL (ZIP con shapefile)
    # ------------------------------------------------------------------

    def _descargar_url(self, criterio: Criterio) -> QgsVectorLayer:
        """Descarga un archivo ZIP desde una URL pública y extrae el shapefile."""
        if not criterio.descarga_url:
            raise ErrorDescarga("No se definió una URL de descarga para este criterio.")

        url = criterio.descarga_url
        archivo_zip = self.directorio_trabajo / f"{criterio.id}.zip"
        carpeta_extraccion = self.directorio_trabajo / criterio.id

        # Reutilizar si ya fue extraído
        shp_existente = self._buscar_shapefile(carpeta_extraccion)
        if shp_existente:
            capa = QgsVectorLayer(str(shp_existente), criterio.nombre, "ogr")
            if capa.isValid():
                return capa

        self.progreso.emit(10, f"Descargando {criterio.nombre} desde {url}…")

        def _avance(recibido: int, total: int):
            if total > 0:
                pct = min(int(recibido * 50 / total), 50)
                mb = recibido / 1_048_576
                mb_tot = total / 1_048_576
                self.progreso.emit(
                    pct, f"Descargando {criterio.nombre}… "
                         f"{mb:,.1f} / {mb_tot:,.1f} MB")
            else:
                self.progreso.emit(
                    -1, f"Descargando {criterio.nombre}… "
                        f"{recibido / 1_048_576:,.1f} MB")

        try:
            descargar_archivo(url, archivo_zip, al_progresar=_avance)
        except ErrorRed as exc:
            raise ErrorDescarga(
                f"No se pudo descargar {criterio.nombre}:\n  {exc}\n\n"
                "Si su red usa proxy, verifíquelo en QGIS → Preferencias → "
                "Opciones → Red. También puede descargar el archivo a mano y "
                "cargarlo con «Seleccionar archivo…»."
            ) from exc

        # Detectar soft-404: servidor devuelve HTML en lugar del archivo esperado
        _detectar_soft404(archivo_zip)

        self.progreso.emit(55, f"Extrayendo {criterio.nombre}…")
        carpeta_extraccion.mkdir(parents=True, exist_ok=True)

        try:
            with zipfile.ZipFile(str(archivo_zip), "r") as zf:
                zf.extractall(str(carpeta_extraccion))
        except zipfile.BadZipFile as exc:
            raise ErrorDescarga(f"El archivo descargado no es un ZIP válido: {exc}") from exc

        shp = self._buscar_shapefile(carpeta_extraccion)
        if shp is None:
            raise ErrorDescarga(
                "No se encontró ningún shapefile (.shp) dentro del ZIP descargado."
            )

        capa = QgsVectorLayer(str(shp), criterio.nombre, "ogr")
        if not capa.isValid():
            raise ErrorDescarga(f"El shapefile encontrado no es válido: {shp}")
        return capa

    # ------------------------------------------------------------------
    # Descarga OSM vía Overpass API
    # ------------------------------------------------------------------

    def _descargar_osm(self, criterio: Criterio) -> QgsVectorLayer:
        """Descarga datos OSM del área de interés desde la Overpass API.

        Despacha la consulta Overpass correcta según ``criterio.id``
        (vialidad → líneas; cuerpos_agua / zona_pantanosa → polígonos;
        corrientes_agua → líneas de ríos/arroyos).

        Los datos se cachean como GeoPackage local para reutilización.
        """
        if criterio.id not in _OSM_QUERIES:
            raise ErrorDescarga(
                f"No existe una consulta Overpass definida para el criterio '{criterio.id}'."
            )

        archivo_local = self.directorio_trabajo / f"{criterio.id}.gpkg"

        # Reutilizar caché local si existe
        if archivo_local.exists():
            capa = QgsVectorLayer(str(archivo_local), criterio.nombre, "ogr")
            if capa.isValid():
                self.progreso.emit(80, f"Usando caché local OSM: {criterio.nombre}")
                return capa

        b = self.bbox_wgs84()
        # Overpass QL: bbox en orden S,W,N,E
        bbox_op = f"{b.yMinimum()},{b.xMinimum()},{b.yMaximum()},{b.xMaximum()}"
        query = _OSM_QUERIES[criterio.id].format(bbox=bbox_op)

        es_poligono = criterio.id in _OSM_POLIGONO
        tipo_geo = "polígonos" if es_poligono else "líneas"
        self.progreso.emit(10, f"Consultando OpenStreetMap — {criterio.nombre} ({tipo_geo})…")

        geojson_path = self.directorio_trabajo / f"{criterio.id}_osm.geojson"

        for endpoint in (_OVERPASS_URL, _OVERPASS_FALLBACK):
            try:
                datos = urllib.parse.urlencode({"data": query}).encode()
                contenido = publicar(endpoint, datos)

                _detectar_soft404_bytes(contenido)

                self.progreso.emit(50, f"Convirtiendo datos OSM → GeoJSON ({criterio.nombre})…")
                osm_json = json.loads(contenido)
                if es_poligono:
                    geojson = _osm_json_a_geojson_poligono(osm_json)
                else:
                    geojson = _osm_json_a_geojson(osm_json)

                with open(geojson_path, "w", encoding="utf-8") as f:
                    json.dump(geojson, f)
                break  # éxito — salir del bucle de endpoints

            except ErrorDescarga:
                raise
            except Exception as exc:
                if endpoint == _OVERPASS_FALLBACK:
                    raise ErrorDescarga(
                        f"No se pudo descargar '{criterio.nombre}' desde OpenStreetMap.\n"
                        f"Error: {exc}\n"
                        "Cargue el shapefile manualmente."
                    ) from exc
                self.progreso.emit(35, "Reintentando con servidor OSM alternativo…")

        # Cargar GeoJSON como capa vectorial temporal
        self.progreso.emit(65, f"Cargando capa OSM: {criterio.nombre}…")
        capa_tmp = QgsVectorLayer(str(geojson_path), criterio.nombre, "ogr")
        if not capa_tmp.isValid():
            raise ErrorDescarga(f"No se pudo cargar la capa OSM: {geojson_path}")

        if capa_tmp.featureCount() == 0:
            raise ErrorDescarga(
                f"La consulta OSM no devolvió geometrías para '{criterio.nombre}' "
                "en el área de interés. Es posible que los datos no existan en OSM "
                "para esta región. Cargue el shapefile manualmente."
            )

        # Guardar en GeoPackage local para reutilización
        self.progreso.emit(75, f"Guardando en caché local: {criterio.nombre}…")
        opciones = QgsVectorFileWriter.SaveVectorOptions()
        opciones.driverName = "GPKG"
        opciones.fileEncoding = "UTF-8"

        error, _ = QgsVectorFileWriter.writeAsVectorFormatV2(
            capa_tmp,
            str(archivo_local),
            QgsProject.instance().transformContext(),
            opciones,
        )
        if error == QgsVectorFileWriter.NoError:
            capa_local = QgsVectorLayer(str(archivo_local), criterio.nombre, "ogr")
            if capa_local.isValid():
                return capa_local

        # Si el guardado falla, usar la capa en memoria directamente
        return capa_tmp

    # ------------------------------------------------------------------
    # Descarga Copernicus GLO-30 DEM + cálculo de pendiente
    # ------------------------------------------------------------------

    def _tiles_copernicus(self, bbox: QgsRectangle) -> list:
        """Enumera los tiles 1°×1° de Copernicus GLO-30 que cubren el bbox.

        El sistema de tiles usa coordenadas enteras: el tile (lat, lon) cubre
        la celda [lat, lat+1) × [lon, lon+1).  Las latitudes negativas usan
        prefijo S y las longitudes negativas prefijo W.
        """
        tiles = []
        lat_min = int(math.floor(bbox.yMinimum()))
        lat_max = int(math.ceil(bbox.yMaximum()))
        lon_min = int(math.floor(bbox.xMinimum()))
        lon_max = int(math.ceil(bbox.xMaximum()))
        for lat in range(lat_min, lat_max):
            for lon in range(lon_min, lon_max):
                tiles.append((lat, lon))
        return tiles

    def _url_tile_copernicus(self, lat: int, lon: int) -> str:
        """Construye la URL pública de un tile Copernicus GLO-30 en AWS S3."""
        ns = "N" if lat >= 0 else "S"
        ew = "E" if lon >= 0 else "W"
        lat_str = f"{ns}{abs(lat):02d}"
        lon_str = f"{ew}{abs(lon):03d}"
        nombre = f"Copernicus_DSM_COG_10_{lat_str}_00_{lon_str}_00_DEM"
        return f"https://copernicus-dem-30m.s3.amazonaws.com/{nombre}/{nombre}.tif"

    def _descargar_copernicus_pendiente(self, criterio: Criterio) -> QgsVectorLayer:
        """Descarga tiles DEM Copernicus GLO-30, calcula pendiente y vectoriza zonas >25%.

        Flujo:
          1. Enumerar tiles 1°×1° que cubren el bbox del área de interés.
          2. Descargar cada tile desde AWS S3 (sin autenticación).
          3. Fusionar tiles con gdal:merge (si hay más de uno).
          4. Calcular pendiente (%) con gdal:slope.
          5. Umbralizar: pendiente > 25 % → ráster binario con gdal:rastercalculator.
          6. Vectorizar con gdal:polygonize → polígonos de exclusión.
          7. Filtrar sólo polígonos con DN=1 (pendiente > 25 %).
          8. Guardar en GeoPackage local y retornar QgsVectorLayer.

        El resultado es la capa de *zonas de exclusión* por pendiente excesiva,
        equivalente a lo que el evaluador usa para el criterio de pendiente.
        """
        import processing  # importación tardía — no disponible en tiempo de importación del módulo

        directorio_dem = self.directorio_trabajo / "dem_copernicus"
        directorio_dem.mkdir(parents=True, exist_ok=True)

        archivo_local = self.directorio_trabajo / f"{criterio.id}.gpkg"

        # Reutilizar caché local si ya fue procesado
        if archivo_local.exists():
            capa = QgsVectorLayer(str(archivo_local), criterio.nombre, "ogr")
            if capa.isValid():
                self.progreso.emit(80, f"Usando caché local DEM: {criterio.nombre}")
                return capa

        bbox = self.bbox_wgs84()
        tiles = self._tiles_copernicus(bbox)

        if not tiles:
            raise ErrorDescarga(
                "No se pudieron determinar los tiles DEM para el área de interés.\n"
                "Verifique que el área de interés tenga coordenadas válidas."
            )

        # ── Descargar tiles ────────────────────────────────────────────────
        # Cada tile GLO-30 cubre 1°×1° y pesa entre 25 y 60 MB. Se rechaza de
        # entrada un área que exigiría una descarga desproporcionada, en lugar
        # de dejar al usuario esperando sin saber cuánto falta.
        total_tiles = len(tiles)
        if total_tiles > self.MAX_TILES_DEM:
            raise ErrorDescarga(
                f"El área de interés abarca {total_tiles} tiles del MDT "
                f"(máximo {self.MAX_TILES_DEM}, ~{total_tiles * 40} MB de descarga).\n\n"
                "Opciones:\n"
                "  • Reduzca el área de interés y ejecute por zonas.\n"
                "  • Desactive el criterio de pendiente.\n"
                "  • Cargue un MDT propio con «Seleccionar archivo…»\n"
                "    (INEGI ContinuoMex: https://www.inegi.org.mx/app/geo2/elevacionesmex/)"
            )
        if total_tiles > 4:
            self.progreso.emit(
                -1,
                f"Aviso: el área requiere {total_tiles} tiles del MDT "
                f"(~{total_tiles * 40} MB). La primera ejecución puede tardar; "
                f"los tiles quedan en caché para las siguientes."
            )

        rutas_tiles: list[str] = []
        omitidos: list[str] = []

        for i, (lat, lon) in enumerate(tiles):
            base_pct = int(i / total_tiles * 30)
            ns = "N" if lat >= 0 else "S"
            ew = "E" if lon >= 0 else "W"
            etiqueta = f"{ns}{abs(lat):02d}° {ew}{abs(lon):03d}°"
            nombre_tile = f"dem_{ns}{abs(lat):02d}_{ew}{abs(lon):03d}.tif"
            ruta_tile = directorio_dem / nombre_tile

            # Caché: los tiles del MDT no cambian, así que se reutilizan siempre
            if ruta_tile.exists() and ruta_tile.stat().st_size > 0:
                self.progreso.emit(
                    base_pct, f"Tile MDT {i + 1}/{total_tiles} ({etiqueta}) en caché")
                rutas_tiles.append(str(ruta_tile))
                continue

            def _avance(recibido: int, total: int, _p=base_pct, _e=etiqueta,
                        _i=i, _n=total_tiles):
                mb = recibido / 1_048_576
                if total > 0:
                    frac = recibido / total / _n * 30
                    self.progreso.emit(
                        int(_p + frac),
                        f"Descargando MDT {_i + 1}/{_n} ({_e})… "
                        f"{mb:,.1f} / {total / 1_048_576:,.1f} MB")
                else:
                    self.progreso.emit(
                        -1, f"Descargando MDT {_i + 1}/{_n} ({_e})… {mb:,.1f} MB")

            url = self._url_tile_copernicus(lat, lon)
            try:
                descargar_archivo(url, ruta_tile, al_progresar=_avance)
                rutas_tiles.append(str(ruta_tile))
            except ErrorRed as exc:
                # Un tile ausente no es un error fatal: el archivo Copernicus no
                # publica tiles completamente oceánicos, situación habitual en
                # áreas costeras. Se omite y se continúa con los demás.
                omitidos.append(etiqueta)
                self.progreso.emit(
                    -1,
                    f"  Tile {etiqueta} no disponible (probablemente océano); "
                    f"se omite.")
                continue

        if omitidos:
            self.progreso.emit(
                -1, f"Tiles omitidos ({len(omitidos)}): {', '.join(omitidos)}")

        if not rutas_tiles:
            raise ErrorDescarga(
                "Ningún tile del MDT Copernicus está disponible para esta área.\n\n"
                "Si el área es mayormente marina, desactive el criterio de "
                "pendiente. Si no, verifique su conexión —y el proxy en QGIS → "
                "Preferencias → Opciones → Red— o cargue un MDT propio desde\n"
                "  https://www.inegi.org.mx/app/geo2/elevacionesmex/"
            )

        # ── Paso 3: Fusionar tiles ─────────────────────────────────────────
        self.progreso.emit(35, "Fusionando tiles DEM Copernicus…")
        ruta_dem_merged = str(self.directorio_trabajo / "dem_merged.tif")

        if len(rutas_tiles) == 1:
            ruta_dem_merged = rutas_tiles[0]
        else:
            result = processing.run("gdal:merge", {
                "INPUT":        rutas_tiles,
                "PCT":          False,
                "SEPARATE":     False,
                "NODATA_INPUT": None,
                "NODATA_OUTPUT": None,
                "OPTIONS":      "",
                "EXTRA":        "",
                "DATA_TYPE":    5,      # Float32
                "OUTPUT":       ruta_dem_merged,
            })
            ruta_dem_merged = result["OUTPUT"]

        # Verificar que el DEM fusionado existe y es válido
        capa_dem = QgsRasterLayer(ruta_dem_merged, "dem_tmp")
        if not capa_dem.isValid():
            raise ErrorDescarga(
                "El DEM Copernicus descargado no pudo cargarse como capa ráster válida.\n"
                "Elimine la carpeta de caché 'dem_copernicus' e intente nuevamente."
            )

        # ── Recortar al área de interés ────────────────────────────────────
        # Un tile completo mide unos 3 600 × 3 600 px. Calcular pendiente,
        # umbralizar y vectorizar sobre la extensión completa multiplica el
        # trabajo por el área que se va a descartar de todos modos: el recorte
        # previo es la diferencia entre minutos y segundos.
        self.progreso.emit(42, "Recortando el MDT al área de interés…")
        ruta_recorte = str(self.directorio_trabajo / "dem_recortado.tif")
        margen = 0.02          # ~2 km de holgura para que el borde no quede sesgado
        try:
            processing.run("gdal:translate", {
                "INPUT": ruta_dem_merged,
                "PROJWIN": (
                    f"{bbox.xMinimum() - margen},{bbox.xMaximum() + margen},"
                    f"{bbox.yMaximum() + margen},{bbox.yMinimum() - margen}"
                    " [EPSG:4326]"
                ),
                "DATA_TYPE": 0,
                "OUTPUT": ruta_recorte,
            })
            capa_recorte = QgsRasterLayer(ruta_recorte, "dem_recorte_tmp")
            if capa_recorte.isValid() and capa_recorte.width() > 0:
                px_antes = capa_dem.width() * capa_dem.height()
                px_ahora = capa_recorte.width() * capa_recorte.height()
                if px_antes > 0:
                    self.progreso.emit(
                        -1,
                        f"  MDT reducido de {capa_dem.width()}×{capa_dem.height()} "
                        f"a {capa_recorte.width()}×{capa_recorte.height()} px "
                        f"({px_ahora / px_antes * 100:,.1f} % del original)")
                ruta_dem_merged = ruta_recorte
        except Exception as exc:  # noqa: BLE001
            # Si el recorte falla se sigue con el MDT completo: más lento, pero
            # el resultado es el mismo.
            self.progreso.emit(
                -1, f"  No se pudo recortar el MDT ({exc}); se procesa completo.")

        # ── Calcular pendiente (%) ────────────────────────────────────────
        self.progreso.emit(50, "Calculando pendiente del terreno (%)…")
        ruta_pendiente = str(self.directorio_trabajo / "pendiente.tif")

        processing.run("gdal:slope", {
            "INPUT":         ruta_dem_merged,
            "BAND":          1,
            "SCALE":         1,
            "AS_PERCENT":    True,
            "COMPUTE_EDGES": True,
            "ZEVENBERGEN":   False,
            "OPTIONS":       "",
            "EXTRA":         "",
            "OUTPUT":        ruta_pendiente,
        })

        # ── Paso 5: Umbralizar (pendiente > 25 %) → ráster binario ───────
        self.progreso.emit(65, "Generando máscara de exclusión (pendiente > 25 %)…")
        ruta_binario = str(self.directorio_trabajo / "pendiente_exclusion.tif")

        processing.run("gdal:rastercalculator", {
            "INPUT_A": ruta_pendiente,
            "BAND_A":  1,
            "INPUT_B": None,
            "BAND_B":  -1,
            "INPUT_C": None,
            "BAND_C":  -1,
            "INPUT_D": None,
            "BAND_D":  -1,
            "INPUT_E": None,
            "BAND_E":  -1,
            "INPUT_F": None,
            "BAND_F":  -1,
            "FORMULA": "(A > 25) * 1",
            "NO_DATA": 0,
            "PROJWIN": None,
            "RTYPE":   0,       # Byte
            "OPTIONS": "",
            "EXTRA":   "",
            "OUTPUT":  ruta_binario,
        })

        # ── Paso 6: Vectorizar (polygonize) ───────────────────────────────
        self.progreso.emit(80, "Vectorizando zonas con pendiente > 25 %…")
        ruta_vector_tmp = str(self.directorio_trabajo / "pendiente_poligonos_tmp.gpkg")

        processing.run("gdal:polygonize", {
            "INPUT":              ruta_binario,
            "BAND":               1,
            "FIELD":              "DN",
            "EIGHT_CONNECTEDNESS": False,
            "EXTRA":              "",
            "OUTPUT":             ruta_vector_tmp,
        })

        # ── Paso 7: Filtrar DN=1 (exclusión) y guardar en GPKG ───────────
        self.progreso.emit(90, "Filtrando zonas de exclusión por pendiente…")
        capa_tmp = QgsVectorLayer(ruta_vector_tmp, "pendiente_tmp", "ogr")
        if not capa_tmp.isValid():
            raise ErrorDescarga(
                "La vectorización de la capa de pendiente no produjo una capa válida.\n"
                "Intente nuevamente o cargue un ráster de pendiente manualmente."
            )

        # Sólo polígonos donde DN=1 → pendiente > 25 %
        capa_tmp.setSubsetString('"DN" = 1')

        # Un terreno sin pendientes > 25 % es un resultado válido, no un fallo:
        # significa que este criterio no excluye nada. Antes se lanzaba
        # ErrorDescarga y la GUI acababa pidiendo un archivo manual.
        if capa_tmp.featureCount() == 0:
            self.progreso.emit(
                -1,
                "  No hay pendientes > 25 % en el área: el criterio no excluye "
                "superficie (terreno predominantemente plano).")

        opciones = QgsVectorFileWriter.SaveVectorOptions()
        opciones.driverName = "GPKG"
        opciones.fileEncoding = "UTF-8"

        err, _ = QgsVectorFileWriter.writeAsVectorFormatV2(
            capa_tmp,
            str(archivo_local),
            QgsProject.instance().transformContext(),
            opciones,
        )
        if err != QgsVectorFileWriter.NoError:
            # Guardado local falló — usar capa filtrada en memoria
            return capa_tmp

        capa_final = QgsVectorLayer(str(archivo_local), criterio.nombre, "ogr")
        if not capa_final.isValid():
            return capa_tmp

        # Conservar el ráster de pendiente: la capa que se entrega son los
        # polígonos de exclusión, pero la ponderación por rango óptimo necesita
        # los valores continuos.
        criterio.ruta_raster_continuo = ruta_pendiente
        return capa_final

    # ------------------------------------------------------------------
    # Descarga CENAPRED — Atlas Nacional de Riesgos por Inundación
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Utilidades
    # ------------------------------------------------------------------

    @staticmethod
    def _ruta_de_capa(capa) -> Optional[str]:
        """Ruta en disco de una capa, sin el sufijo |layername=… de OGR."""
        try:
            return capa.source().split("|")[0]
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _buscar_shapefile(directorio: Path) -> Optional[Path]:
        """Busca el primer archivo .shp dentro de un directorio (recursivo)."""
        if not directorio.exists():
            return None
        for shp in directorio.rglob("*.shp"):
            return shp
        return None

    def descargar_todos(self, criterios: list[Criterio]) -> dict[str, Optional[QgsVectorLayer]]:
        """Descarga todos los criterios con fuente automática.

        Returns
        -------
        dict con id_criterio → QgsVectorLayer (o None si falló / es manual).
        """
        resultados: dict[str, Optional[QgsVectorLayer]] = {}
        total = len(criterios)

        for i, criterio in enumerate(criterios):
            base_pct = int(i / total * 100)
            self.progreso.emit(base_pct, f"Procesando {criterio.nombre}…")

            if criterio.fuente == FuenteDatos.MANUAL:
                resultados[criterio.id] = None
                self.requiere_archivo.emit(criterio.id)
            else:
                resultados[criterio.id] = self.descargar_criterio(criterio)

        self.progreso.emit(100, "Descarga de datos completada.")
        return resultados
