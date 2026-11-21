"""
simulador.py: datos sinteticos para probar el panel sin hardware.

  SimSala   -> pulseras virtuales que se mueven en la sala y generan RSSI ruidoso para
               cada ancla (modelo log-distancia). Esos RSSI pasan por el MISMO camino
               que los reales (trilateracion, filtros, zonas), asi se prueba todo el sistema.
  SimParque -> publico simulado en un recinto grande (posiciones directas), para mostrar
               como se veria FLUXA en un evento real.
"""
import math
import random
import threading
import time

import numpy as np


class SimSala:
    def __init__(self, cfg_sala, fuentes, n_tags=3, ruido_db=2.5, hz=8):
        self.cfg, self.f = cfg_sala, fuentes
        self.W, self.H = cfg_sala["dimensiones"]
        self.ruido, self.dt = ruido_db, 1.0 / hz
        self.tags = []
        for i in range(n_tags):
            p = np.array([random.uniform(0.5, self.W - 0.5), random.uniform(0.5, self.H - 0.5)])
            self.tags.append({"id": f"FX{i + 1:02d}", "p": p, "meta": p.copy(), "pausa": 0.0})
        self._hilo = threading.Thread(target=self._run, daemon=True)

    def iniciar(self):
        self._hilo.start()

    def _run(self):
        t_lat = 0.0
        while True:
            for tg in self.tags:
                if tg["pausa"] > 0:
                    tg["pausa"] -= self.dt
                else:
                    d = tg["meta"] - tg["p"]
                    dist = np.linalg.norm(d)
                    if dist < 0.1:
                        # a veces se juntan en la misma zona para provocar una alerta
                        if random.random() < 0.35:
                            base = np.array([4.5, 1.6])
                            tg["meta"] = base + np.random.normal(0, 0.4, 2)
                        else:
                            tg["meta"] = np.array([random.uniform(0.4, self.W - 0.4), random.uniform(0.4, self.H - 0.4)])
                        tg["meta"] = np.clip(tg["meta"], 0.2, [self.W - 0.2, self.H - 0.2])
                        tg["pausa"] = random.uniform(2, 8)
                    else:
                        tg["p"] = tg["p"] + d / dist * min(dist, 0.5 * self.dt)
                for nombre, a in self.cfg["anclas"].items():
                    d = max(0.2, float(np.hypot(*(tg["p"] - np.array(a["pos"])))))
                    rssi = a["A"] - 10 * a["n"] * math.log10(d) + random.gauss(0, self.ruido)
                    self.f.inyectar(tg["id"], nombre, int(round(rssi)))
            if time.time() - t_lat > 5:
                t_lat = time.time()
                for nombre in self.cfg["anclas"]:
                    if nombre != self.f.nombre_laptop:
                        self.f._latido(nombre, {"hb": 1, "up": int(time.time()) % 100000, "wifi": -55}, ip="sim")
            time.sleep(self.dt)


