# -*- coding: utf-8 -*-
# EvaluaciónRSU — Plugin QGIS para la selección de sitios de disposición final de RSU
# Copyright (C) 2024-2025  Sergio López Olvera <lopezolverasergio@gmail.com>
# SPDX-License-Identifier: GPL-3.0-or-later
# Repository: https://github.com/sergio-lopez-olvera/evaluacion-rsu

"""
Acceso a red mediante la pila de QGIS.

Todas las peticiones del plugin pasan por ``QgsNetworkAccessManager``, nunca por
``urllib`` ni ``requests``. La razón no es solo cumplir con los lineamientos de
publicación de plugins de QGIS: ``urllib`` ignora por completo la configuración
de proxy del programa, de modo que en cualquier institución con proxy corporativo
—dependencias federales, estatales o municipales— las descargas fallarían sin
explicación aparente. ``QgsNetworkAccessManager`` hereda proxy, autenticación,
certificados y encabezados configurados por el usuario en QGIS.

Funciones expuestas
-------------------
obtener(url, ...)            → bytes de una petición GET
publicar(url, datos, ...)    → bytes de una petición POST (formulario)
descargar_archivo(url, ...)  → guarda la respuesta en disco, con progreso
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

# QgsBlockingNetworkRequest y QgsFileDownloader se apoyan internamente en
# QgsNetworkAccessManager, de modo que heredan la configuración de red de QGIS.
from qgis.core import QgsBlockingNetworkRequest, QgsFileDownloader
from qgis.PyQt.QtCore import QEventLoop, QTimer, QUrl
from qgis.PyQt.QtNetwork import QNetworkRequest


AGENTE_USUARIO = "EvaluacionRSU-QGIS-Plugin"
TIEMPO_ESPERA_MS = 120_000          # 120 s para servicios lentos del sector público


class ErrorRed(Exception):
    """Falla de red: sin conexión, host inalcanzable, HTTP de error o tiempo agotado."""


# ---------------------------------------------------------------------------
# Utilidades internas
# ---------------------------------------------------------------------------

def _peticion(url: str) -> QNetworkRequest:
    req = QNetworkRequest(QUrl(url))
    req.setRawHeader(b"User-Agent", AGENTE_USUARIO.encode())
    # Seguir redirecciones: varios portales gubernamentales redirigen a un CDN.
    try:
        req.setAttribute(
            QNetworkRequest.RedirectPolicyAttribute,
            QNetworkRequest.NoLessSafeRedirectPolicy,
        )
    except AttributeError:          # Qt antiguo: atributo no disponible
        pass
    return req


def _mensaje_error(nam: QgsBlockingNetworkRequest, url: str) -> str:
    """Compone un mensaje de error legible a partir de la respuesta."""
    partes = []
    try:
        err = nam.errorMessage()
        if err:
            partes.append(err)
    except Exception:  # noqa: BLE001
        pass
    try:
        codigo = nam.reply().attribute(QNetworkRequest.HttpStatusCodeAttribute)
        if codigo:
            partes.append(f"HTTP {codigo}")
    except Exception:  # noqa: BLE001
        pass
    detalle = " — ".join(partes) if partes else "causa no reportada"
    return f"{detalle}\n  URL: {url}"


def _verificar(resultado, nam: QgsBlockingNetworkRequest, url: str) -> None:
    if resultado != QgsBlockingNetworkRequest.NoError:
        raise ErrorRed(_mensaje_error(nam, url))


# ---------------------------------------------------------------------------
# Peticiones síncronas
# ---------------------------------------------------------------------------

def obtener(url: str, tiempo_espera_ms: int = TIEMPO_ESPERA_MS) -> bytes:
    """GET que devuelve el cuerpo de la respuesta como bytes.

    Corre de forma bloqueante, pensado para usarse dentro de un QThread.
    """
    nam = QgsBlockingNetworkRequest()
    try:
        nam.setTimeout(tiempo_espera_ms)
    except AttributeError:          # setTimeout no existe en versiones antiguas
        pass
    resultado = nam.get(_peticion(url), forceRefresh=True)
    _verificar(resultado, nam, url)
    return bytes(nam.reply().content())


def publicar(
    url: str,
    datos: bytes,
    tipo_contenido: str = "application/x-www-form-urlencoded",
    tiempo_espera_ms: int = TIEMPO_ESPERA_MS,
) -> bytes:
    """POST de un cuerpo ya codificado; devuelve la respuesta como bytes."""
    req = _peticion(url)
    req.setHeader(QNetworkRequest.ContentTypeHeader, tipo_contenido)

    nam = QgsBlockingNetworkRequest()
    try:
        nam.setTimeout(tiempo_espera_ms)
    except AttributeError:
        pass
    resultado = nam.post(req, datos, forceRefresh=True)
    _verificar(resultado, nam, url)
    return bytes(nam.reply().content())


# ---------------------------------------------------------------------------
# Descarga de archivos
# ---------------------------------------------------------------------------

def descargar_archivo(
    url: str,
    destino: "str | Path",
    al_progresar: Optional[Callable[[int, int], None]] = None,
    tiempo_espera_ms: int = TIEMPO_ESPERA_MS,
) -> Path:
    """Descarga ``url`` a ``destino`` informando el avance.

    Usa ``QgsFileDownloader``, que escribe en disco por bloques en lugar de
    mantener el archivo completo en memoria: los ZIP del INEGI y los tiles del
    MDT pesan cientos de megabytes.

    ``al_progresar`` recibe (bytes_recibidos, bytes_totales); ``bytes_totales``
    es -1 mientras el servidor no informe el tamaño.
    """
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)

    # Descargar a un temporal y mover al final, para no dejar un archivo
    # truncado en la ruta definitiva si la descarga se interrumpe.
    temporal = destino.with_suffix(destino.suffix + ".parcial")
    if temporal.exists():
        temporal.unlink()

    bucle = QEventLoop()
    estado: dict = {"error": None}

    descargador = QgsFileDownloader(
        QUrl(url), str(temporal), delayStart=True
    )

    def _en_error(mensajes):
        texto = "; ".join(mensajes) if isinstance(mensajes, (list, tuple)) else str(mensajes)
        estado["error"] = texto or "error no especificado"

    def _en_progreso(recibido, total):
        if al_progresar is not None:
            al_progresar(int(recibido), int(total))

    descargador.downloadError.connect(_en_error)
    descargador.downloadProgress.connect(_en_progreso)
    descargador.downloadExited.connect(bucle.quit)

    # Tiempo máximo global: evita que un servidor que acepta la conexión pero
    # nunca responde deje el análisis colgado indefinidamente.
    vigilante = QTimer()
    vigilante.setSingleShot(True)

    def _agotado():
        estado["error"] = (
            f"La descarga excedió el tiempo límite de {tiempo_espera_ms // 1000} s"
        )
        descargador.cancelDownload()

    vigilante.timeout.connect(_agotado)
    vigilante.start(tiempo_espera_ms)

    descargador.startDownload()
    bucle.exec_()
    vigilante.stop()

    if estado["error"]:
        if temporal.exists():
            temporal.unlink()
        raise ErrorRed(f"{estado['error']}\n  URL: {url}")

    if not temporal.exists() or temporal.stat().st_size == 0:
        if temporal.exists():
            temporal.unlink()
        raise ErrorRed(f"La descarga quedó vacía.\n  URL: {url}")

    if destino.exists():
        destino.unlink()
    temporal.replace(destino)
    return destino


def hay_conexion(url_prueba: str = "https://www.inegi.org.mx") -> bool:
    """Comprueba conectividad de salida respetando el proxy de QGIS."""
    try:
        obtener(url_prueba, tiempo_espera_ms=10_000)
        return True
    except Exception:  # noqa: BLE001
        return False
