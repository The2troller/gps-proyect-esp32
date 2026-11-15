"""
fluxa_gui.py: panel web de FLUXA (Plataforma de Inteligencia Operacional).

Uso:
    python fluxa_gui.py            # sala con datos reales (ESP32 + laptop) y parque simulado
    python fluxa_gui.py --sim      # todo simulado, para probar sin hardware
    python fluxa_gui.py --puerto 8080 --sin-navegador

Luego abre http://localhost:8000 (se abre solo).
"""
import argparse
import json
import os
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from fuentes import Fuentes
from motor import MotorSala, MotorParque
from simulador import SimSala, SimParque

AQUI = os.path.dirname(os.path.abspath(__file__))


class App:
    def __init__(self, cfg, simular):
        self.cfg = cfg
        det = cfg.get("deteccion", {})
        self.fuentes = Fuentes(cfg["puerto_udp"], det, usar_ble=not simular)
        self.fuentes.iniciar()
        if simular:
            SimSala(cfg["sala"], self.fuentes).iniciar()
        self.motores = {
            "sala": MotorSala(cfg["sala"], self.fuentes, det.get("alias", {}), simulado=simular),
            "parque": MotorParque(cfg["parque"], SimParque(cfg["parque"])),
        }
        self.cache = {}
        self.lock = threading.Lock()
        threading.Thread(target=self._bucle, daemon=True).start()

    def _bucle(self):
        while True:
            t = time.time()
            for clave, m in self.motores.items():
                try:
                    with self.lock:
                        m.paso()
                        self.cache[clave] = json.dumps(m.estado(), ensure_ascii=False).encode("utf-8")
                except Exception:
                    traceback.print_exc()
            time.sleep(max(0.05, 0.25 - (time.time() - t)))


def crear_handler(app):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _enviar(self, cuerpo, tipo="application/json; charset=utf-8", codigo=200, extra=None):
            if isinstance(cuerpo, str):
                cuerpo = cuerpo.encode("utf-8")
            self.send_response(codigo)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(cuerpo)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(cuerpo)

        def _motor(self, q):
            return app.motores.get(q.get("mapa", ["sala"])[0], app.motores["sala"])

        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if u.path in ("/", "/index.html"):
                with open(os.path.join(AQUI, "web", "index.html"), "rb") as fh:
                    return self._enviar(fh.read(), "text/html; charset=utf-8")
            if u.path == "/api/estado":
                clave = q.get("mapa", ["sala"])[0]
                return self._enviar(app.cache.get(clave, b"{}"))
            if u.path == "/api/historico":
                with app.lock:
                    data = self._motor(q).historico()
                return self._enviar(json.dumps(data, ensure_ascii=False))
            if u.path == "/api/exportar":
                tipo = q.get("tipo", ["historico"])[0]
                m = self._motor(q)
                with app.lock:
                    csv = m.exportar_csv(tipo)
                nombre = f"fluxa_{m.clave}_{tipo}_{time.strftime('%Y%m%d_%H%M')}.csv"
                return self._enviar(csv, "text/csv; charset=utf-8",
                                    extra={"Content-Disposition": f'attachment; filename="{nombre}"'})
            self._enviar('{"error":"no encontrado"}', codigo=404)

        def do_POST(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if u.path == "/api/alertas/vistas":
                with app.lock:
                    self._motor(q).marcar_vistas()
                return self._enviar('{"ok":true}')
            self._enviar('{"error":"no encontrado"}', codigo=404)

    return H


def main():
    ap = argparse.ArgumentParser(description="Panel FLUXA")
    ap.add_argument("--sim", action="store_true", help="simular la sala (sin ESP32 ni BLE)")
    ap.add_argument("--puerto", type=int, default=None, help="puerto web (por defecto el de config.json)")
    ap.add_argument("--config", default=os.path.join(AQUI, "config.json"))
    ap.add_argument("--sin-navegador", action="store_true")
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as fh:
        cfg = json.load(fh)
    app = App(cfg, args.sim)
    puerto = args.puerto or cfg.get("puerto_web", 8000)
    srv = ThreadingHTTPServer(("0.0.0.0", puerto), crear_handler(app))
    url = f"http://localhost:{puerto}"
    print(f"[ok] Panel FLUXA en {url}   (Ctrl+C para salir)")
    if args.sim:
        print("[sim] Sala simulada: 3 pulseras virtuales (FX01, FX02, FX03)")
    if not args.sin_navegador:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nchao")


if __name__ == "__main__":
    main()
