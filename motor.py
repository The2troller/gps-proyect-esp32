"""
motor.py: convierte posiciones de pulseras en estadisticas de monitoreo.

  - Mapa de calor de densidad actual (personas/m2) y mapa acumulado.
  - Aforo estimado = pulseras activas x factor de expansion (la muestra representa al publico).
  - Densidad, nivel (BAJO/MEDIO/ALTO) y alertas de congestion por zona.
  - Permanencia por zona, entradas y flujos entre zonas.
  - Historico (cada 2 s) exportable a CSV.
"""
import csv
import io
import time
from collections import deque, Counter

import numpy as np
from scipy.ndimage import gaussian_filter
from scipy.optimize import least_squares

NIVELES = ["BAJO", "MEDIO", "ALTO"]


# ---------------------------------------------------------------- geometria
def rssi_a_dist(rssi, A, n):
    return 10 ** ((A - rssi) / (10 * n))


def trilaterar(puntos, dists, x0, limites, w_ancla=None):
    puntos = np.asarray(puntos, float)
    dists = np.asarray(dists, float)
    pesos = 1.0 / np.maximum(dists, 0.5)
    if w_ancla is not None:
        pesos = pesos * np.asarray(w_ancla, float)

    def residuo(p):
        return (np.hypot(puntos[:, 0] - p[0], puntos[:, 1] - p[1]) - dists) * pesos

    W, H = limites
    x0 = np.clip(np.asarray(x0, float), [1e-3, 1e-3], [W - 1e-3, H - 1e-3])
    return least_squares(residuo, x0, bounds=([0, 0], [W, H])).x


def dentro_poligono(xy, poli):
    """Ray casting vectorizado. xy: (N,2), poli: (M,2). Devuelve mascara (N,)."""
    xy = np.asarray(xy, float).reshape(-1, 2)
    x, y = xy[:, 0], xy[:, 1]
    res = np.zeros(len(xy), bool)
    px, py = poli[:, 0], poli[:, 1]
    j = len(poli) - 1
    for i in range(len(poli)):
        cond = ((py[i] > y) != (py[j] > y))
        with np.errstate(divide="ignore", invalid="ignore"):
            xint = (px[j] - px[i]) * (y - py[i]) / (py[j] - py[i]) + px[i]
        res ^= cond & (x < xint)
        j = i
    return res


def area_poligono(poli):
    x, y = poli[:, 0], poli[:, 1]
    return 0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


