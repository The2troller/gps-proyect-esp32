"""
calibrar.py: ajusta RSSI = A - 10 n log10(d) para UNA ancla.

Uso:  python calibrar.py A        (luego B y LAPTOP, una vez cada una)

Para cada distancia pones el celular (anunciando PULSERA1) a esa distancia de la ancla,
sin moverlo, y presionas Enter. El script toma ~100 muestras y usa la mediana.
"""
import json
import sys
import time

import numpy as np

from fuentes import Fuentes

DISTANCIAS = [0.5, 1, 2, 3, 4]   # metros
MUESTRAS = 100
TIMEOUT_S = 90


def main():
    if len(sys.argv) != 2:
        sys.exit("Uso: python calibrar.py <ancla>   (A, B o LAPTOP)")
    ancla = sys.argv[1]

    with open("config.json") as fh:
        cfg = json.load(fh)
    if ancla not in cfg["anclas"]:
        sys.exit(f"Ancla '{ancla}' no existe en config.json: {list(cfg['anclas'])}")

    f = Fuentes(cfg["objetivo"], cfg["puerto_udp"])
    f.iniciar()
    time.sleep(2)

    puntos = []  # (distancia, mediana_rssi)
    for d in DISTANCIAS:
        input(f"\nCelular a {d} m de {ancla}, quieto. Enter para medir... ")
        f.limpiar(ancla)
        t0 = time.time()
        while len(f.todas(ancla)) < MUESTRAS and time.time() - t0 < TIMEOUT_S:
            time.sleep(0.2)
        lect = f.todas(ancla)
        if len(lect) < 10:
            print(f"  Solo {len(lect)} muestras: salto esta distancia (revisa que llegan datos).")
            continue
        med = float(np.median(lect))
        puntos.append((d, med))
        print(f"  {len(lect)} muestras | mediana {med:.1f} dBm | desv. {np.std(lect):.1f} dB")

    if len(puntos) < 3:
        sys.exit("Muy pocos puntos para ajustar.")

    d = np.array([p[0] for p in puntos])
    rssi = np.array([p[1] for p in puntos])
    pendiente, A = np.polyfit(np.log10(d), rssi, 1)   # rssi = A + pendiente*log10(d)
    n = -pendiente / 10

    print(f"\nAjuste para {ancla}:  A = {A:.1f} dBm,  n = {n:.2f}")
    if not 1.5 <= n <= 5:
        print("  Ojo: n fuera de lo típico (1.5 a 5). Repite la medición.")
    print("  d real -> d estimada")
    for real, r in zip(d, rssi):
        est = 10 ** ((A - r) / (10 * n))
        print(f"  {real:4.1f} m -> {est:4.2f} m")

    if input("\n¿Guardar A y n en config.json? [s/N] ").strip().lower() == "s":
        cfg["anclas"][ancla]["A"] = round(float(A), 1)
        cfg["anclas"][ancla]["n"] = round(float(n), 2)
        with open("config.json", "w") as fh:
            json.dump(cfg, fh, indent=2)
        print("Guardado.")


if __name__ == "__main__":
    main()
