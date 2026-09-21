# -*- coding: utf-8 -*-
"""
Validación de metadata.txt y del paquete, contra lo que plugins.qgis.org exige.

Existe por un error propio: una edición del changelog dejó una línea sin
sangría, ConfigParser abortó el archivo entero y dos versiones empaquetadas no
se habrían podido instalar. El ZIP se veía perfectamente normal.

No necesita QGIS: se corre con cualquier Python.
"""

from __future__ import annotations

import configparser
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import arnes

RAIZ = Path(__file__).resolve().parent.parent
META = RAIZ / "evaluacion_rsu" / "metadata.txt"

R = arnes.Reporte()


# ===========================================================================
print("\nmetadata.txt se puede leer")
# ===========================================================================

cfg = configparser.ConfigParser()
try:
    cfg.read(META, encoding="utf-8")
    parsea, detalle = True, f"{len(cfg['general'])} claves en [general]"
except Exception as exc:  # noqa: BLE001
    parsea, detalle = False, f"{type(exc).__name__}: {str(exc).splitlines()[-1].strip()}"

R.comprobar(
    "D1", "ConfigParser lee el archivo — si falla, el plugin no instala",
    parsea, detalle)

if not parsea:
    R.resumen()
    arnes.terminar(1)

g = cfg["general"]

# Toda línea de un valor multilínea debe ir sangrada: una sola línea en la
# columna cero se interpreta como clave nueva y rompe el archivo entero.
crudo = META.read_text(encoding="utf-8").splitlines()
sin_sangrar = [
    (n + 1, l) for n, l in enumerate(crudo)
    if l.strip() and not l[0].isspace() and "=" not in l and not l.startswith("[")
]
# El signo «%» es el marcador de interpolación de ConfigParser: uno suelto
# hace fallar la lectura del archivo entero, igual que una línea sin sangrar.
# Escribir «por ciento» evita depender de si quien lee usa raw=True.
por_cientos = [(n + 1, l) for n, l in enumerate(crudo)
               if "%" in l and "%%" not in l]
R.comprobar(
    "D3", "no hay signos de porcentaje sueltos",
    not por_cientos,
    "; ".join(f"línea {n}: {l.strip()[:45]!r}" for n, l in por_cientos[:3])
    or "ninguno")

R.comprobar(
    "D2", "ninguna línea de continuación quedó sin sangrar",
    not sin_sangrar,
    "; ".join(f"línea {n}: {l[:45]!r}" for n, l in sin_sangrar[:3]) or "todas correctas")


# ===========================================================================
print("\nCampos que el revisor comprueba")
# ===========================================================================

for campo in ("name", "qgisMinimumVersion", "description", "version",
              "author", "email", "about", "repository"):
    R.comprobar(
        f"C.{campo}", f"{campo} está presente y no vacío",
        bool(g.get(campo, "").strip()),
        (g.get(campo, "") or "").strip().splitlines()[0][:60] if g.get(campo) else "VACÍO")

# Los tres enlaces se verifican a mano durante la aprobación.
for campo in ("repository", "tracker", "homepage"):
    valor = (g.get(campo, "") or "").strip()
    R.comprobar(
        f"L.{campo}", f"{campo} es una URL https y no un archivo ZIP",
        valor.startswith("https://") and not valor.endswith(".zip"),
        valor or "VACÍO")

CATEGORIAS = {"Raster", "Vector", "Database", "Mesh", "Plugins"}
R.comprobar(
    "C.category", "category es uno de los valores que acepta el repositorio",
    g.get("category", "").strip() in CATEGORIAS,
    f"category={g.get('category', '')!r}; admitidas: {sorted(CATEGORIAS)}")

for campo in ("experimental", "deprecated", "server"):
    R.comprobar(
        f"B.{campo}", f"{campo} es True o False",
        g.get(campo, "").strip() in ("True", "False"),
        f"{campo}={g.get(campo, '')!r}")

