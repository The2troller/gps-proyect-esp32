"""
prueba_udp.py: imita a los ESP32 enviando paquetes UDP reales al panel.

Sirve para comprobar que el camino de red funciona sin tener las placas a mano.
Envia lecturas de dos pulseras (FX01 y FX02) vistas por las anclas del config.json,
incluida LAPTOP, y latidos cada 5 s.

Uso (con el panel corriendo SIN --sim):
    python prueba_udp.py                 # al mismo computador
    python prueba_udp.py --host 192.168.x.x
"""
import argparse
import json
import math
import random
import socket
import time


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--puerto", type=int, default=None)
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--segundos", type=float, default=0, help="0 = sin limite")
    a = ap.parse_args()

    cfg = json.load(open(a.config, encoding="utf-8"))
    puerto = a.puerto or cfg["puerto_udp"]
    sala = cfg["sala"]
    W, H = sala["dimensiones"]
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    t0, t_lat = time.time(), 0
    print(f"Enviando a {a.host}:{puerto}  (Ctrl+C para parar)")
    while not a.segundos or time.time() - t0 < a.segundos:
        t = time.time() - t0
        tags = {
            "FX01": (W / 2 + 2.0 * math.cos(t / 6), H / 2 + 2.2 * math.sin(t / 6)),
            "FX02": (4.6 + 0.3 * math.sin(t / 3), 1.5 + 0.3 * math.cos(t / 3)),
        }
        for tag, (x, y) in tags.items():
            for nombre, an in sala["anclas"].items():
                d = max(0.2, math.hypot(x - an["pos"][0], y - an["pos"][1]))
                rssi = an["A"] - 10 * an["n"] * math.log10(d) + random.gauss(0, 2.5)
                msg = {"a": nombre, "o": tag, "rssi": int(round(rssi))}
                sock.sendto(json.dumps(msg).encode(), (a.host, puerto))
        if time.time() - t_lat > 5:
            t_lat = time.time()
            for nombre in sala["anclas"]:
                sock.sendto(json.dumps({"a": nombre, "hb": 1, "up": int(t), "wifi": -60}).encode(), (a.host, puerto))
        time.sleep(0.12)


if __name__ == "__main__":
    main()
