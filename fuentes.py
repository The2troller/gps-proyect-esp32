"""
fuentes.py: recibe las lecturas RSSI de las anclas y las ordena por pulsera y por ancla.

Fuentes de datos:
  - ESP32 (anclas A, B, ...): paquetes JSON por UDP (puerto 4210 por defecto).
      {"a":"A","o":"FX01","rssi":-61}          lectura de una pulsera
      {"a":"A","hb":1,"up":120,"ble":900,...}   latido (estado del receptor)
      {"a":"A","hola":1}                        aviso al conectarse al WiFi
  - Laptop (ancla LAPTOP): escaneo BLE con bleak.

Identificacion de pulseras SIN depender del nombre:
  Una pulsera FLUXA anuncia "Manufacturer Data" con Company ID 0xFFFF y datos que
  empiezan con "FX" seguido de su numero en ASCII, por ejemplo FX01.
  (nRF Connect > Advertiser > Add record > Manufacturer Data:
   Company ID 0xFFFF, data 46583031  ->  "FX01")
  Por compatibilidad, tambien se aceptan los nombres listados en config.json.
"""
import asyncio
import json
import threading
import time
from collections import defaultdict, deque


def identificar_tag(nombre, manufacturer_data, deteccion):
    """Devuelve el ID de la pulsera (p. ej. 'FX01') o None si no es una pulsera FLUXA."""
    cid = int(deteccion.get("company_id", 0xFFFF))
    pref = deteccion.get("prefijo", "FX")
    datos = (manufacturer_data or {}).get(cid)
    if datos:
        datos = bytes(datos)
        if datos.startswith(pref.encode()):
            resto = datos[len(pref):].decode("ascii", "ignore")
            resto = "".join(c for c in resto if c.isalnum() or c in "-_")
            if resto:
                return pref + resto
    if nombre and nombre in deteccion.get("nombres", []):
        return nombre
    return None


class _UDP(asyncio.DatagramProtocol):
    def __init__(self, fuentes):
        self.f = fuentes

    def datagram_received(self, data, addr):
        try:
            m = json.loads(data.decode())
        except Exception:
            return
        ancla = str(m.get("a", "")).strip()
        if not ancla:
            return
        if "o" in m and "rssi" in m:
            try:
                self.f._guardar(str(m["o"]), ancla, int(m["rssi"]), ip=addr[0])
            except (TypeError, ValueError):
                pass
        elif "hb" in m or "hola" in m:
            self.f._latido(ancla, m, ip=addr[0])