version = g.get("version", "").strip()
R.comprobar(
    "C.version", "version tiene forma X.Y.Z",
    len(version.split(".")) == 3 and all(p.isdigit() for p in version.split(".")),
    f"version={version!r}")

R.comprobar(
    "C.changelog", "el changelog menciona la versión actual",
    version in g.get("changelog", ""),
    f"buscando {version!r} en el changelog")

icono = (g.get("icon", "") or "").strip()
R.comprobar(
    "C.icon", "el archivo de icono existe en el paquete",
    bool(icono) and (RAIZ / "evaluacion_rsu" / icono).exists(),
    f"icon={icono!r}")


# ===========================================================================
print("\nLa versión que el registro imprime coincide con metadata.txt")
# ===========================================================================

try:
    texto_analisis = (RAIZ / "evaluacion_rsu" / "core" / "analisis.py").read_text(
        encoding="utf-8")
    lee_metadata = "_leer_version" in texto_analisis and "metadata.txt" in texto_analisis
except Exception:  # noqa: BLE001
    lee_metadata = False

R.comprobar(
    "V1", "el motor toma su número de versión de metadata.txt, no de una copia",
    lee_metadata,
    "analisis.py lee metadata.txt en _leer_version()")


# ===========================================================================
print("\nEl paquete cumple los límites del repositorio")
# ===========================================================================

LIMITE_MB = 20

def construir_zip(destino: Path) -> Path:
    with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted((RAIZ / "evaluacion_rsu").rglob("*")):
            if not f.is_file():
                continue
            rel = f.relative_to(RAIZ)
            if "__pycache__" in rel.parts or f.suffix == ".pyc":
                continue
            zf.write(f, str(rel))
    return destino

tmp = arnes.dir_temporal("paquete")
zip_prueba = construir_zip(tmp / "evaluacion_rsu.zip")
mb = zip_prueba.stat().st_size / 1_048_576

R.comprobar(
    "Z1", f"el paquete pesa menos de {LIMITE_MB} MB",
    mb < LIMITE_MB, f"{mb:.2f} MB")

with zipfile.ZipFile(zip_prueba) as zf:
    nombres = zf.namelist()

R.comprobar(
    "Z2", "todo cuelga de una sola carpeta con el nombre del módulo",
    all(n.startswith("evaluacion_rsu/") for n in nombres),
    f"{len(nombres)} archivos; raíces = "
    f"{sorted({n.split('/')[0] for n in nombres})}")

R.comprobar(
    "Z3", "metadata.txt está en la raíz de esa carpeta",
    "evaluacion_rsu/metadata.txt" in nombres)

R.comprobar(
    "Z4", "no se cuela ningún .pyc ni __pycache__",
    not [n for n in nombres if n.endswith(".pyc") or "__pycache__" in n],
    f"{len([n for n in nombres if n.endswith('.pyc')])} archivos .pyc")

# El repositorio no admite binarios compilados.
BINARIOS = (".so", ".dll", ".dylib", ".exe", ".pyd", ".a", ".o")
hallados = [n for n in nombres if n.lower().endswith(BINARIOS)]
R.comprobar(
    "Z5", "no contiene binarios compilados",
    not hallados, f"encontrados: {hallados[:3]}" if hallados else "ninguno")

# El ZIP recién construido debe volver a parsear: es lo que se sube.
with zipfile.ZipFile(zip_prueba) as zf:
    contenido = zf.read("evaluacion_rsu/metadata.txt").decode("utf-8")
c2 = configparser.ConfigParser()
try:
    c2.read_string(contenido)
    ok_zip = c2["general"]["version"].strip() == version
    det = f"version en el ZIP = {c2['general']['version'].strip()}"
except Exception as exc:  # noqa: BLE001
    ok_zip, det = False, f"{type(exc).__name__}: {exc}"

R.comprobar(
    "Z6", "el metadata.txt DENTRO del ZIP parsea y trae la versión correcta",
    ok_zip, det)


ok = R.resumen()
arnes.terminar(0 if ok else 1)
