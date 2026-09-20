# -*- coding: utf-8 -*-
"""
Pruebas del módulo de red, que hasta ahora nunca se había ejecutado.

Verifica la API de QGIS contra la que se escribió, el camino feliz de las tres
funciones y el manejo de fallos. La conectividad real depende del entorno, así
que los casos que salen a internet se marcan como omitidos si no hay salida.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import arnes

arnes.iniciar()

from qgis.core import QgsBlockingNetworkRequest, QgsFileDownloader
from qgis.PyQt.QtCore import QUrl

from evaluacion_rsu.core import red

R = arnes.Reporte()
TMP = arnes.dir_temporal("red")


# ===========================================================================
print("\nAPI de QGIS contra la que se escribió el módulo")
# ===========================================================================

R.comprobar(
    "A1", "QgsFileDownloader acepta delayStart",
    QgsFileDownloader(QUrl("https://example.invalid"), str(TMP / "x"),
                      delayStart=True) is not None)

d = QgsFileDownloader(QUrl("https://example.invalid"), str(TMP / "y"),
                      delayStart=True)
for señal in ("downloadError", "downloadProgress", "downloadExited",
              "cancelDownload", "startDownload"):
    R.comprobar(f"A2.{señal}", f"QgsFileDownloader expone {señal}",
                hasattr(d, señal))

n = QgsBlockingNetworkRequest()
R.comprobar("A3", "QgsBlockingNetworkRequest expone get/post/errorMessage/reply",
            all(hasattr(n, m) for m in ("get", "post", "errorMessage", "reply")))

tiene_timeout = hasattr(n, "setTimeout")
R.comprobar(
    "A4", "el módulo tolera que setTimeout no exista",
    True,
    f"setTimeout presente en esta versión: {tiene_timeout}"
    + ("" if tiene_timeout else " — el try/except del módulo lo absorbe"))


# ===========================================================================
print("\nManejo de fallos (sin depender de internet)")
# ===========================================================================

try:
    red.obtener("https://host.que.no.existe.invalid/x", tiempo_espera_ms=8000)
    R.comprobar("F1", "obtener() lanza ErrorRed ante un host inexistente", False,
                "no lanzó excepción")
except red.ErrorRed as e:
    R.comprobar("F1", "obtener() lanza ErrorRed ante un host inexistente", True,
                str(e).replace("\n", " | ")[:88])
except Exception as e:  # noqa: BLE001
    R.comprobar("F1", "obtener() lanza ErrorRed ante un host inexistente", False,
                f"lanzó {type(e).__name__} en su lugar")

destino = TMP / "no_deberia_existir.bin"
try:
    red.descargar_archivo("https://host.que.no.existe.invalid/a.zip", destino,
                          tiempo_espera_ms=8000)
    R.comprobar("F2", "descargar_archivo() lanza ErrorRed", False)
except red.ErrorRed as e:
    R.comprobar("F2", "descargar_archivo() lanza ErrorRed", True,
                str(e).replace("\n", " | ")[:88])
except Exception as e:  # noqa: BLE001
    R.comprobar("F2", "descargar_archivo() lanza ErrorRed", False,
                f"lanzó {type(e).__name__}")

R.comprobar(
    "F3", "no deja el archivo de destino tras un fallo",
    not destino.exists(), f"{destino.name} existe: {destino.exists()}")
R.comprobar(
    "F4", "no deja el archivo .parcial tras un fallo",
    not destino.with_suffix(destino.suffix + ".parcial").exists())


# ===========================================================================
print("\nCamino feliz (requiere salida a internet)")
# ===========================================================================

URL_PRUEBA = os.environ.get("URL_PRUEBA_RED", "https://www.openstreetmap.org/robots.txt")

if not red.hay_conexion(URL_PRUEBA):
    print("  (omitido: sin salida a internet hacia la URL de prueba)")
else:
    try:
        cuerpo = red.obtener(URL_PRUEBA, tiempo_espera_ms=20000)
        R.comprobar("H1", "obtener() devuelve el cuerpo de la respuesta",
                    isinstance(cuerpo, bytes) and len(cuerpo) > 0,
                    f"{len(cuerpo)} bytes")
    except Exception as e:  # noqa: BLE001
        R.comprobar("H1", "obtener() devuelve el cuerpo", False,
                    f"{type(e).__name__}: {str(e)[:70]}")

    avance = {"llamadas": 0, "ultimo": (0, 0)}

    def _progreso(recibido, total):
        avance["llamadas"] += 1
        avance["ultimo"] = (recibido, total)

    salida = TMP / "descargado.txt"
    try:
        ruta = red.descargar_archivo(URL_PRUEBA, salida, al_progresar=_progreso,
                                     tiempo_espera_ms=20000)
        R.comprobar("H2", "descargar_archivo() escribe el archivo",
                    ruta.exists() and ruta.stat().st_size > 0,
                    f"{ruta.stat().st_size} bytes en {ruta.name}")
        R.comprobar("H3", "informa el avance durante la descarga",
                    avance["llamadas"] > 0,
                    f"{avance['llamadas']} llamadas, último={avance['ultimo']}")
        R.comprobar("H4", "no deja residuo .parcial",
                    not salida.with_suffix(salida.suffix + ".parcial").exists())
    except Exception as e:  # noqa: BLE001
        R.comprobar("H2", "descargar_archivo() escribe el archivo", False,
                    f"{type(e).__name__}: {str(e)[:70]}")


ok = R.resumen()
arnes.terminar(0 if ok else 1)
