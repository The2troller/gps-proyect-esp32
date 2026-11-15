/*
 * FLUXA - firmware de PULSERA (emisor BLE) para ESP32-S3, proyecto PlatformIO.
 *
 * La pulsera solo anuncia por Bluetooth, no se conecta a nada ni guarda datos.
 * Cada anuncio lleva la firma FLUXA en "Manufacturer Data":
 *     Company ID 0xFFFF  +  "FX01"   ->  bytes FF FF 46 58 30 31
 * Las torres reconocen esa firma, miden el RSSI y lo envian al panel.
 *
 * El ID de cada pulsera lo define platformio.ini:
 *     pio run -e pulsera1 -t upload   (FX01)
 *     pio run -e pulsera2 -t upload   (FX02) ...
 */
#include <Arduino.h>
#include <BLEDevice.h>
#include <BLEAdvertising.h>

#ifndef ID_PULSERA_DEF
#define ID_PULSERA_DEF "FX01"
#endif
const char*    ID_PULSERA   = ID_PULSERA_DEF;
const uint16_t COMPANY_ID   = 0xFFFF;   // ID reservado para pruebas (no pertenece a ninguna empresa)
const uint16_t INTERVALO_MS = 100;      // cada cuanto anuncia (100 ms = 10 veces por segundo)

#ifndef LED_BUILTIN
#define LED_BUILTIN 2
#endif

void setup() {
  Serial.begin(115200);
  pinMode(LED_BUILTIN, OUTPUT);
  unsigned long t0 = millis();
  while (!Serial && millis() - t0 < 3000) delay(10);

  // Sin nombre: el anuncio solo lleva la firma (asi no depende del nombre ni supera 31 bytes)
  BLEDevice::init("");
  BLEDevice::setPower(ESP_PWR_LVL_P9);   // potencia maxima para mejor alcance

  // Manufacturer Data = Company ID (little endian) + ID de la pulsera en ASCII.
  // No contiene bytes 0, asi que se puede pasar como texto (compila en core 2.x y 3.x).
  char md[24];
  size_t n = 0;
  md[n++] = (char)(COMPANY_ID & 0xFF);
  md[n++] = (char)(COMPANY_ID >> 8);
  for (const char* p = ID_PULSERA; *p && n < sizeof(md) - 1; p++) md[n++] = *p;
  md[n] = 0;

  BLEAdvertisementData datos;
  datos.setFlags(0x06);                 // descubrible, solo BLE
  datos.setManufacturerData(md);

  BLEAdvertising* adv = BLEDevice::getAdvertising();
  adv->setAdvertisementData(datos);
  adv->setScanResponse(false);
  adv->setAdvertisementType(ADV_TYPE_NONCONN_IND);   // solo emite, no acepta conexiones
  uint16_t unidades = (uint16_t)(INTERVALO_MS / 0.625);
  adv->setMinInterval(unidades);
  adv->setMaxInterval(unidades + 16);
  BLEDevice::startAdvertising();

  Serial.printf("[PULSERA %s] anunciando cada ~%u ms\n", ID_PULSERA, (unsigned)INTERVALO_MS);
}

void loop() {
  // Parpadeo corto cada 2 s: indica que la pulsera esta encendida
  digitalWrite(LED_BUILTIN, HIGH);
  delay(60);
  digitalWrite(LED_BUILTIN, LOW);
  delay(1940);
  static unsigned long ultimo = 0;
  if (millis() - ultimo > 10000) {
    ultimo = millis();
    Serial.printf("[PULSERA %s] activa, %lu s\n", ID_PULSERA, millis() / 1000);
  }
}
