# SDF-RSU — Evaluación RSU

**Plug-in de QGIS para el análisis de aptitud del suelo en sitios de disposición final de residuos sólidos urbanos en México**, conforme a la **NOM-083-SEMARNAT-2003**.

[![QGIS](https://img.shields.io/badge/QGIS-3.16%2B-589632)](https://qgis.org)
[![Licencia](https://img.shields.io/badge/licencia-GPL--3.0--or--later-blue)](LICENSE)
[![Pruebas](https://img.shields.io/badge/pruebas-145%20comprobaciones-success)](pruebas/)

El plugin responde a una pregunta concreta: *dada un área de estudio, ¿qué superficie queda descartada por las restricciones de ubicación de la norma, y cuál de la restante es más apta?* Descarga los datos oficiales, aplica las distancias del § 6.1 de la **NOM-083-SEMARNAT-2003** y entrega capas de zonas prohibidas y permitidas junto con un reporte que documenta cada decisión.

> **Versión experimental.** Marque «Mostrar complementos experimentales» en el administrador de complementos de QGIS para instalarlo desde el repositorio oficial. Los reportes de error son bienvenidos.

---

## Qué hace

El análisis se organiza en dos etapas.

**Exclusión.** Cada criterio activo produce una geometría prohibitiva —por traslape directo o por una zona de amortiguamiento— que se une con las demás y se resta del área de interés. El resultado son dos capas vectoriales: la superficie descartada y la que no incumple ningún criterio evaluado.

**Ponderación** (opcional). Sobre la superficie permitida, los criterios con rol *ponderado* aportan un puntaje con el peso que el usuario les asigne. La suma debe ser 100 %. El resultado es un ráster de aptitud relativa de 0 a 1.

En modo **dicotómico** solo corre la primera etapa y el resultado es binario: prohibido o permitido. En modo **ponderado**, cada criterio se declara excluyente —máscara binaria— o ponderado —aporta puntaje.

## Criterios

Los criterios marcados **NOM-083** provienen de las restricciones de ubicación del § 6.1 de la norma, que aplican a cualquier sitio de disposición final sea tipo A, B, C o D.

| Criterio | Referencia | Regla predeterminada | Fuente |
|---|---|---|---|
| Corrientes de agua (ríos y arroyos) | § 6.1.6 | buffer 500 m | OpenStreetMap |
| Cuerpos de agua (lagos y lagunas) | § 6.1.6 | buffer 500 m | OpenStreetMap |
| Zona de inundación (Tr = 100 años) | § 6.1.5 | traslape | Manual |
| Marismas, manglares, esteros y humedales | § 6.1.4 | traslape | INEGI |
| Zonas de recarga de acuíferos | § 6.1.4 | traslape | INEGI / CONAGUA |
| Pozos de extracción de agua | § 6.1.7 | buffer 500 m | Manual (REPDA) |
| Cavernas, fallas y fracturas geológicas | § 6.1.4 | traslape | INEGI |
| Áreas Naturales Protegidas federales | § 6.1.2 · LGEEPA 44 | traslape | CONANP |
| ANP estatales, municipales, ejidales y privadas | LGEEPA 46 | traslape | CONABIO |
| Aeropuertos y aeródromos de servicio público | § 6.1.1 | buffer 13 000 m | INEGI |
| Localidades urbanas (≥ 2 500 hab) | § 6.1.3 | buffer 500 m | INEGI |
| Zonas arqueológicas | § 6.1.4 | buffer 200 m | INAH |

Criterios adicionales, fuera del § 6.1: uso de suelo y vegetación (Serie VII), tipo de suelo (edafología) y Áreas Destinadas Voluntariamente a la Conservación.

### Criterios de superficie continua

La **pendiente del terreno** y la **red vial** no se evalúan por traslape: cada punto del territorio tiene un valor —su pendiente, su distancia a la vía más cercana— y el criterio se resuelve comparándolo contra un umbral.

| Criterio | Como excluyente | Como ponderado |
|---|---|---|
| Pendiente del terreno | Descarta lo que supere la pendiente máxima (25 % por omisión) | Escala continua entre un valor óptimo y uno peor |
| Red vial (accesibilidad) | Descarta lo que quede a más de la distancia máxima (5 000 m por omisión) | Escala continua entre un valor óptimo y uno peor |

En el caso de la red vial lo prohibitivo es la **lejanía**, no la cercanía: un sitio no se descarta por estar junto a una carretera sino por quedar fuera del alcance de los camiones recolectores.

### Sobre el buffer de aeropuertos

El § 6.1.1 **no prohíbe** ubicarse a menos de 13 km del centro de las pistas: establece que en ese caso la distancia debe determinarse mediante un estudio de riesgo aviario. El plugin aplica los 13 000 m como exclusión por criterio conservador. Un sitio descartado únicamente por este criterio puede ser viable si se respalda con el estudio correspondiente.

## Instalación

**Desde el repositorio oficial de QGIS** — Complementos → Administrar e instalar complementos → activar «Mostrar complementos experimentales» en Configuración → buscar «Evaluación RSU».

**Desde un archivo ZIP** — descargue el ZIP de la [última versión](../../releases/latest) y use Complementos → Administrar e instalar complementos → Instalar a partir de ZIP.

Si actualiza sobre una instalación previa, **reinicie QGIS**: el módulo puede quedar en memoria y seguir ejecutando la versión anterior. El registro del análisis imprime la versión en su primera línea, de modo que puede confirmar cuál está corriendo.

**Requisitos:** QGIS 3.16 o posterior y conexión a internet para la descarga automática. No requiere paquetes de Python adicionales.

## Uso

El asistente tiene siete pasos.

1. **Área de interés** — seleccione una capa de polígonos, dibújela sobre el mapa o cárguela desde un archivo. Defina el directorio de trabajo.
2. **Criterios** — elija el modo de análisis y qué criterios incluir.
3. **Datos** — descargue los datos automáticos o cargue archivos propios. Cada fila muestra su fuente y estado.
4. **Exclusión** — ajuste las distancias. Los valores predeterminados son los mínimos de la norma; modificarlos es responsabilidad del usuario.
5. **Ponderación** — asigne pesos a los criterios de aptitud, si usa ese modo.
6. **Configuración por criterio** — para capas vectoriales, el mapeo de atributos: por omisión toda la geometría es prohibitiva, y solo hace falta configurarlo si necesita filtrar por los valores de un campo. Para pendiente y red vial, el umbral que descarta o la escala de puntaje. El rol de cada criterio se decide en el Paso 4 y aquí solo se consulta.
7. **Resultados** — ejecute el análisis. El registro detalla cada criterio y el resumen final presenta el balance de superficies y la procedencia de los datos.

### Formatos de archivo admitidos

| Tipo | Formatos |
|---|---|
| Vectorial | `.shp` · `.gpkg` · `.geojson` · `.kml` |
| Ráster | `.tif` · `.img` · `.asc` · `.vrt` |

Para el criterio de **pendiente**, cargue un modelo digital de elevación (MDE): el plugin calcula la pendiente en porcentaje y vectoriza las zonas que superan el umbral. No cargue un ráster de pendiente ya calculado.

### Archivos que genera

```
<directorio de trabajo>/
├── datos_evaluacion_rsu/     descargas y caché de tiles del MDT
└── resultados_rsu/
    ├── zona_excluida.gpkg    superficie descartada
    ├── zona_apta.gpkg        superficie permitida
    ├── excl_<criterio>.gpkg  aporte individual de cada criterio
    └── aptitud_final.tif     ráster de aptitud (solo modo ponderado)
```

Las capas se añaden al proyecto con simbología aplicada. El reporte HTML se exporta desde el paso 7.

## Fuentes de datos

| Fuente | Contenido |
|---|---|
| INEGI | Marco Geoestadístico, hidrografía, geología, uso de suelo, edafología |
| CONANP | Áreas Naturales Protegidas federales y ADVC |
| CONABIO | Inventario de ANP estatales, municipales, ejidales y privadas |
| OpenStreetMap | Hidrografía y red vial, vía Overpass API |
| Copernicus | Modelo digital de terreno GLO-30, para derivar la pendiente |

Los conjuntos nacionales se consumen desde copias alojadas por el proyecto para que la descarga sea reproducible y no dependa de la disponibilidad de cada portal. Las fuentes originales se citan en la descripción de cada criterio.

El acceso a red usa `QgsNetworkAccessManager`, por lo que respeta el proxy, la autenticación y los certificados configurados en QGIS → Preferencias → Opciones → Red. Si trabaja detrás de un proxy institucional, configúrelo ahí.

## Interpretación de los resultados

El plugin es una herramienta de **tamizaje**, no un dictamen. Delimita la superficie que la norma descarta y ordena el resto por aptitud relativa, lo que reduce el universo de búsqueda antes del trabajo de campo. No sustituye los estudios previos del § 6.2 ni las especificaciones de diseño del § 7, que dependen de la categoría por tonelaje de la Tabla 1.

El registro del análisis indica, para cada criterio, el CRS de la capa, cuántos elementos se consideraron y qué superficie aportó. **Conviene revisarlo**: un criterio que reporta cero elementos en el entorno del área puede significar que la capa no cubre esa región, no que no haya restricciones ahí.

## Desarrollo

### Estructura

```
SDF-RSU/
├── evaluacion_rsu/        el plugin (lo que se empaqueta y se instala)
│   ├── core/              motor de análisis, criterios, descargas, red
│   ├── gui/               asistente de siete pasos
│   └── utils/             estilos y reportes
└── pruebas/               conjunto de pruebas automatizadas
```

### Empaquetar

El ZIP instalable contiene únicamente la carpeta `evaluacion_rsu/`, sin `__pycache__` ni archivos `.pyc`. `test_metadata.py` lo construye y lo valida como parte de la suite.

## Contribuir

Los reportes de error y las propuestas se reciben en los [issues](../../issues). Para un reporte útil conviene incluir:

- la versión que imprime el registro en su primera línea,
- el texto del registro completo,
- y, si es posible, el área de interés empleada.

## Cita

Si usa el plugin en un trabajo académico, el archivo [`CITATION.cff`](CITATION.cff) contiene la referencia en formato APA y BibTeX. El botón «Copiar referencia bibliográfica» del asistente la copia al portapapeles.

## Licencia

**GPL-3.0-or-later.** Véase [`LICENSE`](LICENSE).

Los datos descargados conservan la licencia de su fuente original: los del INEGI se rigen por sus [términos de libre uso](https://www.inegi.org.mx/inegi/terminos.html) y los de OpenStreetMap por la [ODbL](https://www.openstreetmap.org/copyright).

## Autor

Sergio López Olvera — <lopezolverasergio@gmail.com>
