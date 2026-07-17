import serial

ser = serial.Serial('/dev/ttyUSB0', 9600)

try:
    while True:
        line = ser.readline().decode().strip()
        print(line)

except KeyboardInterrupt:
    print("\nGLITCH BOOTH OFFLINE")
    ser.close()
