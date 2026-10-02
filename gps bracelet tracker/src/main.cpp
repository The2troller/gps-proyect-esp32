#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>

const char* ssid = "my_shared_connection";
const char* password = "password67";

WebServer server(80);

void handleData() {
  long rssi = WiFi.RSSI();
  int touchVal = touchRead(4);
  
  String json = "{\"rssi\": " + String(rssi) + ", \"touch\": " + String(touchVal) + "}";
  
  server.sendHeader("Access-Control-Allow-Origin", "*");
  server.send(200, "application/json", json);
}

void setup() {
  Serial.begin(115200);
  //delay(3000); // JUST FOR MINIESP32

  WiFi.begin(ssid, password);
  Serial.print("Connecting to Wi-Fi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  
  WiFi.setSleep(true); 
  
  Serial.println("\nConnected to Wi-Fi!");
  Serial.print("ESP32 IP Address: ");
  Serial.println(WiFi.localIP());

  server.on("/api/data", handleData);
  server.begin();

  server.on("/api/data", handleData);
  server.begin();
}

void loop() {
  server.handleClient();
  delay(10);
}