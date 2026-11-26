import time
import Jetson.GPIO as GPIO

GREEN_PIN = 11
YELLOW_PIN = 15
RED_PIN = 18

GPIO.setmode(GPIO.BOARD)
GPIO.setwarnings(False)

all_pins = [GREEN_PIN, YELLOW_PIN, RED_PIN]
GPIO.setup(all_pins, GPIO.OUT, initial=GPIO.LOW)

try:
    print("Turning ALL THREE LEDs ON together...")
    GPIO.output(all_pins, GPIO.HIGH)
    time.sleep(5)
except KeyboardInterrupt:
    print("\nStopped by user.")
finally:
    print("Turning LEDs OFF and cleaning up GPIO...")
    GPIO.output(all_pins, GPIO.LOW)
    GPIO.cleanup()
