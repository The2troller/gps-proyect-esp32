#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>

const char* ssid = "my_shared_connection";
const char* password = "password67";

WebServer server(80);

void handleData() {
  long rssi = WiFi.RSSI();
  int touchVal = touchRead(4);
  
  // Format the data cleanly as JSON: {"rssi": -55, "touch": 42}
  String json = "{\"rssi\": " + String(rssi) + ", \"touch\": " + String(touchVal) + "}";
  
  // CRITICAL: This header tells your browser it is safe to accept data from this IP
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
  
  // CRITICAL: Shut down the Wi-Fi radio between browser requests
  WiFi.setSleep(true); 
  
  Serial.println("\nConnected to Wi-Fi!");
  Serial.print("ESP32 IP Address: ");
  Serial.println(WiFi.localIP());

  server.on("/api/data", handleData);
  server.begin();
  
  
  Serial.println("\nConnected to Wi-Fi!");
  Serial.print("ESP32 IP Address: ");
  Serial.println(WiFi.localIP());


  // Only one route exists now
  server.on("/api/data", handleData);
  server.begin();
}

void loop() {
  server.handleClient();
  delay(10);
}