# ---------------------------------------------------------------- motor base
class Motor:
    def __init__(self, clave, cfg, alias=None, simulado=False):
        self.clave = clave
        self.cfg = cfg
        self.alias = alias or {}
        self.simulado = simulado
        self.W, self.H = map(float, cfg["dimensiones"])
        self.celda = float(cfg.get("celda_m", 0.5))
        self.nx = max(1, int(round(self.W / self.celda)))
        self.ny = max(1, int(round(self.H / self.celda)))
        self.factor = float(cfg.get("factor_expansion", 1))
        self.sigma = float(cfg.get("sigma_m", 0.6)) / self.celda
        self.decay = float(cfg.get("decay", 0.98))
        self.olvido = float(cfg.get("olvido_s", 5.0))

        self.zonas = []
        for z in cfg.get("zonas", []):
            poli = np.asarray(z["poligono"], float)
            self.zonas.append({
                "nombre": z["nombre"], "poli": poli, "area": float(area_poligono(poli)),
                "medio": float(z.get("umbral_medio", 2.0)), "alto": float(z.get("umbral_alto", 4.0)),
                "nivel": 0, "racha": 0, "conteo": 0, "densidad": 0.0,
            })

        self.densidad = np.zeros((self.ny, self.nx))
        self.acumulado = np.zeros((self.ny, self.nx))
        self.tags = {}                        # id -> {pos, zona, t_entrada, t_visto, anclas}
        self.permanencias = deque(maxlen=20000)  # (t_fin, zona, duracion_s)
        self.entradas = deque(maxlen=50000)      # (t, zona)
        self.transiciones = deque(maxlen=50000)  # (t, de, a)
        self.alertas = deque(maxlen=500)
        self._id_alerta = 0
        self._ultima_alerta = {}
        self.historia = deque(maxlen=5400)       # 3 h a 2 s
        self._t_hist = 0.0
        self.t0 = time.time()
        self.kpis = {}

    # -- a implementar por cada mapa
    def obtener_posiciones(self, ahora):
        """Devuelve dict id -> (x, y, info_extra)."""
        raise NotImplementedError

    def receptores(self):
        return []

    def extra(self):
        return {}

    # -- comun
    def _zona_de(self, x, y):
        for z in self.zonas:
            if dentro_poligono([[x, y]], z["poli"])[0]:
                return z["nombre"]
        return None

    def _salir(self, tid, ahora):
        st = self.tags.pop(tid, None)
        if st and st["zona"]:
            self.permanencias.append((ahora, st["zona"], ahora - st["t_entrada"]))

    def paso(self):
        ahora = time.time()
        pos = self.obtener_posiciones(ahora)

        # 1) seguimiento por pulsera: zona, permanencia, transiciones
        ids = list(pos)
        xy_all = np.array([[pos[i][0], pos[i][1]] for i in ids]) if ids else np.zeros((0, 2))
        zonas_de = [None] * len(ids)
        for z in reversed(self.zonas):          # la primera zona de la lista tiene prioridad
            if len(ids):
                for k in np.nonzero(dentro_poligono(xy_all, z["poli"]))[0]:
                    zonas_de[k] = z["nombre"]
        for k, tid in enumerate(ids):
            x, y, info = pos[tid]
            zona = zonas_de[k]
            st = self.tags.get(tid)
            if st is None:
                st = self.tags[tid] = {"zona": None, "t_entrada": ahora}
            if zona != st["zona"]:
                if st["zona"]:
                    self.permanencias.append((ahora, st["zona"], ahora - st["t_entrada"]))
                if zona:
                    self.entradas.append((ahora, zona))
                if st["zona"] and zona:
                    self.transiciones.append((ahora, st["zona"], zona))
                st["zona"], st["t_entrada"] = zona, ahora
            st["pos"], st["t_visto"], st["info"] = (x, y), ahora, info
        for tid in [t for t, s in self.tags.items() if ahora - s["t_visto"] > self.olvido]:
            self._salir(tid, ahora)

        activos = {t: s for t, s in self.tags.items() if t in pos}
        xy = np.array([s["pos"] for s in activos.values()]) if activos else np.zeros((0, 2))

        # 2) mapas de calor
        if len(xy):
            h, _, _ = np.histogram2d(xy[:, 1], xy[:, 0], bins=[self.ny, self.nx],
                                     range=[[0, self.H], [0, self.W]])
        else:
            h = np.zeros((self.ny, self.nx))
        self.densidad = gaussian_filter(h, self.sigma, mode="constant") * self.factor / (self.celda ** 2)
        self.acumulado = self.acumulado * self.decay + h

        # 3) zonas: densidad, nivel con histeresis, alertas
        for z in self.zonas:
            c = int(dentro_poligono(xy, z["poli"]).sum()) if len(xy) else 0
            z["conteo"] = c
            z["densidad"] = c * self.factor / z["area"]
            d = z["densidad"]
            bruto = 2 if d >= z["alto"] else 1 if d >= z["medio"] else 0
            # histeresis: para bajar de nivel la densidad debe caer bajo el 85% del umbral
            if bruto < z["nivel"]:
                if z["nivel"] == 2 and d >= 0.85 * z["alto"]:
                    bruto = 2
                elif z["nivel"] >= 1 and bruto == 0 and d >= 0.85 * z["medio"]:
                    bruto = 1
            if bruto == z["nivel"]:
                z["racha"] = 0
                continue
            z["racha"] += 1
            sube = bruto > z["nivel"]
            if (sube and z["racha"] >= 2) or (not sube and z["racha"] >= 8):
                anterior, z["nivel"], z["racha"] = z["nivel"], bruto, 0
                if sube:
                    self._alertar(ahora, z, anterior)

        # 4) indicadores
        muestra = len(pos)
        rec = self.receptores()
        self.kpis = {
            "aforo": int(round(muestra * self.factor)),
            "muestra": muestra,
            "factor": self.factor,
            "receptores_online": sum(1 for r in rec if r["online"]),
            "receptores_total": len(rec),
            "alertas_sin_ver": sum(1 for a in self.alertas if not a["vista"]),
        }

        # 5) historico
        if ahora - self._t_hist >= 2.0:
            self._t_hist = ahora
            self.historia.append({
                "t": ahora, "aforo": self.kpis["aforo"], "muestra": muestra,
                "zonas": {z["nombre"]: round(z["densidad"], 3) for z in self.zonas},
            })

    def _alertar(self, ahora, z, anterior):
        # no repetir la misma alerta (zona y nivel) antes de 60 s
        clave = (z["nombre"], z["nivel"])
        if ahora - self._ultima_alerta.get(clave, 0) < 60:
            return
        self._ultima_alerta[clave] = ahora
        self._id_alerta += 1
        self.alertas.append({
            "id": self._id_alerta, "t": ahora, "zona": z["nombre"], "nivel": NIVELES[z["nivel"]],
            "densidad": round(z["densidad"], 2), "personas": int(z["conteo"] * self.factor),
            "mensaje": f"{'Alta' if z['nivel'] == 2 else 'Media'} densidad en {z['nombre']}",
            "vista": False,
        })

    def marcar_vistas(self):
        for a in self.alertas:
            a["vista"] = True

    # ------------------------------------------------------------ salidas
    def _stats_zonas(self, ahora, ventana=600):
        corte = ahora - ventana
        perm = [(z, d) for t, z, d in self.permanencias if t >= corte]
        ent = Counter(z for t, z in self.entradas if t >= corte)
        out = []
        for z in self.zonas:
            ds = [d for zz, d in perm if zz == z["nombre"]]
            actuales = [ahora - s["t_entrada"] for s in self.tags.values() if s["zona"] == z["nombre"]]
            prom = float(np.mean(ds)) if ds else (float(np.mean(actuales)) if actuales else 0.0)
            out.append({
                "nombre": z["nombre"], "poligono": z["poli"].round(3).tolist(), "area": round(z["area"], 1),
                "conteo": z["conteo"], "personas": int(z["conteo"] * self.factor),
                "densidad": round(z["densidad"], 2), "nivel": NIVELES[z["nivel"]],
                "umbral_medio": z["medio"], "umbral_alto": z["alto"],
                "permanencia_s": round(prom, 1),
                "entradas_10min": int(ent.get(z["nombre"], 0) * self.factor),
            })
        return out

    def _flujos(self, ahora, ventana=600):
        c = Counter((de, a) for t, de, a in self.transiciones if t >= ahora - ventana)
        return [{"de": de, "a": a, "personas": int(n * self.factor)} for (de, a), n in c.most_common(8)]

    def estado(self):
        ahora = time.time()
        acum = self.acumulado / self.acumulado.max() if self.acumulado.max() > 0 else self.acumulado
        tags = []
        for tid, s in self.tags.items():
            if ahora - s["t_visto"] > 1.5:
                continue
            tags.append({"id": tid, "alias": self.alias.get(tid, tid),
                         "x": round(s["pos"][0], 2), "y": round(s["pos"][1], 2),
                         "zona": s["zona"], **(s.get("info") or {})})
        serie = list(self.historia)[-90:]
        return {
            "mapa": self.clave, "nombre": self.cfg.get("nombre", self.clave), "simulado": self.simulado,
            "dims": [self.W, self.H], "celda": self.celda, "grid": [self.nx, self.ny],
            "densidad": np.round(self.densidad, 2).ravel().tolist(),
            "acumulado": np.round(acum, 3).ravel().tolist(),
            "tags": tags if len(tags) <= 400 else tags[:400],
            "zonas": self._stats_zonas(ahora),
            "flujos": self._flujos(ahora),
            "kpis": self.kpis,
            "serie_aforo": [[round(h["t"]), h["aforo"]] for h in serie],
            "alertas": list(self.alertas)[-40:][::-1],
            "receptores": self.receptores(),
            "decoracion": self.cfg.get("decoracion", []),
            **self.extra(),
        }

    def historico(self, max_puntos=600):
        h = list(self.historia)
        paso = max(1, len(h) // max_puntos)
        h = h[::paso]
        return {
            "zonas": [z["nombre"] for z in self.zonas],
            "t": [round(p["t"]) for p in h],
            "aforo": [p["aforo"] for p in h],
            "densidad": {z["nombre"]: [p["zonas"].get(z["nombre"], 0) for p in h] for z in self.zonas},
            "permanencia": [{"zona": z["nombre"], "prom_s": s["permanencia_s"], "entradas_10min": s["entradas_10min"]}
                            for z, s in zip(self.zonas, self._stats_zonas(time.time()))],
            "flujos": self._flujos(time.time(), ventana=3600),
        }

    def exportar_csv(self, tipo):
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        fmt = lambda t: time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t))
        if tipo == "alertas":
            w.writerow(["fecha_hora", "zona", "nivel", "densidad_p_m2", "personas_est"])
            for a in self.alertas:
                w.writerow([fmt(a["t"]), a["zona"], a["nivel"], str(a["densidad"]).replace(".", ","), a["personas"]])
        elif tipo == "permanencias":
            w.writerow(["fecha_hora_salida", "zona", "permanencia_s"])
            for t, z, d in self.permanencias:
                w.writerow([fmt(t), z, str(round(d, 1)).replace(".", ",")])
        elif tipo == "flujos":
            w.writerow(["fecha_hora", "desde", "hacia"])
            for t, de, a in self.transiciones:
                w.writerow([fmt(t), de, a])
        else:
            nombres = [z["nombre"] for z in self.zonas]
            w.writerow(["fecha_hora", "aforo_estimado", "pulseras_activas"] + [f"densidad_{n}" for n in nombres])
            for p in self.historia:
                w.writerow([fmt(p["t"]), p["aforo"], p["muestra"]] +
                           [str(p["zonas"].get(n, 0)).replace(".", ",") for n in nombres])
        return "﻿" + buf.getvalue()   # BOM para que Excel lea bien los tildes


