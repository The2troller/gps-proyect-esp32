"""
colector.py: rastrea múltiples celulares a la vez dibujándolos con distintos colores.
"""
import json
import numpy as np
from scipy.optimize import least_squares
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from fuentes import Fuentes

def rssi_a_dist(rssi, A, n):
    return 10 ** ((A - rssi) / (10 * n))

def trilaterar(puntos, dists, x0, limites, w_ancla=None):
    puntos = np.asarray(puntos, float)
    dists = np.asarray(dists, float)
    pesos = 1.0 / np.maximum(dists, 0.5)
    if w_ancla is not None:
        pesos = pesos * np.asarray(w_ancla, float)  # peso de confianza por ancla (config.json)

    def residuo(p):
        return (np.hypot(puntos[:, 0] - p[0], puntos[:, 1] - p[1]) - dists) * pesos

    W, H = limites
    return least_squares(residuo, x0, bounds=([0, 0], [W, H])).x

def main():
    with open("config.json") as fh:
        cfg = json.load(fh)

    anclas = cfg["anclas"]
    W, H = cfg["sala"]
    celda = cfg["celda_m"]
    nx, ny = int(round(W / celda)), int(round(H / celda))
    mapa = np.zeros((ny, nx))
    
    # Manejar si es un solo string o una lista
    objetivos = cfg["objetivo"]
    if isinstance(objetivos, str): objetivos = [objetivos]

    estado = {obj: {"pos": None} for obj in objetivos}

    fuentes = Fuentes(objetivos, cfg["puerto_udp"])
    fuentes.iniciar()

    fig, ax = plt.subplots(figsize=(7, max(4, 7 * H / W)))
    img = ax.imshow(mapa, origin="lower", extent=[0, W, 0, H], cmap="hot",
                    vmin=0, vmax=1, interpolation="bilinear")
    
    for nombre, a in anclas.items():
        ax.plot(*a["pos"], "c^", ms=12)
        ax.annotate(nombre, a["pos"], textcoords="offset points", xytext=(6, 6), color="cyan")
    
    # Asignar un color diferente a cada celular detectado
    colores = ["wo", "co", "mo", "go", "yo"] # Blanco, Cian, Magenta, Verde, Amarillo
    puntos_plot = {}
    for i, obj in enumerate(objetivos):
        # Usamos np.nan para que el punto nazca "invisible" sin romper los ejes
        p, = ax.plot([np.nan], [np.nan], colores[i % len(colores)], ms=10, mec="k", label=obj)
        puntos_plot[obj] = p
        
    ax.legend(loc="upper right", framealpha=0.7)
    ax.set_xlabel("x (m)")
    ax.set_ylabel("y (m)")
    
    # FORZAR LOS LÍMITES PARA QUE EMPIECE EN 0,0 SÍ O SÍ
    ax.set_xlim(0, W)
    ax.set_ylim(0, H)

    def actualizar(_):
        mapa[:] *= cfg["decay"]
        
        # Calcular posición para CADA objetivo por separado
        for obj in objetivos:
            puntos, dists, usados, w = [], [], [], []
            for nombre_ancla, a in anclas.items():
                lect = fuentes.ultimas(obj, nombre_ancla, cfg["ventana_s"])
                if len(lect) >= cfg["min_muestras"]:
                    puntos.append(a["pos"])
                    dists.append(rssi_a_dist(np.median(lect), a["A"], a["n"]))
                    usados.append(nombre_ancla)
                    w.append(a.get("peso", 1.0))

            if len(puntos) >= 3:
                x0 = estado[obj]["pos"] if estado[obj]["pos"] is not None else [W / 2, H / 2]
                p = np.asarray(trilaterar(puntos, dists, x0, (W, H), w))
                k = cfg["suavizado"]
                estado[obj]["pos"] = p if estado[obj]["pos"] is None else k * p + (1 - k) * estado[obj]["pos"]
                
                ix = min(int(estado[obj]["pos"][0] / celda), nx - 1)
                iy = min(int(estado[obj]["pos"][1] / celda), ny - 1)
                mapa[iy, ix] += 1.0
                
                # Actualizar la posición visual del punto
                puntos_plot[obj].set_data([estado[obj]["pos"][0]], [estado[obj]["pos"][1]])
            else:
                # Si pierde la señal, esconder el punto con np.nan para no alterar el gráfico
                puntos_plot[obj].set_data([np.nan], [np.nan])

        img.set_data(mapa)
        img.set_clim(0, max(1.0, float(mapa.max())))
        ax.set_title("Rastreando múltiples celulares")

    anim = FuncAnimation(fig, actualizar, interval=int(cfg["intervalo_ms"]),
                         cache_frame_data=False)
    plt.show()
    return anim

if __name__ == "__main__":
    main()