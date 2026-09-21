# -*- coding: utf-8 -*-
# EvaluaciónRSU — Plugin QGIS para la selección de sitios de disposición final de RSU
# Copyright (C) 2024-2025  Sergio López Olvera <lopezolverasergio@gmail.com>
# SPDX-License-Identifier: GPL-3.0-or-later
# Repository: https://github.com/sergio-lopez-olvera/evaluacion-rsu

"""
Definición de criterios de evaluación para selección de sitios RSU.

Fuentes de datos
────────────────
  * INEGI_DL   → shapefiles nacionales publicados como ZIP (Marco
                 Geoestadístico, hidrografía, geología, uso de suelo).
  * CONANP     → Áreas Naturales Protegidas federales y ADVC.
  * CONABIO    → inventario de ANP estatales, municipales y privadas.
  * OSM        → Overpass API, recortado al área de interés.
  * COPERNICUS → MDT GLO-30 para derivar la pendiente del terreno.
  * MANUAL     → el usuario aporta el archivo.

El WFS wsinfogeo del INEGI (WFSService.aspx) fue descontinuado y ya no se
usa; los datos vectoriales provienen de descargas directas.

Análisis de exclusión (resultado dicotómico):
    Derivado principalmente de NOM-083-SEMARNAT-2003 y la LGEEPA.

Análisis de ponderación:
    Criterios de aptitud relativa; el usuario asigna pesos (suma = 100 %).
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ===========================================================================
# Enumeradores
# ===========================================================================

class FuenteDatos(Enum):
    """Origen del dato geográfico."""
    INEGI_DL  = "inegi_dl"      # Descarga directa de ZIP desde INEGI
    CONANP    = "conanp"        # Shapefile ANP federal CONANP (ZIP directo)
    CONABIO   = "conabio"       # Shapefile Ramsar / inventario CONABIO (ZIP directo)
    OSM       = "osm"           # OpenStreetMap vía Overpass API (recorte por bbox del área de interés)
    COPERNICUS = "copernicus"   # DEM Copernicus GLO-30 (AWS S3, tiles 1°×1°) → pendiente calculada automáticamente
    MANUAL     = "manual"       # El usuario carga el archivo


class TipoExclusion(Enum):
    TRASLAPE = "traslape"       # Exclusión por superposición directa
    BUFFER   = "buffer"         # Exclusión por zona de amortiguamiento

    # Excluye lo que queda DEMASIADO LEJOS de la capa, no demasiado cerca.
    # Es el caso de la red vial: un sitio inaccesible para los camiones
    # recolectores se descarta aunque no incumpla ninguna distancia mínima.
    # La superficie excluida es el área de interés menos el buffer del umbral.
    LEJANIA = "lejania"

    # Excluye según el valor de un ráster continuo, no según geometría.
    # Es el caso de la pendiente: se descarta lo que supere el umbral.
    UMBRAL_RASTER = "umbral_raster"


class TipoScore(Enum):
    """Dirección del score en el análisis de ponderación."""
    MAYOR_ES_MEJOR = "mayor_es_mejor"
    MENOR_ES_MEJOR = "menor_es_mejor"  # menor distancia = mayor aptitud
    RANGO_OPTIMO   = "rango_optimo"    # existe un rango ideal


# ===========================================================================
# Dataclass principal
# ===========================================================================

@dataclass
class Criterio:
    """Representa un criterio de evaluación (exclusión y/o ponderación)."""

    # Identificación
    id: str
    nombre: str
    descripcion: str
    referencia_normativa: str = ""

    # Fuente de datos
    fuente: FuenteDatos = FuenteDatos.MANUAL
    descarga_url: Optional[str] = None      # URL directa de descarga alternativa

    # ── Exclusión dicotómica ────────────────────────────────────────────────
    es_exclusion: bool = True
    tipo_exclusion: TipoExclusion = TipoExclusion.BUFFER
    buffer_m: float = 0.0           # Distancia configurada por el usuario
    buffer_min_nom: float = 0.0     # Mínimo NOM-083 (sólo referencia)
    buffer_max_nom: Optional[float] = None

    # ── Ponderación (aptitud relativa) ────────────────────────────────────────────────
    es_ponderacion: bool = False
    tipo_score: TipoScore = TipoScore.MAYOR_ES_MEJOR
    peso: float = 0.0               # Peso asignado por el usuario (0–100)
    rango_optimo_min: Optional[float] = None
    rango_optimo_max: Optional[float] = None

    # ── Criterios de superficie continua (distancia o valor de ráster) ─────
    # Estos criterios no se evalúan por traslape: cada celda del territorio
    # tiene un valor —su distancia a la vía más cercana, su pendiente— y el
    # criterio se configura con números, no eligiendo un campo de atributos.
    #
    # umbral_exclusion: el límite que descarta cuando el rol es «excluyente».
    #   LEJANIA       → distancia máxima aceptable en metros (más lejos, fuera).
    #   UMBRAL_RASTER → valor máximo aceptable (p. ej. 25 % de pendiente).
    umbral_exclusion: Optional[float] = None

    # Escala continua para cuando el rol es «ponderado»: el puntaje interpola
    # linealmente entre el valor óptimo (1.0) y el peor (0.0). Se define así y
    # no al revés porque «óptimo» y «peor» se leen igual sin importar si el
    # criterio mejora al crecer o al decrecer: para vialidad el óptimo es 0 m
    # y el peor 5 000 m; para pendiente el óptimo podría ser 5 % y el peor 30 %.
    escala_optimo: Optional[float] = None
    escala_peor: Optional[float] = None

    # Unidad de los tres valores anteriores, para rotular la interfaz.
    unidad_umbral: str = "m"

    # ── Estado en tiempo de ejecución ──────────────────────────────────────
    activo: bool = True
    capa: Optional[object] = field(default=None, repr=False)  # QgsVectorLayer

    # Procedencia del dato cargado, para trazabilidad en el log y el reporte.
    #   ""           → sin cargar
    #   "automatica" → descargado por el plugin desde la fuente declarada
    #   "manual"     → archivo provisto por el usuario
    origen_carga: str = ""
    ruta_dato: Optional[str] = None      # ruta en disco de la capa cargada

    # Ráster continuo auxiliar. Algunos criterios entregan como capa principal
    # una vectorización (p. ej. pendiente → polígonos con pendiente > 25 %) pero
    # la ponderación por rango óptimo necesita los valores continuos originales.
    ruta_raster_continuo: Optional[str] = None

    # ── Clasificación normativa ─────────────────────────────────────────────
    # True  = requerido por NOM-083-SEMARNAT-2003 (debe incluirse en el análisis).
    # False = criterio adicional o personalizado fuera de la norma.
    # En la UI, los criterios obligatorios se muestran en la sección
    # "NOM-083" y los no-obligatorios en "Criterios personalizados".
    obligatorio: bool = True

    # True cuando el usuario definió el criterio desde la interfaz, en lugar de
    # venir con el plugin. Estos se pueden borrar y no traen descarga
    # automática: su capa la aporta el usuario.
    es_personalizado: bool = False

    # ── Mapeo de columna para capas cargadas manualmente ───────────────────
    # filtro_tipo: None = usar todas las geometrías (sin filtro de atributos)
    #              "continuo"   = filtrar por rango numérico
    #              "categorico" = filtrar por valor de cadena/código
    filtro_tipo: Optional[str] = None
    filtro_campo: Optional[str] = None          # nombre del campo a evaluar
    # Rango prohibitivo para variables continuas (ambos inclusive)
    filtro_continuo_min: Optional[float] = None
    filtro_continuo_max: Optional[float] = None
    # Mapa valor→etiqueta para variables categóricas
    # Ej.: {"alto": "prohibitivo", "medio": "permisivo", "bajo": "permisivo"}
    filtro_categorico: Optional[dict] = field(default=None, repr=False)

    # ── Guía de descarga manual ────────────────────────────────────────────────
    # Texto breve que se muestra en la UI cuando la fuente es MANUAL,
    # indicando al usuario cómo y dónde descargar el shapefile requerido.
    descarga_info: Optional[str] = None

    # ── Modo ponderado: rol del criterio ───────────────────────────────────────
    # Solo aplica cuando el usuario elige el modo de análisis "ponderado".
    # "excluyente" → máscara binaria: aptitud = 0 si el criterio no se cumple.
    # "ponderado"  → contribuye con peso y puntaje a la suma de aptitud.
    # En modo dicotómico, este campo se ignora.
    rol_ponderado: str = "excluyente"

    # ── Puntaje por valor categórico (modo ponderado) ──────────────────────────
    # Mapa valor→puntaje (0–100) para criterios ponderados con variable
    # categórica. 100 = máxima aptitud, 0 = mínima aptitud.
    # Configurado por el usuario en DialogoMapeoColumna.
    puntaje_categorico: Optional[dict] = field(default=None, repr=False)


# ===========================================================================
# URLs de descarga de datos geográficos por fuente
# ===========================================================================

# Los datos vectoriales provienen de la Descarga Masiva del INEGI
#   https://www.inegi.org.mx/app/descarga/
# y de las páginas temáticas citadas en cada criterio. El plugin consume copias
# alojadas en el bucket del proyecto para que la descarga sea reproducible.

_INEGI_DESCARGA   = "https://www.inegi.org.mx/app/descarga/"
_INEGI_HIDRO      = "https://www.inegi.org.mx/temas/hidrografia/"
_INEGI_USV        = "https://www.inegi.org.mx/temas/usosuelo/"
_INEGI_GEO        = "https://www.inegi.org.mx/temas/geologia/"
_INEGI_EDAFO      = "https://www.inegi.org.mx/temas/edafologia/"
_INEGI_MG         = "https://www.inegi.org.mx/temas/mg/"
_INEGI_VIALIDAD   = "https://www.inegi.org.mx/temas/vialidad/"
_INEGI_CONTINUO   = "https://www.inegi.org.mx/app/geo2/elevacionesmex/"

# Supabase Storage — bucket público "datos" del proyecto Evaluacion-SDF.
# Aloja los shapefiles estáticos que el plugin descarga automáticamente.
# Para actualizar un dataset: reemplaza el archivo en el bucket de Supabase
# (Storage → datos → Upload file) con el mismo nombre de archivo.
_SUPABASE = "https://uzqfdmotrurpzdnglzgf.supabase.co/storage/v1/object/public/datos"


# ===========================================================================
# Catálogo de criterios predeterminados
# ===========================================================================

CRITERIOS_DEFAULT: list[Criterio] = [

    # ── HIDROLOGÍA ────────────────────────────────────────────────────────────

    Criterio(
        id="corrientes_agua",
        nombre="Corrientes de agua (ríos y arroyos)",
        descripcion=(
            "Red hidrográfica superficial: ríos, arroyos y canales con caudal continuo. "
            "La NOM-083 § 6.1.6 establece 500 m mínimo respecto a cuerpos de agua "
            "superficiales con caudal continuo. "
            "Fuente: OpenStreetMap vía Overpass API — descarga automática recortada "
            "al bbox del área de interés (waterway: river, stream, canal, drain)."
        ),
        referencia_normativa="NOM-083-SEMARNAT-2003, § 6.1.6",
        fuente=FuenteDatos.OSM,
        descarga_url="https://overpass-api.de/api/interpreter",
        descarga_info=(
            "Si la descarga automática desde OpenStreetMap falla, obtenga la red "
            "hidrográfica de INEGI:\n"
            "  https://www.inegi.org.mx/temas/hidrografia/\n"
            "Descarga Masiva → Hidrografía → Red Hidrográfica 1:50 000 (líneas).\n"
            "Cargue el shapefile de corrientes de agua (ríos/arroyos)."
        ),
        es_exclusion=True,
        tipo_exclusion=TipoExclusion.BUFFER,
        buffer_m=500.0,
        buffer_min_nom=500.0,
        es_ponderacion=False,
    ),

    Criterio(
        id="cuerpos_agua",
        nombre="Cuerpos de agua (lagos y lagunas)",
        descripcion=(
            "Lagos, lagunas y embalses naturales. "
            "La NOM-083 § 6.1.6 establece 500 m mínimo respecto a cuerpos de agua "
            "superficiales con caudal continuo, lagos y lagunas. "
            "Fuente: OpenStreetMap vía Overpass API — descarga automática recortada "
            "al bbox del área de interés (natural=water, water=lake/pond/reservoir/lagoon)."
        ),
        referencia_normativa="NOM-083-SEMARNAT-2003, § 6.1.6",
        fuente=FuenteDatos.OSM,
        descarga_url="https://overpass-api.de/api/interpreter",
        descarga_info=(
            "Si la descarga automática desde OpenStreetMap falla, obtenga la capa "
            "de cuerpos de agua de INEGI:\n"
            "  https://www.inegi.org.mx/temas/hidrografia/\n"
            "Descarga Masiva → Hidrografía → Red Hidrográfica 1:50 000 (polígonos).\n"
            "Cargue el shapefile de cuerpos de agua (lagos, lagunas, embalses)."
        ),
        es_exclusion=True,
        tipo_exclusion=TipoExclusion.BUFFER,
        buffer_m=500.0,
        buffer_min_nom=500.0,
        es_ponderacion=False,
    ),

    # ── RIESGOS NATURALES ─────────────────────────────────────────────────────

    Criterio(
        id="zona_inundacion",
        nombre="Zona de inundación (Tr = 100 años)",
        descripcion=(
            "Zonas susceptibles a inundación con periodo de retorno de 100 años. "
            "La NOM-083 § 6.1.5 excluye sitios dentro de estas zonas salvo "
            "demostración técnica de no obstaculización del flujo ni deslaves.\n\n"
            "No existe actualmente una capa vectorial nacional de libre acceso para "
            "este criterio. El índice oficial (CENAPRED / Atlas Nacional de Riesgos, "
            "2016) se publica únicamente como ráster. El usuario debe cargar el "
            "shapefile correspondiente al municipio o zona de estudio."
        ),
        referencia_normativa="NOM-083-SEMARNAT-2003, § 6.1.5",
        fuente=FuenteDatos.MANUAL,
        descarga_url="http://www.atlasnacionalderiesgos.gob.mx/",
        descarga_info=(
            "No existe descarga vectorial nacional para la capa de inundación "
            "Tr=100 años. Opciones para obtener el shapefile:\n\n"
            "1. ATLAS NACIONAL DE RIESGOS — CENAPRED (verificación visual, ráster):\n"
            "   http://www.atlasnacionalderiesgos.gob.mx/\n"
            "   → Fenómenos Hidrometeorológicos\n"
            "   → Índice de peligro por inundación (Tr=100 años)\n\n"
            "2. ATLAS MUNICIPALES DE RIESGO (shapefile, si existe para el municipio):\n"
            "   Solicítelo al gobierno municipal o estatal de protección civil.\n"
            "   Algunos estados publican sus atlas en portales de datos abiertos.\n\n"
            "3. INEGI — Mapa Digital de México (zonas de inundación históricas):\n"
            "   https://www.inegi.org.mx/app/mapas/\n"
            "   Tema 'Hidrografía' → 'Áreas de inundación' (escala 1:50,000).\n\n"
            "Documente la fuente utilizada en el expediente técnico del proyecto."
        ),
        es_exclusion=True,
        tipo_exclusion=TipoExclusion.TRASLAPE,
        buffer_m=0.0,
        buffer_min_nom=0.0,
        es_ponderacion=False,
    ),

    # ── HUMEDALES ─────────────────────────────────────────────────────────────

    Criterio(
        id="zona_pantanosa",
        nombre="Marismas, manglares, esteros, pantanos y humedales",
        descripcion=(
            "Ecosistemas hídricos costeros e interiores: marismas, manglares, esteros, "
            "pantanos, humedales y estuarios. "
            "La NOM-083 § 6.1.4 prohíbe explícitamente la ubicación en estas zonas. "
            "Fuente: OpenStreetMap vía Overpass API — descarga automática recortada "
            "al bbox del área de interés (natural=wetland/marsh/mangrove)."
        ),
        referencia_normativa="NOM-083-SEMARNAT-2003, § 6.1.4; LGEEPA Art. 60 TER",
        fuente=FuenteDatos.INEGI_DL,
        descarga_url=f"{_SUPABASE}/humedales_inegi.zip",
        descarga_info=(
            "Si la descarga automática falla, obtenga la capa directamente de INEGI:\n"
            "  https://www.inegi.org.mx/temas/hidrologia/\n"
            "Descargue 'Conjunto de datos de humedales escala 1:50 000, cuencas\n"
            "hidrológicas prioritarias' (edición 2024, desglose Nacional).\n"
            "Este dataset incluye el campo de régimen de permanencia de agua,\n"
            "que permite distinguir humedales permanentes de temporales.\n"
            "Como alternativa ligera, OpenStreetMap cubre manglares y humedales\n"
            "principales (natural=wetland/marsh/mangrove) en la región de estudio."
        ),
        es_exclusion=True,
        tipo_exclusion=TipoExclusion.TRASLAPE,
        buffer_m=0.0,
        buffer_min_nom=0.0,
        es_ponderacion=False,
    ),

    # ── HIDROGEOLOGÍA ─────────────────────────────────────────────────────────

    Criterio(
        id="unidades_geohidrologicas",
        nombre="Zonas de recarga de acuíferos (CONAGUA)",
        descripcion=(
            "Polígonos de la delimitación oficial de acuíferos de México publicada por "
            "CONAGUA. Se usa como proxy conservador de zonas de recarga. "
            "La NOM-083 § 6.1.4 prohíbe la ubicación en estas zonas. "
            "Fuente: CONAGUA — descarga automática desde Supabase Storage "
            "(acuiferos_conagua.zip). Si el archivo no ha sido subido al bucket, "
            "el plugin solicita al usuario que lo cargue manualmente."
        ),
        referencia_normativa="NOM-083-SEMARNAT-2003, § 6.1.4; Ley de Aguas Nacionales",
        fuente=FuenteDatos.INEGI_DL,
        descarga_url=f"{_SUPABASE}/acuiferos_conagua.zip",
        descarga_info=(
            "Si la descarga automática falla, obtenga la capa oficial de acuíferos:\n"
            "  1. Visite https://datos.gob.mx/dataset/aguas_subterraneas\n"
            "  2. Descargue 'Delimitación de acuíferos (SHP)'.\n"
            "  3. (Opcional) Súbala al bucket de Supabase como 'acuiferos_conagua.zip'\n"
            "     para activar la descarga automática en futuras ejecuciones.\n"
            "Cargue el shapefile de acuíferos para la región de estudio."
        ),
        es_exclusion=True,
        tipo_exclusion=TipoExclusion.TRASLAPE,
        buffer_m=0.0,
        buffer_min_nom=0.0,
        es_ponderacion=False,
    ),

    Criterio(
        id="zona_veda",
        nombre="Pozos de extracción de agua (REPDA)",
        descripcion=(
            "Pozos de extracción de agua para uso doméstico, industrial, riego y ganadero "
            "(en operación y abandonados). "
            "La NOM-083 § 6.1.7 establece una distancia mínima de 500 m al pozo "
            "cuando no se puede determinar el cono de abatimiento."
        ),
        referencia_normativa="NOM-083-SEMARNAT-2003, § 6.1.7; Ley de Aguas Nacionales",
        fuente=FuenteDatos.MANUAL,
        descarga_url="https://www.gob.mx/conagua/documentos/acuiferos-de-mexico",
        descarga_info=(
            "Solicite el shapefile de pozos registrados a CONAGUA (REPDA — Registro Público "
            "de Derechos de Agua) o consulte el SINA: https://www.conagua.gob.mx/CONAGUA07/Contenido/Documentos/SINA.htm. "
            "Use las zonas de veda declaradas como polígono de exclusión."
        ),
        es_exclusion=True,
        tipo_exclusion=TipoExclusion.BUFFER,
        buffer_m=500.0,
        buffer_min_nom=500.0,
        es_ponderacion=False,
    ),

    # ── USO DE SUELO Y VEGETACIÓN ─────────────────────────────────────────────

    Criterio(
        id="uso_suelo_vegetacion",
        nombre="Uso de suelo y vegetación (Serie VII)",
        descripcion=(
            "Clasificación de cobertura del suelo a escala 1:250 000. "
            "Se excluyen polígonos con vegetación primaria (bosques, selvas, "
            "manglar, vegetación de galería). El resto participa en ponderación. "
            "Fuente: INEGI Serie VII — descarga automática desde Supabase Storage."
        ),
        referencia_normativa="NOM-083-SEMARNAT-2003; LGEEPA",
        obligatorio=False,   # § 5.x — fuera de §6.1 NOM-083
        fuente=FuenteDatos.INEGI_DL,
        descarga_url=f"{_SUPABASE}/uso_suelo_vegetacion_inegi.zip",
        descarga_info=(
            "Si la descarga automática falla, obtenga la capa de Uso de Suelo y Vegetación "
            "Serie VII de INEGI:\n"
            "  https://www.inegi.org.mx/temas/usosuelo/\n"
            "Descarga Masiva → Recursos Naturales → Uso del Suelo y Vegetación → Serie VII.\n"
            "El campo clave de clasificación es 'CVE_USV' o 'DESCRIP'.\n"
            "Nota: el shapefile nacional pesa ~430 MB; la descarga puede tardar varios minutos."
        ),
        es_exclusion=True,
        tipo_exclusion=TipoExclusion.TRASLAPE,
        buffer_m=0.0,
        buffer_min_nom=0.0,
        es_ponderacion=True,
        tipo_score=TipoScore.MAYOR_ES_MEJOR,
        peso=0.0,
    ),

    # ── GEOLOGÍA ──────────────────────────────────────────────────────────────

    Criterio(
        id="fallas_fracturas",
        nombre="Cavernas, fallas y fracturas geológicas",
        descripcion=(
            "Trazas de fallas activas, fracturas y zonas kársticas (cavernas). "
            "La NOM-083 § 6.1.4 prohíbe la ubicación sobre cavernas, fracturas "
            "o fallas geológicas — criterio de traslape directo. "
            "El usuario puede aumentar el buffer como margen de seguridad."
        ),
        referencia_normativa="NOM-083-SEMARNAT-2003, § 6.1.4",
        fuente=FuenteDatos.INEGI_DL,
        descarga_url=f"{_SUPABASE}/fallas_fracturas_inegi.zip",
        descarga_info=(
            "Si la descarga automática falla, obtenga la capa de INEGI:\n"
            "  https://www.inegi.org.mx/temas/geologia/\n"
            "Descargue 'Conjunto de datos vectoriales Geológicos. Continuo Nacional.\n"
            "Fallas fracturas' (escala 1:1 000 000, desglose Nacional, ~2.4 MB).\n"
            "Para mayor detalle, consulte el Servicio Geológico Mexicano (SGM):\n"
            "  https://mapserver.sgm.gob.mx/"
        ),
        es_exclusion=True,
        tipo_exclusion=TipoExclusion.TRASLAPE,
        buffer_m=0.0,
        buffer_min_nom=0.0,
        es_ponderacion=False,
    ),

    Criterio(
        id="edafologia",
        nombre="Tipo de suelo (edafología)",
        descripcion=(
            "Clasificación edafológica. "
            "La impermeabilidad natural del suelo determina el puntaje: "
            "suelos arcillosos o con baja permeabilidad = mayor aptitud."
        ),
        referencia_normativa="NOM-083-SEMARNAT-2003, § 5.2",
        fuente=FuenteDatos.MANUAL,
        descarga_url=_INEGI_EDAFO,
        descarga_info=(
            "Descargue la capa de Edafología de INEGI. "
            "En la Descarga Masiva: Recursos Naturales → Edafología 1:250 000. "
            "El campo de clasificación suele llamarse 'SIMBOLOGIA' o 'SUBUNIDAD'."
        ),
        es_exclusion=False,
        es_ponderacion=True,
        tipo_score=TipoScore.MAYOR_ES_MEJOR,
        peso=0.0,
        obligatorio=False,   # § 5.2 — fuera de §6.1 NOM-083
        rol_ponderado="ponderado",  # solo aporta puntaje, no excluye
    ),

    # ── ÁREAS NATURALES PROTEGIDAS (CONANP) ──────────────────────────────────

    Criterio(
        id="anp",
        nombre="Áreas Naturales Protegidas federales",
        descripcion=(
            "Polígonos de ANP federales decretadas. "
            "Fuente: CONANP — descarga automática desde Supabase Storage. "
            "La superposición directa excluye el sitio."
        ),
        referencia_normativa="LGEEPA Art. 44; NOM-083-SEMARNAT-2003",
        fuente=FuenteDatos.CONANP,
        descarga_url=f"{_SUPABASE}/anp_federal.zip",
        descarga_info=(
            "Si la descarga automática falla, obtenga el shapefile de ANP federales en:\n"
            "  https://sig.conanp.gob.mx/website/pagsig/info_gis.htm\n"
            "o en datos.gob.mx buscando 'áreas naturales protegidas federales CONANP'."
        ),
        es_exclusion=True,
        tipo_exclusion=TipoExclusion.TRASLAPE,
        buffer_m=0.0,
        buffer_min_nom=0.0,
        es_ponderacion=False,
    ),

    # ── ÁREAS NATURALES PROTEGIDAS SUBNACIONALES (CONABIO) ───────────────────

    Criterio(
        id="anp_estatales",
        nombre="ANP estatales, municipales, ejidales y privadas",
        descripcion=(
            "Polígonos de Áreas Naturales Protegidas de carácter estatal, municipal, "
            "ejidal, comunitario y privado. "
            "Fuente: CONABIO — descarga automática desde Supabase Storage. "
            "La superposición directa excluye el sitio conforme a la LGEEPA "
            "y legislación ambiental estatal aplicable."
        ),
        referencia_normativa="LGEEPA Art. 46 fracc. VIII–X; legislación estatal aplicable",
        fuente=FuenteDatos.CONABIO,
        descarga_url=f"{_SUPABASE}/anp_estatales.zip",
        descarga_info=(
            "Si la descarga automática falla, obtenga el shapefile de ANP estatales en:\n"
            "  http://www.conabio.gob.mx/informacion/gis/\n"
            "Busque 'anpest' en el catálogo de metadatos geográficos de CONABIO."
        ),
        es_exclusion=True,
        tipo_exclusion=TipoExclusion.TRASLAPE,
        buffer_m=0.0,
        buffer_min_nom=0.0,
        es_ponderacion=False,
    ),

    # ── ÁREAS DESTINADAS VOLUNTARIAMENTE A LA CONSERVACIÓN (ADVC) ────────────

    Criterio(
        id="advc",
        nombre="Áreas Destinadas Voluntariamente a la Conservación",
        descripcion=(
            "Polígonos de predios certificados ante la CONANP como ADVC vigentes. "
            "Las ADVC son áreas privadas, ejidales o comunales cuyos propietarios "
            "se comprometen voluntariamente a conservar la biodiversidad conforme "
            "a la LGEEPA Art. 55 BIS. Fuente: CONANP — descarga automática desde "
            "Supabase Storage. La superposición directa excluye el sitio."
        ),
        referencia_normativa="LGEEPA Art. 55 BIS; NOM-083-SEMARNAT-2003",
        fuente=FuenteDatos.CONANP,
        descarga_url=f"{_SUPABASE}/advc.zip",
        descarga_info=(
            "Si la descarga automática falla, obtenga el shapefile de ADVC vigentes en:\n"
            "  https://sig.conanp.gob.mx/website/pagsig/info_gis.htm\n"
            "o en datos.gob.mx buscando 'áreas destinadas voluntariamente conservación CONANP'.\n"
            "El archivo en Supabase se llama advc.zip."
        ),
        es_exclusion=True,
        tipo_exclusion=TipoExclusion.TRASLAPE,
        buffer_m=0.0,
        buffer_min_nom=0.0,
        es_ponderacion=False,
        obligatorio=False,   # LGEEPA Art. 55 BIS — fuera de §6.1 NOM-083
    ),

    # ── AEROPUERTOS ───────────────────────────────────────────────────────────

    Criterio(
        id="aeropuertos",
        nombre="Aeropuertos y aeródromos de servicio público",
        descripcion=(
            "Instalaciones aeroportuarias de servicio al público. "
            "La NOM-083 § 6.1.1 NO prohíbe de forma absoluta ubicarse a menos de "
            "13 km del centro de la(s) pista(s): establece que en ese caso la "
            "distancia elegida se determine mediante un estudio de riesgo aviario. "
            "Este análisis de tamizaje aplica el radio de 13 km como exclusión "
            "por criterio conservador. Un sitio descartado únicamente por este "
            "criterio puede ser viable si se respalda con el estudio de riesgo "
            "aviario correspondiente."
        ),
        referencia_normativa="NOM-083-SEMARNAT-2003, § 6.1.1",
        fuente=FuenteDatos.INEGI_DL,
        descarga_url=f"{_SUPABASE}/aeropuertos.zip",
        descarga_info=(
            "Si la descarga automática falla, obtenga el catálogo de aeródromos de la AFAC "
            "desde datos.gob.mx (buscar 'catálogo de aeródromos'), o use la capa de aeropuertos "
            "del INEGI (Marco Topográfico)."
        ),
        es_exclusion=True,
        tipo_exclusion=TipoExclusion.BUFFER,
        buffer_m=13000.0,
        buffer_min_nom=13000.0,
        es_ponderacion=False,
    ),

    # ── LOCALIDADES ───────────────────────────────────────────────────────────

    Criterio(
        id="localidades_urbanas",
        nombre="Localidades urbanas (≥ 2 500 hab)",
        descripcion=(
            "Polígonos de traza urbana de localidades con 2 500 o más habitantes. "
            "La NOM-083 § 6.1.3 establece que el límite del sitio debe estar a una "
            "distancia mínima de 500 m contados a partir del límite de la traza urbana "
            "existente o contemplada en el plan de desarrollo urbano. "
            "Fuente: localidades amanzanadas del Marco Geoestadístico Nacional (INEGI), "
            "filtradas con POBTOT ≥ 2 500 del Censo de Población y Vivienda 2020. "
            "Descarga automática desde Supabase Storage."
        ),
        referencia_normativa="NOM-083-SEMARNAT-2003, § 6.1.3",
        fuente=FuenteDatos.INEGI_DL,
        descarga_url=f"{_SUPABASE}/localidades_urbanas.zip",
        descarga_info=(
            "Si la descarga automática falla, genere el shapefile manualmente:\n"
            "  1. Descargue el Marco Geoestadístico Nacional de INEGI:\n"
            "     https://www.inegi.org.mx/temas/mg/\n"
            "  2. Descargue el ITER del Censo 2020 (CSV nacional):\n"
            "     https://www.inegi.org.mx/programas/ccpv/2020/#Microdatos\n"
            "  3. Una las tablas por CVE_GEO y filtre POBTOT ≥ 2 500.\n"
            "  4. Use el polígono de la traza urbana (localidades amanzanadas),\n"
            "     NO el punto centroide."
        ),
        es_exclusion=True,
        tipo_exclusion=TipoExclusion.BUFFER,
        buffer_m=500.0,
        buffer_min_nom=500.0,
        es_ponderacion=True,
        tipo_score=TipoScore.MAYOR_ES_MEJOR,
        peso=0.0,
    ),

    # ── ZONAS ARQUEOLÓGICAS (INAH) ────────────────────────────────────────────

    Criterio(
        id="zonas_arqueologicas",
        nombre="Zonas arqueológicas (INAH)",
        descripcion=(
            "Polígonos de zonas arqueológicas decretadas. "
            "La NOM-083 § 6.1.4 prohíbe explícitamente la ubicación "
            "en zonas arqueológicas."
        ),
        referencia_normativa="NOM-083-SEMARNAT-2003, § 6.1.4; Ley Federal sobre Monumentos y Zonas Arqueológicos",
        fuente=FuenteDatos.INEGI_DL,
        descarga_url=f"{_SUPABASE}/zonas_arqueologicas.zip",
        descarga_info=(
            "Si la descarga automática falla, obtenga el shapefile de zonas arqueológicas "
            "del INAH en datos.gob.mx (buscar 'zonas arqueológicas INAH'), o directamente "
            "en https://www.inah.gob.mx/zonas"
        ),
        es_exclusion=True,
        tipo_exclusion=TipoExclusion.BUFFER,
        buffer_m=200.0,
        buffer_min_nom=0.0,
        es_ponderacion=False,
    ),

    # ── PENDIENTE / TOPOGRAFÍA ────────────────────────────────────────────────

    Criterio(
        id="pendiente",
        nombre="Pendiente del terreno (%)",
        descripcion=(
            "Pendiente del terreno expresada en porcentaje. "
            "Exclusión: pendiente > 25 %. Aptitud óptima: 2–15 %. "
            "El plugin descarga el MDT Copernicus GLO-30 (30 m) para el área de "
            "interés, calcula la pendiente y vectoriza las zonas que superan el "
            "umbral. Los tiles quedan en caché, así que solo la primera "
            "ejecución descarga. Si prefiere otro MDT —por ejemplo el "
            "ContinuoMex del INEGI, de mayor resolución— puede cargarlo con "
            "«Reemplazar…»."
        ),
        referencia_normativa="NOM-083-SEMARNAT-2003, § 5.3",
        fuente=FuenteDatos.COPERNICUS,
        descarga_url=_INEGI_CONTINUO,
        descarga_info=(
            "Obtenga un Modelo Digital de Elevación (MDE) de alguna de estas fuentes:\n\n"
            "1. INEGI ContinuoMex (MDT de México, alta resolución):\n"
            "   https://www.inegi.org.mx/app/geo2/elevacionesmex/\n"
            "2. SRTM 1-Arc Second (NASA, 30 m global, requiere cuenta gratuita):\n"
            "   https://earthexplorer.usgs.gov/\n\n"
            "Cargue el MDE directamente: el plugin calcula la pendiente en\n"
            "porcentaje y vectoriza las zonas que superan el umbral.\n"
            "NO cargue un ráster de pendiente ya calculado — se le volvería a\n"
            "aplicar el cálculo de pendiente y el resultado no significaría nada.\n"
            "Formatos admitidos: .tif, .img, .asc, .vrt."
        ),
        es_exclusion=True,
        tipo_exclusion=TipoExclusion.UMBRAL_RASTER,
        buffer_m=0.0,
        buffer_min_nom=0.0,
        # Excluyente: se descarta lo que supere esta pendiente.
        umbral_exclusion=25.0,
        # Ponderado: 5 % de pendiente puntúa 1.0 y 30 % puntúa 0.0.
        escala_optimo=5.0,
        escala_peor=30.0,
        unidad_umbral="%",
        es_ponderacion=True,
        tipo_score=TipoScore.RANGO_OPTIMO,
        rango_optimo_min=2.0,
        rango_optimo_max=15.0,
        peso=0.0,
        obligatorio=False,   # § 5.3 — fuera de §6.1 NOM-083
    ),

    # ── RED VIAL ──────────────────────────────────────────────────────────────

    Criterio(
        id="vialidad",
        nombre="Red vial (accesibilidad)",
        descripcion=(
            "Carreteras y caminos dentro del área de interés. "
            "No es criterio de exclusión; sí de ponderación: "
            "mayor proximidad a vialidad pavimentada = mayor aptitud. "
            "Fuente: OpenStreetMap vía Overpass API — descarga automática "
            "recortada al bbox del área de interés."
        ),
        referencia_normativa="NOM-083-SEMARNAT-2003, § 5.4",
        fuente=FuenteDatos.OSM,
        descarga_url="https://overpass-api.de/api/interpreter",
        descarga_info=(
            "Si la descarga automática falla, descargue la Red Nacional de Caminos de INEGI:\n"
            "  https://www.inegi.org.mx/temas/vialidad/\n"
            "O exporte la capa 'red_vial' del archivo rnc2025.gpkg si ya lo tiene descargado."
        ),
        # Se evalúa por la distancia de cada celda a la vía más cercana, no por
        # traslape: un sitio no se descarta por tocar una carretera, se descarta
        # por quedar fuera del alcance de los camiones recolectores.
        es_exclusion=True,
        tipo_exclusion=TipoExclusion.LEJANIA,
        # Excluyente: se descarta lo que quede a más de esta distancia.
        umbral_exclusion=5000.0,
        # Ponderado: pegado a la vía puntúa 1.0; a 10 km puntúa 0.0.
        escala_optimo=0.0,
        escala_peor=10000.0,
        unidad_umbral="m",
        es_ponderacion=True,
        tipo_score=TipoScore.MENOR_ES_MEJOR,
        peso=0.0,
        obligatorio=False,   # § 5.4 — fuera de §6.1 NOM-083
        rol_ponderado="ponderado",  # por omisión solo aporta puntaje
    ),
]


# ===========================================================================
# Diccionario de acceso rápido por ID
# ===========================================================================

CRITERIOS: dict[str, Criterio] = {c.id: c for c in CRITERIOS_DEFAULT}


# ===========================================================================
# Referencia completa para ajuste manual si GetCapabilities devuelve nombres distintos


# ===========================================================================
# Clases de USV Serie V excluidas (campo CLAVE de la capa)
# ===========================================================================

USV_CLAVES_EXCLUIDAS = [
    "BQ", "BQP", "BA", "BJ", "BMM",   # Bosques
    "SAP", "SBC", "SBK", "SMC",        # Selvas
    "MG",                               # Manglar
    "PA",                               # Palmar natural
    "VG",                               # Vegetación de galería
    "TA", "TU", "VA",                   # Humedales / hidrófila
    "DV",                               # Vegetación de dunas
]

# ===========================================================================
# Puntaje edafológico de aptitud (impermeabilidad natural)
# Basado en clasificación FAO-UNESCO; campo típico: SIMBOLOGIA o SUBUNIDAD
# ===========================================================================

PUNTAJE_EDAFOLOGIA: dict[str, int] = {
    "Lc": 9, "Lf": 9,   # Luvisol (alta retención)
    "Ve": 9, "Vp": 8,   # Vertisol (arcilloso, expansivo)
    "Rc": 7, "Re": 6,   # Regosol
    "Bc": 7, "Bh": 6,   # Cambisol
    "Ge": 5,             # Gleysol
    "Zo": 4, "Zg": 3,   # Solonchak
    "Qc": 3, "Qa": 2,   # Arenosol (muy permeable)
    "Fl": 2,             # Fluvisol
    "Hh": 1,             # Histosol (orgánico)
}