# ---------------------------------------------------------------- sala real
class MotorSala(Motor):
    """Posiciones a partir de RSSI reales (o simulados) de las anclas."""

    def __init__(self, cfg, fuentes, alias=None, simulado=False):
        super().__init__("sala", cfg, alias, simulado)
        self.f = fuentes
        self.anclas = cfg["anclas"]
        self.ventana = float(cfg.get("ventana_s", 2.0))
        self.min_anclas = int(cfg.get("min_anclas", 3))
        self.k = float(cfg.get("suavizado", 0.8))
        self._suave = {}
        self.sin_posicion = []

    def obtener_posiciones(self, ahora):
        out, sin_pos = {}, []
        for tid, vistas in self.f.tags(self.ventana).items():
            puntos, dists, w, rssi = [], [], [], {}
            for nombre, a in self.anclas.items():
                lect = self.f.ultimas(tid, nombre, self.ventana)
                if lect:
                    med = float(np.median(lect))
                    rssi[nombre] = round(med, 1)
                    puntos.append(a["pos"])
                    dists.append(rssi_a_dist(med, a["A"], a["n"]))
                    w.append(a.get("peso", 1.0))
            if len(puntos) < self.min_anclas:
                sin_pos.append({"id": tid, "alias": self.alias.get(tid, tid), "anclas": sorted(rssi)})
                continue
            prev = self._suave.get(tid)
            x0 = prev if prev is not None else [self.W / 2, self.H / 2]
            p = trilaterar(puntos, dists, x0, (self.W, self.H), w)
            p = p if prev is None else self.k * p + (1 - self.k) * prev
            self._suave[tid] = p
            out[tid] = (float(p[0]), float(p[1]), {"rssi": rssi})
        for tid in [t for t in self._suave if t not in out]:
            if tid not in self.tags:
                self._suave.pop(tid, None)
        self.sin_posicion = sin_pos
        return out

    def receptores(self):
        est = self.f.anclas_estado()
        out = []
        for nombre, a in self.anclas.items():
            e = est.get(nombre, {})
            hace = e.get("hace_s")
            online = hace is not None and hace < 12
            if nombre == self.f.nombre_laptop and self.f.ble_estado == "ok":
                online = True
            lat = e.get("latido") or {}
            out.append({
                "id": nombre, "x": a["pos"][0], "y": a["pos"][1], "online": online,
                "hace_s": None if hace is None else round(hace, 1),
                "paquetes_s": round(e.get("paquetes_s", 0.0), 1), "ip": e.get("ip"),
                "A": a["A"], "n": a["n"], "peso": a.get("peso", 1.0),
                "wifi_dbm": lat.get("wifi"), "uptime_s": lat.get("up"),
            })
        return out

    def extra(self):
        return {"sin_posicion": self.sin_posicion, "radar": self.f.radar(),
                "udp": self.f.udp_estado, "ble": self.f.ble_estado}


# ---------------------------------------------------------------- parque simulado
class MotorParque(Motor):
    def __init__(self, cfg, sim):
        super().__init__("parque", cfg, simulado=True)
        self.sim = sim

    def obtener_posiciones(self, ahora):
        return self.sim.posiciones()

    def receptores(self):
        return self.sim.receptores()

    def extra(self):
        return {"fase": self.sim.fase}
