/*
  Arduino accessibility buttons for the kiosk (USB serial to the laptop)
  Foil pad buttons: each pin -> foil strip; opposite foil strip -> GND.
  D2 = Repeat audio, D3 = Change language, D4 = Next
  Prints "BTN:REPEAT" etc.; serial_bridge.py forwards it to the kiosk.
*/
const int PINS[] = {2, 3, 4};
const char* NAMES[] = {"REPEAT", "LANG", "NEXT"};
const int COUNT = 3;
int stable[COUNT], lastRead[COUNT];
unsigned long changedAt[COUNT];

void setup() {
  Serial.begin(9600);
  for (int i = 0; i < COUNT; i++) {
    pinMode(PINS[i], INPUT_PULLUP);
    stable[i] = lastRead[i] = HIGH;
    changedAt[i] = 0;
  }
  pinMode(LED_BUILTIN, OUTPUT);
  Serial.println("READY");
}

void loop() {
  for (int i = 0; i < COUNT; i++) {
    int r = digitalRead(PINS[i]);
    if (r != lastRead[i]) { lastRead[i] = r; changedAt[i] = millis(); }
    if (millis() - changedAt[i] > 40 && r != stable[i]) {
      stable[i] = r;
      if (r == LOW) {                          // pressed
        Serial.print("BTN:");
        Serial.println(NAMES[i]);
        digitalWrite(LED_BUILTIN, HIGH);
        delay(80);
        digitalWrite(LED_BUILTIN, LOW);
      }
    }
  }
}
