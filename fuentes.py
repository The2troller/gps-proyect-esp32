"""
fuentes.py: recolecta RSSI separando por ancla Y por dispositivo detectado.
"""
import asyncio
import json
import threading
import time
from collections import defaultdict, deque

class _UDP(asyncio.DatagramProtocol):
    def __init__(self, guardar):
        self.guardar = guardar

    def datagram_received(self, data, addr):
        try:
            m = json.loads(data.decode())
            # Si el paquete viejo (calibracion) no trae "o", lo ignoramos
            if "o" in m and "a" in m and "rssi" in m:
                self.guardar(str(m["o"]), str(m["a"]), int(m["rssi"]))
        except Exception:
            pass 

class Fuentes:
    def __init__(self, objetivo, puerto_udp, nombre_laptop="LAPTOP"):
        self.objetivos = objetivo if isinstance(objetivo, list) else [objetivo]
        self.puerto = puerto_udp
        self.nombre_laptop = nombre_laptop
        
        # Diccionario doble: datos[celular][ancla] = deque(...)
        self._datos = defaultdict(lambda: defaultdict(lambda: deque(maxlen=5000)))
        self._lock = threading.Lock()
        self._hilo = threading.Thread(target=self._run, daemon=True)

    def iniciar(self):
        self._hilo.start()

    def ultimas(self, objetivo, ancla, ventana_s):
        corte = time.time() - ventana_s
        with self._lock:
            return [r for t, r in self._datos[objetivo][ancla] if t >= corte]

    def _guardar(self, objetivo, ancla, rssi):
        with self._lock:
            self._datos[objetivo][ancla].append((time.time(), rssi))

    def _run(self):
        asyncio.run(self._main())

    async def _main(self):
        loop = asyncio.get_running_loop()
        await loop.create_datagram_endpoint(
            lambda: _UDP(self._guardar), local_addr=("0.0.0.0", self.puerto)
        )
        print(f"[ok] UDP en el puerto {self.puerto} escuchando múltiples celulares")

        try:
            from bleak import BleakScanner
            vistos = set()

            def cb(dispositivo, adv):
                nombres = [n for n in (adv.local_name, dispositivo.name) if n]
                
                for n in nombres:
                    if n not in vistos:
                        print(f"[RADAR PC] Dispositivo BLE detectado: '{n}'")
                        vistos.add(n)
                
                # Guarda asociando explícitamente qué objetivo detectó el PC
                for obj in self.objetivos:
                    if obj in nombres:
                        self._guardar(obj, self.nombre_laptop, adv.rssi)

            async with BleakScanner(detection_callback=cb, scanning_mode="active"):
                print(f"[ok] laptop escaneando BLE, buscando {len(self.objetivos)} celulares...")
                await asyncio.Event().wait()
        except Exception as e:
            print(f"[aviso] sin BLE en el laptop ({e}); sigo solo con las anclas ESP32")
            await asyncio.Event().wait()