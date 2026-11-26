import time
import requests
import Jetson.GPIO as GPIO

# Pin mapping (GPIO.BOARD):
# 5j -> Pin 11 (Green / NORMAL)
# 7j -> Pin 18 (Red / CRITICAL)
# 9j -> Pin 15 (Yellow / WARNING)
GREEN_PIN = 11
RED_PIN = 18
YELLOW_PIN = 15

GPIO.setmode(GPIO.BOARD)
GPIO.setwarnings(False)
for pin in (GREEN_PIN, RED_PIN, YELLOW_PIN):
    GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)

USE_SIMULATED_FALLBACK = True
SIM_STEP = 0

dht_device = None
if not USE_SIMULATED_FALLBACK:
    try:
        import board
        import adafruit_dht
        dht_device = adafruit_dht.DHT11(board.D4, use_pulseio=False)
    except Exception as e:
        print(f"Warning: Could not initialize DHT11 ({e})")

def set_leds(state):
    GPIO.output(GREEN_PIN, GPIO.HIGH if state == "NORMAL" else GPIO.LOW)
    GPIO.output(YELLOW_PIN, GPIO.HIGH if state == "WARNING" else GPIO.LOW)
    GPIO.output(RED_PIN, GPIO.HIGH if state == "CRITICAL" else GPIO.LOW)

def ask_local_llm(history, state):
    prompt = (
        f"You are an edge environmental safety AI. Recent readings (Temp C, Humidity %): {history}. "
        f"System status: {state}. Give a brief 2-sentence root-cause diagnosis and 1 action."
    )
    try:
        res = requests.post(
            "http://localhost:11434/api/generate",
            json={
                "model": "tinyllama",
                "prompt": prompt,
                "stream": False,
                "options": {"num_predict": 90},
            },
            timeout=120,
        )
        res.raise_for_status()
        return res.json().get("response", "").strip()
    except Exception as e:
        return f"(LLM error: {e})"

def read_sensor():
    global SIM_STEP
    if dht_device is not None:
        try:
            temp_c = dht_device.temperature
            humidity = dht_device.humidity
            if temp_c is not None and humidity is not None:
                return float(temp_c), float(humidity)
        except RuntimeError:
            pass

    if USE_SIMULATED_FALLBACK:
        cycle = [
            (27.0, 55.0),  # NORMAL
            (31.5, 78.0),  # WARNING
            (34.5, 88.0),  # CRITICAL
            (27.5, 56.0),  # NORMAL
        ]
        temp_c, humidity = cycle[SIM_STEP % len(cycle)]
        SIM_STEP += 1
        return temp_c, humidity

    return None, None

history = []
last_state = None
failed_reads = 0

print("Edge Environmental AI Monitor Started! Press Ctrl+C to exit.")

try:
    while True:
        temp_c, humidity = read_sensor()

        if temp_c is not None and humidity is not None:
            failed_reads = 0
            reading = f"{temp_c}C / {humidity}%"
            history = (history + [reading])[-4:]

            if temp_c < 30.0 and humidity < 75.0:
                state = "NORMAL"
            elif temp_c < 33.0 and humidity < 85.0:
                state = "WARNING"
            else:
                state = "CRITICAL"

            set_leds(state)
            print(f"Temp: {temp_c}C | Humidity: {humidity}% | State: {state}")

            if state != last_state:
                print("--> Generating local LLM assessment...")
                print("AI:", ask_local_llm(history, state), "\n")
                last_state = state
        else:
            failed_reads += 1
            if failed_reads % 5 == 0:
                print("Waiting for valid DHT11 reading (retrying)...")

        time.sleep(3.0)

except KeyboardInterrupt:
    print("\nCleaning up GPIO...")
    if dht_device is not None:
        try:
            dht_device.exit()
        except Exception:
            pass
    GPIO.cleanup()