class Fuentes:
    def __init__(self, puerto_udp, deteccion=None, nombre_laptop="LAPTOP", usar_ble=True):
        # Compatibilidad con la version anterior: Fuentes(lista_objetivos, puerto)
        if isinstance(puerto_udp, (list, str)):
            puerto_udp, deteccion = deteccion, {"nombres": list(puerto_udp) if isinstance(puerto_udp, list) else [puerto_udp]}
        self.puerto = int(puerto_udp)
        self.det = deteccion or {}
        self.nombre_laptop = nombre_laptop
        self.usar_ble = usar_ble

        self._datos = defaultdict(lambda: defaultdict(lambda: deque(maxlen=3000)))  # tag -> ancla -> (t, rssi)
        self._anclas = {}   # ancla -> estado
        self._radar = {}    # direccion BLE -> lo ultimo que vio el laptop
        self._lock = threading.Lock()
        self._hilo = threading.Thread(target=self._run, daemon=True)
        self.udp_estado = "iniciando"
        self.ble_estado = "desactivado" if not usar_ble else "iniciando"

    # ------------------------------------------------------------ API publica
    def iniciar(self):
        self._hilo.start()

    def ultimas(self, tag, ancla, ventana_s):
        corte = time.time() - ventana_s
        with self._lock:
            return [r for t, r in self._datos[tag][ancla] if t >= corte]

    def tags(self, ventana_s):
        """Pulseras con al menos una lectura reciente, con las anclas que las vieron."""
        corte = time.time() - ventana_s
        out = {}
        with self._lock:
            for tag, por_ancla in self._datos.items():
                vistas = [a for a, dq in por_ancla.items() if dq and dq[-1][0] >= corte]
                if vistas:
                    out[tag] = vistas
        return out

    def anclas_estado(self, ventana_s=5.0):
        ahora = time.time()
        out = {}
        with self._lock:
            for nombre, e in self._anclas.items():
                pk = [t for t in e["paquetes"] if t >= ahora - ventana_s]
                out[nombre] = {
                    "ultimo": e["ultimo"],
                    "hace_s": ahora - e["ultimo"] if e["ultimo"] else None,
                    "paquetes_s": len(pk) / ventana_s,
                    "ip": e.get("ip"),
                    "latido": e.get("latido"),
                }
        return out

    def radar(self, ventana_s=15.0):
        corte = time.time() - ventana_s
        with self._lock:
            filas = [dict(v) for v in self._radar.values() if v["t"] >= corte]
        filas.sort(key=lambda r: r["rssi"], reverse=True)
        return filas[:60]

    # usadas por calibrar.py
    def limpiar(self, ancla):
        with self._lock:
            for por_ancla in self._datos.values():
                por_ancla[ancla].clear()

    def todas(self, ancla):
        with self._lock:
            return [r for por_ancla in self._datos.values() for _, r in por_ancla[ancla]]

    # usada por el simulador
    def inyectar(self, tag, ancla, rssi):
        self._guardar(tag, ancla, rssi, ip="sim")

    # ------------------------------------------------------------ interno
    def _estado_ancla(self, ancla):
        e = self._anclas.get(ancla)
        if e is None:
            e = self._anclas[ancla] = {"ultimo": 0.0, "paquetes": deque(maxlen=2000)}
        return e

    def _guardar(self, tag, ancla, rssi, ip=None):
        t = time.time()
        with self._lock:
            self._datos[tag][ancla].append((t, rssi))
            e = self._estado_ancla(ancla)
            e["ultimo"] = t
            e["paquetes"].append(t)
            if ip:
                e["ip"] = ip

    def _latido(self, ancla, m, ip=None):
        with self._lock:
            e = self._estado_ancla(ancla)
            e["ultimo"] = time.time()
            e["latido"] = {k: v for k, v in m.items() if k != "a"}
            if ip:
                e["ip"] = ip

    def _run(self):
        asyncio.run(self._main())

    async def _main(self):
        loop = asyncio.get_running_loop()
        try:
            await loop.create_datagram_endpoint(lambda: _UDP(self), local_addr=("0.0.0.0", self.puerto))
            self.udp_estado = "ok"
            print(f"[ok] UDP escuchando en el puerto {self.puerto}")
        except OSError as e:
            self.udp_estado = f"error: {e}"
            print(f"[error] no pude abrir el puerto UDP {self.puerto}: {e}")

        if not self.usar_ble:
            await asyncio.Event().wait()
            return
        try:
            from bleak import BleakScanner

            def cb(dispositivo, adv):
                nombre = adv.local_name or dispositivo.name
                mfg = adv.manufacturer_data or {}
                tag = identificar_tag(nombre, mfg, self.det)
                with self._lock:
                    self._radar[dispositivo.address] = {
                        "direccion": dispositivo.address,
                        "nombre": nombre or "",
                        "rssi": adv.rssi,
                        "mfg": " ".join(f"{k:04X}:{bytes(v).hex()}" for k, v in mfg.items())[:60],
                        "tag": tag or "",
                        "t": time.time(),
                    }
                if tag:
                    self._guardar(tag, self.nombre_laptop, adv.rssi, ip="local")

            async with BleakScanner(detection_callback=cb, scanning_mode="active"):
                self.ble_estado = "ok"
                with self._lock:
                    self._estado_ancla(self.nombre_laptop)
                print("[ok] laptop escaneando BLE (ancla LAPTOP)")
                await asyncio.Event().wait()
        except Exception as e:
            self.ble_estado = f"sin BLE: {e}"
            print(f"[aviso] sin BLE en el laptop ({e}); sigo solo con las anclas ESP32")
            await asyncio.Event().wait()
