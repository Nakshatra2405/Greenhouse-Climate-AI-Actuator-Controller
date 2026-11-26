import math
import re
import threading
import time
import requests
from flask import Flask, jsonify, render_template_string

app = Flask(__name__)

# True tries live DHT11 first and falls back to sample greenhouse readings on missed reads
USE_SIMULATED_FALLBACK = True
BATCH_SIZE = 30
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "tinyllama"

dht_device = None
try:
    import board
    import adafruit_dht
    dht_device = adafruit_dht.DHT11(board.D4, use_pulseio=False)
except Exception as e:
    print(f"DHT11 init warning: {e}")

latest_data = {
    "incoming_temp_c": None,
    "incoming_humidity": None,
    "incoming_source": "init",
    "incoming_updated_at": None,
    "buffer_count": 0,
    "batch_size": BATCH_SIZE,
    "agg_temp_c": None,
    "agg_humidity": None,
    "state": "WAITING",
    "actuators": {
        "sprinklers": "OFF",
        "shade_curtain": "OPEN",
        "uv_light": "OFF",
    },
    "ai_message": f"Collecting readings (0/{BATCH_SIZE}) before running 30-reading analysis...",
    "action_history": [],
}
data_lock = threading.Lock()
sim_step = 0

HTML_PAGE = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Greenhouse 30-Reading AI &amp; Actuator Monitor</title>
  <style>
    body { font-family: Arial, sans-serif; background: #0b1914; color: #f1f5f9; margin: 0; padding: 20px; }
    .container { max-width: 980px; margin: auto; display: flex; flex-direction: column; gap: 18px; }
    .card { background: #132a22; border: 1px solid #23473a; border-radius: 12px; padding: 20px; box-shadow: 0 10px 24px rgba(0,0,0,0.3); }
    .header { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; }
    .grid-2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 14px; margin-top: 14px; }
    .grid-3 { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 14px; margin-top: 14px; }
    .box { background: #0b1914; border: 1px solid #1e3d32; border-radius: 8px; padding: 14px; }
    .label { font-size: 12px; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.04em; }
    .val { font-size: 26px; font-weight: bold; margin-top: 6px; }
    .sub { font-size: 12px; color: #94a3b8; margin-top: 6px; }
    .badge { display: inline-block; padding: 6px 14px; border-radius: 999px; font-weight: bold; font-size: 13px; }
    .NORMAL { background: #166534; color: #dcfce7; }
    .WARNING { background: #854d0e; color: #fef9c3; }
    .CRITICAL { background: #991b1b; color: #fee2e2; }
    .WAITING { background: #334155; color: #e2e8f0; }

    .progress-wrap { background: #0b1914; border: 1px solid #1e3d32; border-radius: 999px; height: 12px; overflow: hidden; margin-top: 8px; }
    .progress-bar { height: 100%; width: 0%; background: #22c55e; transition: width 0.3s ease; }

    .led-row { display: flex; justify-content: space-around; gap: 12px; flex-wrap: wrap; margin-top: 12px; }
    .led-item { display: flex; align-items: center; gap: 10px; background: #0b1914; padding: 10px 16px; border-radius: 10px; border: 1px solid #1e3d32; flex: 1; min-width: 160px; }
    .bulb { width: 18px; height: 18px; border-radius: 50%; background: #263238; border: 2px solid rgba(255,255,255,0.15); transition: all 0.25s ease; }
    .bulb.green.on { background: #22c55e; box-shadow: 0 0 14px #22c55e, 0 0 26px rgba(34, 197, 94, 0.7); }
    .bulb.yellow.on { background: #eab308; box-shadow: 0 0 14px #eab308, 0 0 26px rgba(234, 179, 8, 0.7); }
    .bulb.red.on { background: #ef4444; box-shadow: 0 0 14px #ef4444, 0 0 26px rgba(239, 68, 68, 0.7); }

    .act-state { display: inline-block; margin-top: 8px; padding: 4px 10px; border-radius: 6px; font-weight: bold; font-size: 13px; }
    .act-active { background: #14532d; color: #bbf7d0; border: 1px solid #22c55e; }
    .act-closed { background: #78350f; color: #fde68a; border: 1px solid #f59e0b; }
    .act-off { background: #1e293b; color: #cbd5e1; border: 1px solid #334155; }

    table { width: 100%; border-collapse: collapse; margin-top: 12px; font-size: 13px; }
    th, td { text-align: left; padding: 10px; border-bottom: 1px solid #1e3d32; vertical-align: top; }
    th { color: #94a3b8; font-size: 12px; text-transform: uppercase; }
    .empty { color: #94a3b8; font-style: italic; padding: 12px 0; }
  </style>
</head>
<body>
  <div class="container">
    <div class="card">
      <div class="header">
        <div>
          <h2 style="margin:0;">Greenhouse Climate AI &amp; Actuator Controller</h2>
          <div class="sub">30-Reading MAD + EWMA Batch Aggregation &bull; Jetson Orin Nano</div>
        </div>
        <span id="badge" class="badge WAITING">WAITING</span>
      </div>

      <div class="grid-2">
        <div class="box">
          <div class="label">Incoming Live Temperature</div>
          <div id="in-temp" class="val">-- °C</div>
          <div id="in-meta" class="sub">Source: -- | Time: --</div>
        </div>
        <div class="box">
          <div class="label">Incoming Live Humidity</div>
          <div id="in-hum" class="val">-- %</div>
          <div id="batch-text" class="sub">Batch Window: 0 / 30 readings</div>
          <div class="progress-wrap"><div id="batch-bar" class="progress-bar"></div></div>
        </div>
        <div class="box">
          <div class="label">30-Reading Aggregated Temp</div>
          <div id="agg-temp" class="val">-- °C</div>
          <div class="sub">Filtered &amp; weighted batch value</div>
        </div>
        <div class="box">
          <div class="label">30-Reading Aggregated Humidity</div>
          <div id="agg-hum" class="val">-- %</div>
          <div class="sub">Filtered &amp; weighted batch value</div>
        </div>
      </div>

      <div class="led-row">
        <div class="led-item">
          <div id="led-green" class="bulb green"></div>
          <div>
            <div style="font-weight:bold;">Green LED</div>
            <div class="sub">NORMAL</div>
          </div>
        </div>
        <div class="led-item">
          <div id="led-yellow" class="bulb yellow"></div>
          <div>
            <div style="font-weight:bold;">Yellow LED</div>
            <div class="sub">WARNING</div>
          </div>
        </div>
        <div class="led-item">
          <div id="led-red" class="bulb red"></div>
          <div>
            <div style="font-weight:bold;">Red LED</div>
            <div class="sub">CRITICAL</div>
          </div>
        </div>
      </div>
    </div>

    <div class="card">
      <div class="label">Active Greenhouse Actuators (Last 30-Reading Decision)</div>
      <div class="grid-3">
        <div class="box">
          <div class="label">Sprinklers</div>
          <div id="act-sprinklers" class="act-state act-off">OFF</div>
          <div class="sub">Cooling &amp; humidity boost when warm/dry</div>
        </div>
        <div class="box">
          <div class="label">Shade Curtain Motors</div>
          <div id="act-curtain" class="act-state act-off">OPEN</div>
          <div class="sub">Closes under severe heat / low humidity</div>
        </div>
        <div class="box">
          <div class="label">UV Light</div>
          <div id="act-uv" class="act-state act-off">OFF</div>
          <div class="sub">Controlled light schedule outside heat stress</div>
        </div>
      </div>

      <div class="box" style="margin-top:14px;">
        <div class="label">TinyLlama Greenhouse Diagnosis &amp; Action Plan</div>
        <p id="ai" style="margin:8px 0 0 0; line-height:1.55;">Collecting readings before running 30-reading analysis...</p>
      </div>
    </div>

    <div class="card">
      <div class="label">Past History of Actions Taken (Completed 30-Reading Batches)</div>
      <div id="history-wrap">
        <div class="empty">No completed 30-reading batches yet.</div>
      </div>
    </div>
  </div>

  <script>
    function setActBadge(el, val) {
      el.textContent = val;
      el.className = 'act-state';
      if (val === 'ON') {
        el.classList.add('act-active');
      } else if (val === 'CLOSED') {
        el.classList.add('act-closed');
      } else {
        el.classList.add('act-off');
      }
    }

    function updateLeds(state) {
      document.getElementById('led-green').classList.toggle('on', state === 'NORMAL');
      document.getElementById('led-yellow').classList.toggle('on', state === 'WARNING');
      document.getElementById('led-red').classList.toggle('on', state === 'CRITICAL');
    }

    function renderHistory(rows) {
      const wrap = document.getElementById('history-wrap');
      if (!rows || rows.length === 0) {
        wrap.innerHTML = '<div class="empty">No completed 30-reading batches yet.</div>';
        return;
      }
      let html = '<table><thead><tr>' +
        '<th>Batch</th><th>Time</th><th>Aggregated</th><th>State</th><th>Actuators</th><th>AI Output</th>' +
        '</tr></thead><tbody>';
      for (const item of rows) {
        const act = item.actuators || {};
        html += '<tr>' +
          `<td>#${item.batch_num}</td>` +
          `<td>${item.time}</td>` +
          `<td>${item.agg_temp_c.toFixed(1)} °C<br>${item.agg_humidity.toFixed(1)} %</td>` +
          `<td><span class="badge ${item.state}">${item.state}</span></td>` +
          `<td>Sprinklers: <b>${act.sprinklers}</b><br>Curtain: <b>${act.shade_curtain}</b><br>UV: <b>${act.uv_light}</b></td>` +
          `<td>${item.summary}</td>` +
          '</tr>';
      }
      html += '</tbody></table>';
      wrap.innerHTML = html;
    }

    async function refresh() {
      try {
        const res = await fetch('/api/status');
        const d = await res.json();

        document.getElementById('in-temp').textContent =
          d.incoming_temp_c !== null ? `${d.incoming_temp_c.toFixed(1)} °C` : '-- °C';
        document.getElementById('in-hum').textContent =
          d.incoming_humidity !== null ? `${d.incoming_humidity.toFixed(1)} %` : '-- %';
        document.getElementById('in-meta').textContent =
          `Source: ${d.incoming_source || '--'} | Time: ${d.incoming_updated_at || '--'}`;

        const pct = Math.min(100, Math.round((d.buffer_count / d.batch_size) * 100));
        document.getElementById('batch-text').textContent =
          `Batch Window: ${d.buffer_count} / ${d.batch_size} readings`;
        document.getElementById('batch-bar').style.width = `${pct}%`;

        document.getElementById('agg-temp').textContent =
          d.agg_temp_c !== null ? `${d.agg_temp_c.toFixed(1)} °C` : '-- °C';
        document.getElementById('agg-hum').textContent =
          d.agg_humidity !== null ? `${d.agg_humidity.toFixed(1)} %` : '-- %';

        const badge = document.getElementById('badge');
        badge.textContent = d.state;
        badge.className = `badge ${d.state}`;
        updateLeds(d.state);

        const act = d.actuators || {};
        setActBadge(document.getElementById('act-sprinklers'), act.sprinklers || 'OFF');
        setActBadge(document.getElementById('act-curtain'), act.shade_curtain || 'OPEN');
        setActBadge(document.getElementById('act-uv'), act.uv_light || 'OFF');

        document.getElementById('ai').textContent = d.ai_message || '';
        renderHistory(d.action_history || []);
      } catch (err) {
        console.error('Status fetch failed:', err);
      }
    }

    refresh();
    setInterval(refresh, 2000);
  </script>
</body>
</html>
"""

def classify_greenhouse_state(temp_c, humidity):
    if 18.0 <= temp_c <= 30.0 and 50.0 <= humidity <= 75.0:
        return "NORMAL"
    elif 14.0 <= temp_c <= 34.0 and 40.0 <= humidity <= 85.0:
        return "WARNING"
    return "CRITICAL"

def mad_ewma_aggregate(values, alpha=0.12, mad_threshold=3.0):
    if not values:
        return None
    if len(values) == 1:
        return round(float(values[0]), 2)

    sorted_vals = sorted(values)
    mid = len(sorted_vals) // 2
    if len(sorted_vals) % 2 == 0:
        median = 0.5 * (sorted_vals[mid - 1] + sorted_vals[mid])
    else:
        median = sorted_vals[mid]

    abs_devs = sorted(abs(v - median) for v in values)
    mad_mid = len(abs_devs) // 2
    if len(abs_devs) % 2 == 0:
        mad = 0.5 * (abs_devs[mad_mid - 1] + abs_devs[mad_mid])
    else:
        mad = abs_devs[mad_mid]

    sigma = 1.4826 * mad
    if sigma > 1e-6:
        filtered = [v for v in values if abs(v - median) <= mad_threshold * sigma]
    else:
        filtered = list(values)

    if not filtered:
        filtered = [median]

    ewma = filtered[0]
    for v in filtered[1:]:
        ewma = alpha * v + (1.0 - alpha) * ewma

    return round(float(ewma), 2)

def fallback_actuators(temp_c, humidity, state):
    sprinklers = "OFF"
    shade_curtain = "OPEN"
    uv_light = "OFF"

    if temp_c >= 34.0 and humidity < 55.0:
        shade_curtain = "CLOSED"
        sprinklers = "ON"
        uv_light = "OFF"
    elif temp_c >= 30.0 and humidity < 60.0:
        sprinklers = "ON"
        shade_curtain = "OPEN"
        uv_light = "OFF"
    elif state == "CRITICAL" and temp_c > 34.0:
        shade_curtain = "CLOSED"
        uv_light = "OFF"
    elif state == "NORMAL":
        uv_light = "ON"

    return {
        "sprinklers": sprinklers,
        "shade_curtain": shade_curtain,
        "uv_light": uv_light,
    }

def extract_actuators_from_text(text, defaults):
    upper = text.upper()
    sp = defaults["sprinklers"]
    sc = defaults["shade_curtain"]
    uv = defaults["uv_light"]

    if re.search(r"SPRINKLERS?\s*[:\-]?\s*ON\b", upper):
        sp = "ON"
    elif re.search(r"SPRINKLERS?\s*[:\-]?\s*OFF\b", upper):
        sp = "OFF"

    if re.search(r"(SHADE\s*)?CURTAIN\s*[:\-]?\s*CLOSED\b", upper):
        sc = "CLOSED"
    elif re.search(r"(SHADE\s*)?CURTAIN\s*[:\-]?\s*OPEN\b", upper):
        sc = "OPEN"

    if re.search(r"UV(\s*LIGHT)?\s*[:\-]?\s*ON\b", upper):
        uv = "ON"
    elif re.search(r"UV(\s*LIGHT)?\s*[:\-]?\s*OFF\b", upper):
        uv = "OFF"

    return {
        "sprinklers": sp,
        "shade_curtain": sc,
        "uv_light": uv,
    }

def evaluate_batch_with_llm(batch_num, agg_temp, agg_hum, state):
    defaults = fallback_actuators(agg_temp, agg_hum, state)
    prompt = (
        f"You are a greenhouse climate assistant. Base your answer ONLY on air temperature "
        f"and relative humidity. "
        f"30-reading aggregated values: {agg_temp:.1f} C, {agg_hum:.1f}% humidity, Condition: {state}. "
        f"Write a concise 2-sentence assessment of plant/greenhouse impact and 1 practical action "
        f"mentioning sprinklers (ON/OFF), shade curtain (OPEN/CLOSED), and UV light (ON/OFF)."
    )

    try:
        res = requests.post(
            OLLAMA_URL,
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"num_predict": 120, "temperature": 0.2},
            },
            timeout=120,
        )
        res.raise_for_status()
        text = res.json().get("response", "").strip()
        if not text:
            return defaults, f"TinyLlama returned empty text (fallback: sprinklers={defaults['sprinklers']}, curtain={defaults['shade_curtain']}, UV={defaults['uv_light']})."

        actuators = extract_actuators_from_text(text, defaults)
        return actuators, text

    except Exception as e:
        return defaults, f"TinyLlama error: {e} (fallback: sprinklers={defaults['sprinklers']}, curtain={defaults['shade_curtain']}, UV={defaults['uv_light']})."

def read_dht11():
    global sim_step
    if dht_device is not None:
        try:
            temp_c = dht_device.temperature
            humidity = dht_device.humidity
            if temp_c is not None and humidity is not None:
                return float(temp_c), float(humidity), "DHT11 (Pin 7)"
        except RuntimeError:
            pass
        except Exception as e:
            print(f"DHT11 read error: {e}")

    if USE_SIMULATED_FALLBACK:
        base_cycle = [
            (25.0, 62.0),
            (26.5, 58.0),
            (31.5, 46.0),
            (35.5, 42.0),
            (24.0, 65.0),
        ]
        t, h = base_cycle[sim_step % len(base_cycle)]
        jitter_t = 0.4 * math.sin(sim_step * 0.5)
        jitter_h = 1.5 * math.cos(sim_step * 0.4)
        sim_step += 1
        return round(t + jitter_t, 1), round(h + jitter_h, 1), "Simulated Greenhouse Fallback"

    return None, None, "DHT11 (retrying)"

def sensor_worker():
    temp_window = []
    hum_window = []
    batch_num = 0

    while True:
        temp_c, humidity, source = read_dht11()
        now_str = time.strftime("%H:%M:%S")

        if temp_c is not None and humidity is not None:
            temp_window.append(temp_c)
            hum_window.append(humidity)
            buf_len = len(temp_window)

            with data_lock:
                latest_data["incoming_temp_c"] = temp_c
                latest_data["incoming_humidity"] = humidity
                latest_data["incoming_source"] = source
                latest_data["incoming_updated_at"] = now_str
                latest_data["buffer_count"] = buf_len

            print(f"[{now_str}] Incoming ({buf_len}/{BATCH_SIZE}): {temp_c:.1f}C, {humidity:.1f}% ({source})")

            if buf_len >= BATCH_SIZE:
                batch_num += 1
                agg_temp = mad_ewma_aggregate(temp_window)
                agg_hum = mad_ewma_aggregate(hum_window)
                state = classify_greenhouse_state(agg_temp, agg_hum)

                temp_window.clear()
                hum_window.clear()

                print(f"[{now_str}] Batch #{batch_num} complete -> Agg: {agg_temp:.1f}C, {agg_hum:.1f}% ({state}). Querying TinyLlama...")
                actuators, ai_msg = evaluate_batch_with_llm(batch_num, agg_temp, agg_hum, state)
                print(f"[{now_str}] TinyLlama output: {ai_msg}")

                history_entry = {
                    "batch_num": batch_num,
                    "time": now_str,
                    "agg_temp_c": agg_temp,
                    "agg_humidity": agg_hum,
                    "state": state,
                    "actuators": actuators,
                    "summary": ai_msg,
                }

                with data_lock:
                    latest_data["buffer_count"] = 0
                    latest_data["agg_temp_c"] = agg_temp
                    latest_data["agg_humidity"] = agg_hum
                    latest_data["state"] = state
                    latest_data["actuators"] = actuators
                    latest_data["ai_message"] = ai_msg
                    latest_data["action_history"] = ([history_entry] + latest_data["action_history"])[:25]
        else:
            with data_lock:
                latest_data["incoming_source"] = source
                latest_data["incoming_updated_at"] = now_str

        time.sleep(2.0)

@app.route("/")
def index():
    return render_template_string(HTML_PAGE)

@app.route("/api/status")
def status():
    with data_lock:
        return jsonify(dict(latest_data))

if __name__ == "__main__":
    try:
        worker = threading.Thread(target=sensor_worker, daemon=True)
        worker.start()
        app.run(host="0.0.0.0", port=5000, threaded=True)
    finally:
        print("\nShutting down greenhouse monitor...")
        if dht_device is not None:
            try:
                dht_device.exit()
            except Exception:
                pass
