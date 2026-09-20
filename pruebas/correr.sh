#!/usr/bin/env bash
# Corre el conjunto de pruebas de EvaluaciónRSU sin interfaz gráfica.
#
#   ./pruebas/correr.sh
#
# Requiere QGIS con enlaces de Python. En Debian/Ubuntu:
#   sudo apt-get install python3-qgis python3-pyqt5 python3-pyqt5.qtsql
#
# En macOS con QGIS.app, use el Python que trae la aplicación:
#   PY=/Applications/QGIS.app/Contents/MacOS/bin/python3 ./pruebas/correr.sh

set -uo pipefail
cd "$(dirname "$0")/.."

export QT_QPA_PLATFORM=offscreen   # sin servidor gráfico

# Los enlaces de QGIS suelen estar compilados para UNA versión concreta de
# Python, que no siempre es la que responde a `python3`. Se busca el primer
# intérprete que logre importar qgis.core.
if [ -z "${PY:-}" ]; then
    for candidato in python3 python3.12 python3.11 python3.10; do
        command -v "$candidato" >/dev/null 2>&1 || continue
        if "$candidato" -c "import qgis.core" >/dev/null 2>&1; then
            PY="$candidato"
            break
        fi
    done
fi
if [ -z "${PY:-}" ]; then
    echo "No se encontró un Python con enlaces de QGIS." >&2
    echo "Indique uno con PY=/ruta/a/python3 $0" >&2
    exit 1
fi
echo "Intérprete: $($PY -c 'import sys,qgis.core; print(sys.executable, "— QGIS", qgis.core.Qgis.QGIS_VERSION)')"

fallos=0
for prueba in pruebas/test_*.py; do
    echo
    echo "───────────────────────────────────────────────────────────"
    echo "  $prueba"
    echo "───────────────────────────────────────────────────────────"
    "$PY" "$prueba" 2>&1 | grep -v "XDG_RUNTIME_DIR\|QStandardPaths"
    estado=${PIPESTATUS[0]}
    [ "$estado" -ne 0 ] && fallos=$((fallos + 1))
done

echo
if [ "$fallos" -eq 0 ]; then
    echo "Todas las pruebas pasan."
else
    echo "$fallos archivo(s) de prueba con fallos."
fi
exit "$fallos"
