"""El servidor se arranca como script («python backend\\api\\api_server.py»): la raíz del proyecto no está en el camino de Python
hasta que el propio archivo la añade. Un import de `backend` antes de eso lo tumba al arrancar (y las pruebas, que lo
importan como paquete, no lo ven)."""

import os
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def test_api_server_se_puede_importar_como_script():
    codigo = "import sys; sys.path.insert(0, r'backend/api'); import api_server; print('IMPORT-OK')"
    r = subprocess.run([sys.executable, "-I", "-c", codigo],      # -I: sin la carpeta actual en el camino, como al arrancar el script
                       cwd=RAIZ, env={**os.environ, "ONIX_AUDIO_BACKEND": "mock"}, capture_output=True, text=True, timeout=120)
    assert "IMPORT-OK" in r.stdout, (r.stdout + r.stderr)[-1200:]