class SimParque:
    """Agentes que entran por el acceso, recorren zonas con puntos de interes y se van."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.W, self.H = cfg["dimensiones"]
        self.N = int(cfg.get("pulseras", 1200))
        self.vel = float(cfg.get("velocidad_sim", 3.0))
        z = {zz["nombre"]: np.asarray(zz["poligono"], float) for zz in cfg["zonas"]}
        self.zonas = z
        # puntos de interes por zona (centro, dispersion)
        self.poi = {
            "Frente de escenario": [((60, 60), (9, 3))],
            "Zona de Stands": [((14, 39), (4, 5)), ((28, 39), (4, 5)), ((42, 39), (4, 5))],
            "Patio de comidas": [((80, 39), (5, 4)), ((96, 39), (5, 4)), ((108, 39), (3, 4))],
            "Atracciones Norte": [((96, 64), (4, 4)), ((108, 70), (4, 4))],
            "Atracciones Sur": [((16, 14), (4, 4)), ((34, 14), (5, 4))],
            "Zona de tránsito": [((60, 30), (4, 9))],
        }
        self.hub = np.array([60.0, 28.0])
        self.puerta = np.array([60.0, 1.5])
        self.estado = np.zeros(self.N, int)          # 0 fuera, 1 dentro, 2 saliendo
        self.p = np.tile(self.puerta, (self.N, 1)).astype(float)
        self.ruta = [[] for _ in range(self.N)]
        self.pausa = np.zeros(self.N)
        self.v = np.random.uniform(0.9, 1.4, self.N)
        self.t0 = time.time()
        self._t = time.time()
        self.fase = "Apertura"
        self.rec = self._receptores_fijos()
        self._lock = threading.Lock()
        # arranca con parte del publico adentro
        for i in random.sample(range(self.N), int(self.N * 0.35)):
            self.estado[i] = 1
            self.p[i] = self._punto(self._elegir_zona())
            self.pausa[i] = random.uniform(5, 40)

    def _receptores_fijos(self):
        xs, ys = np.linspace(12, 108, 6), np.linspace(12, 68, 3)
        return [{"id": f"R{k + 1:02d}", "x": float(x), "y": float(y)}
                for k, (y, x) in enumerate((y, x) for y in ys for x in xs)]

    # ciclo de ~6 min (tiempo real): apertura -> show -> intermedio
    def _pesos(self, tt):
        c = (tt % 360) / 360
        if c < 0.25:
            self.fase = "Apertura"
            return {"Zona de Stands": 3, "Patio de comidas": 2, "Atracciones Norte": 2, "Atracciones Sur": 2, "Frente de escenario": 1, "Zona de tránsito": 1}
        if c < 0.65:
            self.fase = "Show en escenario"
            return {"Frente de escenario": 6, "Zona de Stands": 1, "Patio de comidas": 1, "Atracciones Norte": 0.5, "Atracciones Sur": 0.5, "Zona de tránsito": 0.5}
        self.fase = "Intermedio"
        return {"Patio de comidas": 5, "Zona de Stands": 3, "Atracciones Norte": 1.5, "Atracciones Sur": 1.5, "Frente de escenario": 1, "Zona de tránsito": 1}

    def _elegir_zona(self):
        w = self._pesos(time.time() - self.t0)
        nombres = list(w)
        return random.choices(nombres, weights=[w[n] for n in nombres])[0]

    def _punto(self, zona):
        poli = self.zonas[zona]
        (cx, cy), (sx, sy) = random.choice(self.poi[zona])
        x0, y0 = poli.min(0)
        x1, y1 = poli.max(0)
        return np.array([min(max(random.gauss(cx, sx), x0 + 0.5), x1 - 0.5),
                         min(max(random.gauss(cy, sy), y0 + 0.5), y1 - 0.5)])

    def _ruta_a(self, i, zona):
        destino = self._punto(zona)
        ruta = []
        # pasa por el eje central si cambia de lado del recinto
        if abs(self.p[i][0] - destino[0]) > 15 or self.p[i][1] < 12:
            ruta.append(self.hub + np.random.normal(0, [3.5, 6]))
        ruta.append(destino)
        self.ruta[i] = ruta

    def _objetivo_dentro(self, tt):
        sube = min(1.0, 0.35 + tt / 150)
        return int(self.N * sube * (0.9 + 0.08 * math.sin(2 * math.pi * tt / 300)))

    def paso(self):
        ahora = time.time()
        dt = min(0.5, ahora - self._t) * self.vel
        self._t = ahora
        tt = ahora - self.t0
        dentro = int((self.estado == 1).sum())
        obj = self._objetivo_dentro(tt)
        if dentro < obj:
            fuera = np.nonzero(self.estado == 0)[0]
            for i in np.random.choice(fuera, min(len(fuera), max(1, (obj - dentro) // 25)), replace=False):
                self.estado[i] = 1
                self.p[i] = self.puerta + np.random.normal(0, [3, 0.5])
                self._ruta_a(i, self._elegir_zona())
        elif dentro > obj + 10:
            idx = np.nonzero(self.estado == 1)[0]
            for i in np.random.choice(idx, min(len(idx), (dentro - obj) // 25 + 1), replace=False):
                self.estado[i] = 2
                self.ruta[i] = [self.hub + np.random.normal(0, [2, 4]), self.puerta + np.random.normal(0, [3, 0.3])]

        for i in np.nonzero(self.estado > 0)[0]:
            if self.pausa[i] > 0:
                self.pausa[i] -= dt
                self.p[i] += np.random.normal(0, 0.15, 2)      # pequeño vaiven
                continue
            if not self.ruta[i]:
                if self.estado[i] == 2:
                    self.estado[i] = 0
                    continue
                self._ruta_a(i, self._elegir_zona())
            meta = self.ruta[i][0]
            d = meta - self.p[i]
            dist = float(np.hypot(*d))
            paso = self.v[i] * dt
            if dist <= paso:
                self.p[i] = meta
                self.ruta[i].pop(0)
                if not self.ruta[i] and self.estado[i] == 1:
                    self.pausa[i] = random.uniform(15, 90)
            else:
                self.p[i] += d / dist * paso
        np.clip(self.p, [0.3, 0.3], [self.W - 0.3, self.H - 0.3], out=self.p)

    def posiciones(self):
        self.paso()
        idx = np.nonzero(self.estado > 0)[0]
        return {f"T{i:04d}": (float(self.p[i][0]), float(self.p[i][1]), None) for i in idx}

    def receptores(self):
        dentro = int((self.estado > 0).sum())
        return [{**r, "online": True, "hace_s": 0.4, "paquetes_s": round(dentro / 18 * 4 * random.uniform(0.8, 1.2), 1)}
                for r in self.rec]
