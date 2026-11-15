#include <Arduino.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <BLEDevice.h>
#include <BLEScan.h>
#include <BLEAdvertisedDevice.h>

// ------------------------- CONFIGURACION -------------------------
const char* WIFI_SSID = "S20 FE de Sebastian";   // hotspot
const char* WIFI_PASS = "tkwn1519";              // clave del hotspot
const char* IP_LAPTOP = "255.255.255.255";       // broadcast: no depende de la IP del notebook
const uint16_t PUERTO    = 4210;

const char* ID_ANCLA = "A";  // <<<<<< PLACA 1 = "A".  Para la SEGUNDA placa, cambiar a "B".

// LISTA DE DISPOSITIVOS OBJETIVO
const int NUM_OBJETIVOS = 3;
const char* OBJETIVOS[NUM_OBJETIVOS] = {
  "Xiaomi 15T Pro",
  "S24 de Cristobal",
  "Redmi Note 13 Pro 5G"
};

const unsigned long WIFI_TIMEOUT_MS    = 15000;
const unsigned long WIFI_REINTENTO_MS  = 10000;
const unsigned long LATIDO_MS          = 5000;
const unsigned long AVISO_SIN_SENAL_MS = 5000;

#ifndef LED_BUILTIN
#define LED_BUILTIN 2
#endif

// ------------------------- VARIABLES -------------------------
WiFiUDP udp;
BLEScan* scan;

uint32_t bleVistos     = 0;
uint32_t objetivoVisto = 0;
uint32_t udpOk         = 0;
uint32_t udpFallo      = 0;
int      ultimoRssi    = 0;
unsigned long ultimoVisto    = 0;
unsigned long ultimoLatido   = 0;
unsigned long ultimoReintento = 0;
bool wifiEstabaOk = false;

// ------------------------- UTILIDADES -------------------------
const bool LED_ACTIVO_BAJO = true;
void ledOn()  { digitalWrite(LED_BUILTIN, LED_ACTIVO_BAJO ? LOW : HIGH); }
void ledOff() { digitalWrite(LED_BUILTIN, LED_ACTIVO_BAJO ? HIGH : LOW); }
void ledToggle() { digitalWrite(LED_BUILTIN, !digitalRead(LED_BUILTIN)); }

void parpadear(int veces, int ms) {
  for (int i = 0; i < veces; i++) {
    ledOn(); delay(ms); ledOff(); delay(ms);
  }
}

// Ahora recibe el nombre del objetivo detectado y lo mete en el JSON
void enviar(const char* nombreObj, int rssi) {
  char buf[128];
  snprintf(buf, sizeof(buf), "{\"a\":\"%s\",\"o\":\"%s\",\"rssi\":%d}", ID_ANCLA, nombreObj, rssi);
  udp.beginPacket(IP_LAPTOP, PUERTO);
  udp.print(buf);
  if (udp.endPacket()) udpOk += 1; else udpFallo += 1;
}

void avisarLaptop() {
  char buf[48];
  snprintf(buf, sizeof(buf), "{\"a\":\"%s\",\"hola\":1}", ID_ANCLA);
  udp.beginPacket(IP_LAPTOP, PUERTO);
  udp.print(buf);
  udp.endPacket();
}

bool conectarWiFi(bool detallar) {
  Serial.printf("[WiFi] Conectando a '%s' ", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  delay(100);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  WiFi.setTxPower(WIFI_POWER_19_5dBm);   // potencia maxima: mejor alcance en la sala

  unsigned long t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < WIFI_TIMEOUT_MS) {
    delay(300); Serial.print(".");
  }
  Serial.println();

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("**********************************");
    Serial.println("  WIFI CONECTADO  [OK]");
    Serial.printf("  IP ancla:  %s\n", WiFi.localIP().toString().c_str());
    Serial.printf("  Datos a:   %s:%u (broadcast)\n", IP_LAPTOP, (unsigned)PUERTO);
    Serial.println("**********************************");
    avisarLaptop();
    parpadear(3, 100);
    return true;
  }
  WiFi.disconnect();
  return false;
}

void imprimirEstado() {
  bool wifiOk = (WiFi.status() == WL_CONNECTED);
  unsigned long ahora = millis();
  Serial.printf("[%s] vivo %lu s | WiFi %s | BLE: %lu | Obj Vistos: %lu | UDP ok %lu, fallo %lu\n",
                ID_ANCLA, ahora / 1000, wifiOk ? "OK" : "CAIDO",
                (unsigned long)bleVistos, (unsigned long)objetivoVisto,
                (unsigned long)udpOk, (unsigned long)udpFallo);
}

// ------------------------- BLE -------------------------
class Callback : public BLEAdvertisedDeviceCallbacks {
  void onResult(BLEAdvertisedDevice d) {
    bleVistos += 1;
    if (d.haveName()) {
      std::string nombre = d.getName();
      for (int i = 0; i < NUM_OBJETIVOS; i++) {
        if (nombre == OBJETIVOS[i]) {
          int rssi = d.getRSSI();
          ultimoRssi = rssi;
          ultimoVisto = millis();
          objetivoVisto += 1;
          if (WiFi.status() == WL_CONNECTED) {
            enviar(OBJETIVOS[i], rssi); // Manda el nombre del celular detectado
            ledToggle();
          }
          break;
        }
      }
    }
  }
};

void setup() {
  Serial.begin(115200);
  pinMode(LED_BUILTIN, OUTPUT);
  ledOff();
  unsigned long tSerial = millis();
  while (!Serial && millis() - tSerial < 5000) delay(10);
  delay(500);

  WiFi.mode(WIFI_STA);
  parpadear(1, 400);
  wifiEstabaOk = conectarWiFi(true);

  BLEDevice::init("");
  scan = BLEDevice::getScan();
  scan->setAdvertisedDeviceCallbacks(new Callback(), true);
  scan->setActiveScan(true);
  scan->setInterval(100);
  scan->setWindow(50);

  ultimoReintento = millis();
  ultimoLatido = millis();
}

void loop() {
  scan->start(1, false);
  scan->clearResults();

  if (WiFi.status() != WL_CONNECTED) {
    if (wifiEstabaOk) wifiEstabaOk = false;
    ledOn();
    if (millis() - ultimoReintento >= WIFI_REINTENTO_MS) {
      wifiEstabaOk = conectarWiFi(false);
      ultimoReintento = millis();
    }
  } else if (millis() - ultimoVisto > 2000) {
    ledOff();
  }

  if (millis() - ultimoLatido >= LATIDO_MS) {
    ultimoLatido = millis();
    imprimirEstado();
  }
}
