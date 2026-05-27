#!/usr/bin/env python3
"""
UART Debug Monitor v2 - STM32 串口数据实时监控 + HTTP 推送

功能:
  1. 行级帧解析: IR传感器帧 / 单字节指令 / 自定义帧
  2. 通过 HTTP SSE 推送到本地浏览器 (含 IR 可视化面板)
  3. 支持从 Web UI 向 STM32 发送指令和自定义帧
  4. 完整的收发日志记录

已知 STM32 帧格式:
  - IR传感器:  IR1:v,IR2:v,...,IR10:v\\n  (v=0/1, ~10Hz)
  - 单字节指令: b/y/s/p/c/f
  - 视觉回显:  $type,cx,cy,area,dir*CS\\n (可能)

用法:
    python3 uart_debug.py                       # 自动探测串口
    python3 uart_debug.py --port /dev/ttyUSB0   # 指定串口
    python3 uart_debug.py --http-port 9999      # 自定义HTTP端口
    python3 uart_debug.py --log /tmp/uart.log   # 保存日志

浏览器打开: http://<板端IP>:9999
"""
import sys
import os
import time
import argparse
import threading
import json
import glob
import re
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn


# ============ UART Device Detection ============
def find_uart():
    """Auto-detect UART device: env > USB-TTL > board UART"""
    env_uart = os.environ.get('VISION_UART')
    if env_uart and os.path.exists(env_uart):
        return env_uart
    usb_devs = sorted(glob.glob('/dev/ttyUSB*'))
    if usb_devs:
        return usb_devs[0]
    if os.path.exists('/dev/ttyAS3'):
        return '/dev/ttyAS3'
    return '/dev/ttyUSB0'


# ============ Protocol Constants ============
SINGLE_BYTE_CMDS = {
    'b': 'SET_COLOR_BLUE',
    'y': 'SET_COLOR_YELLOW',
    's': 'START_DETECT',
    'p': 'PAUSE_DETECT',
    'c': 'MODE_COLLECT',
    'f': 'MODE_FIGHT',
}

# Regex for IR sensor frame: IR1:0,IR2:1,...,IR10:1
IR_FRAME_RE = re.compile(r'^IR1:\d')
# Regex for ADC/grayscale frame: A0:1221,V0:984mV,A1:1192,V1:961mV
ADC_FRAME_RE = re.compile(r'^A0:\d+,V0:\d+mV')
# Regex for vision echo: $E,320,240,5000,+25*4A
VISION_FRAME_RE = re.compile(r'^\$[ENFXBT],')


def parse_frame(line: str) -> dict:
    """Parse a complete text line into a structured message"""
    ts = datetime.now().strftime('%H:%M:%S.%f')[:-3]

    # IR sensor frame
    if IR_FRAME_RE.match(line):
        sensors = {}
        for part in line.split(','):
            if ':' in part:
                name, val = part.split(':', 1)
                sensors[name.strip()] = int(val.strip())
        return {
            'time': ts,
            'dir': 'STM32->A7Z',
            'type': 'ir',
            'frame': line,
            'sensors': sensors,
            'decoded': f'IR [{sum(sensors.values())}/{len(sensors)} active]',
        }

    # ADC/grayscale sensor frame
    if ADC_FRAME_RE.match(line):
        adc = {}
        for part in line.split(','):
            if ':' in part:
                name, val_str = part.split(':', 1)
                name = name.strip()
                val_str = val_str.strip().rstrip('mV')
                adc[name] = int(val_str)
        # Extract voltage values for threshold display
        v0 = adc.get('V0', 0)
        v1 = adc.get('V1', 0)
        # Edge thresholds from robot_control: normal 2850mV, emergency 3100mV
        warn0 = 'EDGE!' if v0 > 2850 else 'WARN' if v0 > 2000 else 'OK'
        warn1 = 'EDGE!' if v1 > 2850 else 'WARN' if v1 > 2000 else 'OK'
        return {
            'time': ts,
            'dir': 'STM32->A7Z',
            'type': 'adc',
            'frame': line,
            'adc': adc,
            'decoded': f'ADC V0={v0}mV({warn0}) V1={v1}mV({warn1})',
        }

    # Vision echo frame
    if VISION_FRAME_RE.match(line):
        return {
            'time': ts,
            'dir': 'STM32->A7Z',
            'type': 'vision',
            'frame': line,
            'decoded': f'VISION: {line}',
        }

    # Single-byte command (when received as a line)
    stripped = line.strip()
    if len(stripped) == 1 and stripped in SINGLE_BYTE_CMDS:
        return {
            'time': ts,
            'dir': 'STM32->A7Z',
            'type': 'cmd',
            'frame': stripped,
            'decoded': SINGLE_BYTE_CMDS[stripped],
        }

    # Unknown frame
    return {
        'time': ts,
        'dir': 'STM32->A7Z',
        'type': 'raw',
        'frame': line,
        'decoded': f'RAW: {line[:80]}',
    }


# ============ Thread-safe Message Stream ============
class MessageStream:
    """Thread-safe message buffer with SSE notification"""

    def __init__(self, max_size: int = 2000):
        self._msgs: list = []
        self._max = max_size
        self._seq = 0
        self._cond = threading.Condition()

    def push(self, msg: dict) -> None:
        with self._cond:
            msg['seq'] = self._seq
            self._seq += 1
            self._msgs.append(msg)
            if len(self._msgs) > self._max:
                self._msgs = self._msgs[-self._max:]
            self._cond.notify_all()

    def get_since(self, seq: int, timeout: float = 5.0) -> list:
        """Block until new messages arrive after given sequence number"""
        with self._cond:
            # Find messages with seq >= given seq
            new = [m for m in self._msgs if m['seq'] >= seq]
            if not new:
                self._cond.wait(timeout)
                new = [m for m in self._msgs if m['seq'] >= seq]
            return new

    @property
    def current_seq(self) -> int:
        with self._cond:
            return self._seq


stream = MessageStream()


# ============ UART Reader Thread ============
class UartReader:
    """Read UART data from STM32, decode and push to stream"""

    def __init__(self, port: str, baud: int, log_file=None):
        self.port = port
        self.baud = baud
        self.ser = None
        self._running = False
        self._log = None
        self._rx_count = 0
        self._tx_count = 0
        if log_file:
            self._log = open(log_file, 'a', buffering=1)
            self._log.write(f'\n=== Debug session started {datetime.now()} ===\n')

    def open(self) -> bool:
        try:
            import serial
            self.ser = serial.Serial(
                port=self.port, baudrate=self.baud,
                timeout=0.1, write_timeout=0.5
            )
            # Enable low_latency mode
            try:
                import subprocess
                subprocess.run(
                    ['stty', '-F', self.port, 'low_latency'],
                    capture_output=True, timeout=2
                )
            except Exception:
                pass
            self.ser.reset_input_buffer()
            return True
        except Exception as e:
            print(f'[ERROR] Cannot open {self.port}: {e}')
            return False

    def send_byte(self, ch: str) -> bool:
        """Send a single byte command to STM32"""
        if not self.ser:
            return False
        try:
            self.ser.write(ch.encode())
            self._tx_count += 1
            ts = datetime.now().strftime('%H:%M:%S.%f')[:-3]
            msg = {
                'time': ts,
                'dir': 'A7Z->STM32',
                'raw_hex': f'0x{ord(ch):02X}',
                'raw_char': repr(ch),
                'decoded': f'SEND: {ch}',
                'type': 'tx',
            }
            stream.push(msg)
            if self._log:
                self._log.write(f'{ts} TX 0x{ord(ch):02X} {ch!r}\n')
            return True
        except Exception as e:
            print(f'[TX ERROR] {e}')
            return False

    def send_frame(self, frame_str: str) -> bool:
        """Send a full vision frame to STM32 (for testing)"""
        if not self.ser:
            return False
        try:
            self.ser.write(frame_str.encode())
            self._tx_count += 1
            ts = datetime.now().strftime('%H:%M:%S.%f')[:-3]
            msg = {
                'time': ts,
                'dir': 'A7Z->STM32',
                'raw_hex': '',
                'raw_char': frame_str.strip(),
                'decoded': f'FRAME: {frame_str.strip()}',
                'type': 'tx',
            }
            stream.push(msg)
            if self._log:
                self._log.write(f'{ts} TX_FRAME {frame_str.strip()}\n')
            return True
        except Exception as e:
            print(f'[TX ERROR] {e}')
            return False

    def run(self):
        """Main read loop with line-based frame parsing"""
        self._running = True
        reconnect_interval = 2.0
        last_reconnect = 0.0
        line_buf = b''  # Buffer for incomplete lines

        while self._running:
            if not self.ser:
                now = time.time()
                if now - last_reconnect < reconnect_interval:
                    time.sleep(0.5)
                    continue
                last_reconnect = now
                print(f'[UART] Connecting to {self.port}...')
                if not self.open():
                    continue
                print(f'[UART] Connected: {self.port} @ {self.baud}')
                line_buf = b''

            try:
                if self.ser.in_waiting > 0:
                    data = self.ser.read(self.ser.in_waiting)
                    line_buf += data

                    # Process complete lines
                    while b'\n' in line_buf:
                        line_raw, line_buf = line_buf.split(b'\n', 1)
                        line = line_raw.decode('ascii', errors='replace').strip()
                        if not line:
                            continue

                        self._rx_count += 1

                        # Check for embedded single-byte commands
                        # (STM32 may send a bare command byte without newline)
                        if len(line) == 1 and line in SINGLE_BYTE_CMDS:
                            msg = parse_frame(line)
                        else:
                            msg = parse_frame(line)

                        stream.push(msg)

                        # Console output (compact)
                        if msg['type'] == 'ir':
                            # Only print IR changes or every 10th frame
                            if self._rx_count % 10 == 0:
                                print(f"  {msg['time']} [IR] {msg['decoded']}")
                        else:
                            print(f"  {msg['time']} [{msg['type'].upper()}] "
                                  f"{msg['decoded']}")

                        if self._log:
                            self._log.write(
                                f"{msg['time']} {msg['type']} {msg['frame']}\n"
                            )

                    # Guard against buffer overflow (incomplete frame > 4KB)
                    if len(line_buf) > 4096:
                        print('[WARN] Line buffer overflow, discarding')
                        line_buf = b''

                else:
                    # Also check for single-byte commands (no newline)
                    time.sleep(0.005)
            except Exception as e:
                print(f'[UART] Read error: {e}, reconnecting...')
                try:
                    self.ser.close()
                except Exception:
                    pass
                self.ser = None
                line_buf = b''

    def stop(self):
        self._running = False
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
        if self._log:
            self._log.write(
                f'=== Session ended {datetime.now()} '
                f'RX={self._rx_count} TX={self._tx_count} ===\n'
            )
            self._log.close()


# ============ Global UART reader reference ============
_uart_reader: UartReader = None


# ============ HTTP Server ============
HTML_PAGE = r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>UART Debug Monitor v2</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
    background: #0d1117; color: #c9d1d9;
    font-family: 'Cascadia Code', 'Consolas', 'Courier New', monospace;
    padding: 12px;
}
header {
    display: flex; align-items: center; justify-content: space-between;
    padding: 6px 0; border-bottom: 1px solid #30363d; margin-bottom: 8px;
}
h1 { font-size: 15px; color: #58a6ff; }
.badge { padding: 3px 10px; border-radius: 12px; font-size: 11px; font-weight: 600; }
.badge-on  { background: #1b4332; color: #40c057; }
.badge-off { background: #3d1f1f; color: #f03e3e; }
.top-bar {
    display: flex; gap: 12px; margin-bottom: 8px; font-size: 12px; color: #8b949e;
    align-items: center; flex-wrap: wrap;
}
.top-bar span { color: #58a6ff; }
/* IR Visualization Panel */
.ir-panel {
    display: flex; gap: 6px; align-items: center; padding: 8px 12px;
    background: #161b22; border: 1px solid #30363d; border-radius: 8px;
    margin-bottom: 8px;
}
.ir-panel .label { font-size: 11px; color: #8b949e; margin-right: 4px; }
.ir-dot {
    width: 28px; height: 28px; border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    font-size: 9px; font-weight: 700; transition: all 0.15s;
    border: 2px solid #30363d;
}
.ir-dot.off { background: #21262d; color: #484f58; border-color: #30363d; }
.ir-dot.on  { background: #f85149; color: #fff; border-color: #da3633; box-shadow: 0 0 8px #f8514966; }
.ir-dot.edge { border-color: #d29922; }
.ir-dot.edge.on { background: #d29922; border-color: #e3b341; box-shadow: 0 0 8px #d2992266; }
.ir-ts { font-size: 10px; color: #484f58; margin-left: auto; }
/* ADC/Grayscale Visualization Panel */
.adc-panel {
    display: flex; gap: 16px; align-items: center; padding: 8px 12px;
    background: #161b22; border: 1px solid #30363d; border-radius: 8px;
    margin-bottom: 8px;
}
.adc-panel .label { font-size: 11px; color: #8b949e; margin-right: 4px; white-space: nowrap; }
.adc-ch {
    display: flex; flex-direction: column; gap: 3px; min-width: 180px;
}
.adc-ch .ch-label { font-size: 10px; color: #8b949e; }
.adc-ch .ch-val { font-size: 13px; font-weight: 700; color: #c9d1d9; }
.adc-ch .ch-val.warn { color: #d29922; }
.adc-ch .ch-val.edge { color: #f85149; font-weight: 900; }
.adc-bar-bg {
    width: 100%; height: 10px; background: #21262d; border-radius: 5px; overflow: hidden;
}
.adc-bar {
    height: 100%; border-radius: 5px; transition: width 0.15s, background 0.15s;
    background: #3fb950;
}
.adc-bar.warn { background: #d29922; }
.adc-bar.edge { background: #f85149; }
.adc-ts { font-size: 10px; color: #484f58; margin-left: auto; }
/* Send panel */
.send-panel {
    display: flex; gap: 6px; margin-bottom: 8px; flex-wrap: wrap; align-items: center;
}
.send-panel button {
    padding: 3px 10px; border: 1px solid #30363d; border-radius: 6px;
    background: #21262d; color: #c9d1d9; cursor: pointer;
    font-family: inherit; font-size: 11px; transition: background 0.15s;
}
.send-panel button:hover { background: #30363d; }
.send-panel button.blue { border-color: #1f6feb; color: #58a6ff; }
.send-panel button.yellow { border-color: #d29922; color: #e3b341; }
.send-panel button.green { border-color: #238636; color: #3fb950; }
.send-panel button.red { border-color: #da3633; color: #f85149; }
.send-panel input {
    padding: 3px 8px; border: 1px solid #30363d; border-radius: 6px;
    background: #0d1117; color: #c9d1d9; font-family: inherit; font-size: 11px;
    width: 280px;
}
.send-panel label { font-size: 11px; color: #8b949e; }
/* Filter */
.filter { display: flex; gap: 8px; margin-bottom: 6px; align-items: center; font-size: 11px; }
.filter label { color: #8b949e; }
.filter input[type="checkbox"] { accent-color: #58a6ff; }
/* Log area */
#log {
    height: calc(100vh - 320px); overflow-y: auto;
    border: 1px solid #30363d; border-radius: 8px;
    padding: 6px 8px; background: #010409; font-size: 12px; line-height: 1.5;
}
.msg { white-space: pre; }
.time { color: #484f58; }
.dir-rx { color: #58a6ff; }
.dir-tx { color: #d29922; }
.t-ir { color: #8b949e; }
.t-cmd { color: #3fb950; font-weight: 700; }
.t-vision { color: #bc8cff; }
.t-raw { color: #f85149; }
.t-adc { color: #3fb950; }
.t-adc.warn { color: #d29922; }
.t-adc.edge { color: #f85149; font-weight: 700; }
</style>
</head>
<body>
<header>
    <h1>UART Debug Monitor v2</h1>
    <div>
        <span id="port" style="color:#8b949e;font-size:11px">--</span>
        <span id="status" class="badge badge-off">...</span>
    </div>
</header>
<div class="top-bar">
    Frames: <span id="rx-count">0</span> |
    TX: <span id="tx-count">0</span> |
    <span id="uptime">0s</span>
</div>

<!-- IR Sensor Visualization -->
<div class="ir-panel" id="ir-panel">
    <span class="label">IR:</span>
    <div class="ir-dot edge off" id="ir1" title="IR1 (edge-L)">1</div>
    <div class="ir-dot edge off" id="ir2" title="IR2 (edge-R)">2</div>
    <span style="color:#30363d;margin:0 2px">|</span>
    <div class="ir-dot off" id="ir3" title="IR3">3</div>
    <div class="ir-dot off" id="ir4" title="IR4">4</div>
    <div class="ir-dot off" id="ir5" title="IR5">5</div>
    <div class="ir-dot off" id="ir6" title="IR6">6</div>
    <div class="ir-dot off" id="ir7" title="IR7">7</div>
    <div class="ir-dot off" id="ir8" title="IR8">8</div>
    <div class="ir-dot off" id="ir9" title="IR9">9</div>
    <div class="ir-dot off" id="ir10" title="IR10">10</div>
    <span class="ir-ts" id="ir-ts">--</span>
</div>

<!-- ADC/Grayscale Sensor Visualization -->
<div class="adc-panel" id="adc-panel">
    <span class="label">ADC:</span>
    <div class="adc-ch">
        <div><span class="ch-label">CH0 (PC0)</span> <span class="ch-val" id="adc-v0">--mV</span> <span style="font-size:10px;color:#484f58" id="adc-a0">raw:--</span></div>
        <div class="adc-bar-bg"><div class="adc-bar" id="adc-bar0" style="width:0%"></div></div>
    </div>
    <div class="adc-ch">
        <div><span class="ch-label">CH1 (PC1)</span> <span class="ch-val" id="adc-v1">--mV</span> <span style="font-size:10px;color:#484f58" id="adc-a1">raw:--</span></div>
        <div class="adc-bar-bg"><div class="adc-bar" id="adc-bar1" style="width:0%"></div></div>
    </div>
    <span class="adc-ts" id="adc-ts">--</span>
</div>

<div class="send-panel">
    <label>CMD:</label>
    <button class="blue" onclick="sendCmd('b')">b Blue</button>
    <button class="yellow" onclick="sendCmd('y')">y Yellow</button>
    <button class="green" onclick="sendCmd('s')">s Start</button>
    <button class="red" onclick="sendCmd('p')">p Pause</button>
    <button onclick="sendCmd('c')">c Collect</button>
    <button onclick="sendCmd('f')">f Fight</button>
    <span style="color:#30363d">|</span>
    <input type="text" id="custom-frame" placeholder="$E,320,240,5000,+25">
    <button onclick="sendFrame()">Send</button>
</div>

<div class="filter">
    <label><input type="checkbox" id="filter-ir" checked> IR</label>
    <label><input type="checkbox" id="filter-adc" checked> ADC</label>
    <label><input type="checkbox" id="filter-cmd" checked> CMD</label>
    <label><input type="checkbox" id="filter-vision" checked> Vision</label>
    <label><input type="checkbox" id="filter-raw" checked> Raw</label>
    <label><input type="checkbox" id="filter-tx" checked> TX</label>
    <label><input type="checkbox" id="auto-scroll" checked> AutoScroll</label>
    <button onclick="clearLog()" style="padding:1px 6px;border:1px solid #30363d;border-radius:4px;background:#21262d;color:#c9d1d9;cursor:pointer;font-size:10px">Clear</button>
</div>
<div id="log"></div>

<script>
const logEl = document.getElementById('log');
const statusEl = document.getElementById('status');
let rxCount = 0, txCount = 0;
const t0 = Date.now();

function clearLog() { logEl.innerHTML=''; rxCount=txCount=0; updCounts(); }
function updCounts() {
    document.getElementById('rx-count').textContent = rxCount;
    document.getElementById('tx-count').textContent = txCount;
}

setInterval(()=>{
    const s=Math.floor((Date.now()-t0)/1000), m=Math.floor(s/60);
    document.getElementById('uptime').textContent = m>0?`${m}m${s%60}s`:`${s}s`;
},1000);

function sendCmd(ch) {
    fetch('/send?cmd='+ch).then(r=>r.json()).then(d=>{
        if(!d.ok) console.error('Send failed');
    });
}
function sendFrame() {
    const f=document.getElementById('custom-frame').value.trim();
    if(!f)return;
    fetch('/send_frame',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({frame:f})}).then(r=>r.json()).then(d=>{
        if(d.ok) document.getElementById('custom-frame').value='';
    });
}
document.getElementById('custom-frame').addEventListener('keydown',e=>{if(e.key==='Enter')sendFrame()});

// Update ADC/Grayscale visualization panel
function updateADC(adc) {
    const EDGE=2850, WARN=2000, MAX=3300;
    for (let i = 0; i < 2; i++) {
        const v = adc['V'+i] || 0;
        const a = adc['A'+i] || 0;
        const pct = Math.min(100, v / MAX * 100);
        const cls = v > EDGE ? 'edge' : v > WARN ? 'warn' : '';
        const valEl = document.getElementById('adc-v'+i);
        const barEl = document.getElementById('adc-bar'+i);
        const rawEl = document.getElementById('adc-a'+i);
        if (valEl) { valEl.textContent = v+'mV'; valEl.className = 'ch-val' + (cls ? ' '+cls : ''); }
        if (barEl) { barEl.style.width = pct+'%'; barEl.className = 'adc-bar' + (cls ? ' '+cls : ''); }
        if (rawEl) { rawEl.textContent = 'raw:'+a; }
    }
    document.getElementById('adc-ts').textContent = new Date().toLocaleTimeString();
}

// Update IR visualization panel
function updateIR(sensors) {
    for (let i = 1; i <= 10; i++) {
        const el = document.getElementById('ir'+i);
        const key = 'IR'+i;
        if (el && sensors[key] !== undefined) {
            const on = sensors[key] === 1;
            el.className = el.className.replace(/ (on|off)/g, '') + (on ? ' on' : ' off');
        }
    }
    document.getElementById('ir-ts').textContent = new Date().toLocaleTimeString();
}

function appendMsg(m) {
    const t = m.type;
    // Filter checks
    if (t==='ir' && !document.getElementById('filter-ir').checked) { updateIR(m.sensors||{}); rxCount++; updCounts(); return; }
    if (t==='adc' && !document.getElementById('filter-adc').checked) { updateADC(m.adc||{}); rxCount++; updCounts(); return; }
    if (t==='cmd' && !document.getElementById('filter-cmd').checked) { rxCount++; updCounts(); return; }
    if (t==='vision' && !document.getElementById('filter-vision').checked) { rxCount++; updCounts(); return; }
    if (t==='raw' && !document.getElementById('filter-raw').checked) { rxCount++; updCounts(); return; }
    if (t==='tx' && !document.getElementById('filter-tx').checked) { txCount++; updCounts(); return; }

    if (t==='tx') txCount++; else rxCount++;
    updCounts();

    // Update IR panel for IR frames
    if (t==='ir' && m.sensors) updateIR(m.sensors);
    // Update ADC panel for grayscale frames
    if (t==='adc' && m.adc) updateADC(m.adc);

    const div = document.createElement('div');
    div.className = 'msg';
    const dirCls = t==='tx'?'dir-tx':'dir-rx';
    let typeCls = 't-'+t;
    let content = m.decoded || m.frame;
    // For IR: show compact sensor string
    if (t==='ir' && m.sensors) {
        const bits = [];
        for(let i=1;i<=10;i++) bits.push(m.sensors['IR'+i]??'?');
        content = 'IR [' + bits.join('') + '] ' + m.decoded;
    }
    div.innerHTML =
        `<span class="time">${m.time}</span> `+
        `<span class="${dirCls}">${t==='tx'?'TX':'RX'}</span> `+
        `<span class="${typeCls}">${content}</span>`;
    logEl.appendChild(div);

    while(logEl.children.length>2000) logEl.removeChild(logEl.firstChild);
    if(document.getElementById('auto-scroll').checked) logEl.scrollTop=logEl.scrollHeight;
}

function connect() {
    const es = new EventSource('/events');
    es.onopen=()=>{statusEl.textContent='Connected';statusEl.className='badge badge-on';};
    es.onmessage=(e)=>{try{JSON.parse(e.data).forEach(appendMsg)}catch(err){console.error(err)}};
    es.onerror=()=>{statusEl.textContent='Disconnected';statusEl.className='badge badge-off';es.close();setTimeout(connect,3000);};
}

fetch('/info').then(r=>r.json()).then(d=>{
    document.getElementById('port').textContent=`${d.port} @ ${d.baud}`;
});
connect();
</script>
</body>
</html>'''


class DebugHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/':
            self._send_html()
        elif self.path == '/events':
            self._send_sse()
        elif self.path.startswith('/send?'):
            self._handle_send()
        elif self.path == '/info':
            self._send_info()
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == '/send_frame':
            self._handle_send_frame()
        else:
            self.send_error(404)

    def _send_html(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(HTML_PAGE.encode())

    def _send_sse(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        seq = stream.current_seq  # Start from current position
        try:
            while True:
                new_msgs = stream.get_since(seq, timeout=5.0)
                if new_msgs:
                    seq = new_msgs[-1]['seq'] + 1
                    # Strip seq from output
                    out = [{k: v for k, v in m.items() if k != 'seq'}
                           for m in new_msgs]
                    data = json.dumps(out, ensure_ascii=False)
                    self.wfile.write(f'data: {data}\n\n'.encode())
                    self.wfile.flush()
                else:
                    # Keep-alive
                    self.wfile.write(b': keepalive\n\n')
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _handle_send(self):
        """Handle single byte command send"""
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(self.path).query)
        cmd = qs.get('cmd', [''])[0]
        result = {'ok': False}
        if cmd and len(cmd) == 1 and _uart_reader:
            result['ok'] = _uart_reader.send_byte(cmd)
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(result).encode())

    def _handle_send_frame(self):
        """Handle full frame send with auto-checksum"""
        content_len = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_len)
        result = {'ok': False}
        try:
            data = json.loads(body)
            frame = data.get('frame', '')
            # Auto-compute checksum if missing
            if frame and '*' not in frame and frame.startswith('$'):
                body_str = frame[1:]  # Strip $
                cs = 0
                for b in body_str.encode():
                    cs ^= b
                frame = f'{frame}*{cs:02X}\n'
            elif not frame.endswith('\n'):
                frame += '\n'
            if _uart_reader:
                result['ok'] = _uart_reader.send_frame(frame)
        except Exception as e:
            result['error'] = str(e)
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(result).encode())

    def _send_info(self):
        info = {
            'port': _uart_reader.port if _uart_reader else '?',
            'baud': _uart_reader.baud if _uart_reader else 0,
            'rx': _uart_reader._rx_count if _uart_reader else 0,
            'tx': _uart_reader._tx_count if _uart_reader else 0,
        }
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(info).encode())

    def log_message(self, fmt, *args):
        pass  # Suppress access logs


class ThreadedHTTP(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


# ============ Main ============
def main():
    global _uart_reader

    parser = argparse.ArgumentParser(
        description='UART Debug Monitor - STM32 real-time monitor'
    )
    parser.add_argument('--port', default=None,
                        help='UART device path (auto-detect if omitted)')
    parser.add_argument('--baud', type=int, default=115200,
                        help='Baud rate (default: 115200)')
    parser.add_argument('--http-port', type=int, default=9999,
                        help='HTTP server port (default: 9999)')
    parser.add_argument('--log', default=None,
                        help='Log file path (e.g. /tmp/uart_debug.log)')
    args = parser.parse_args()

    uart_port = args.port or find_uart()

    print('=' * 50)
    print('  UART Debug Monitor')
    print('=' * 50)
    print(f'  UART : {uart_port} @ {args.baud}')
    print(f'  HTTP : http://0.0.0.0:{args.http_port}')
    if args.log:
        print(f'  Log  : {args.log}')
    print()
    print(f'  >> Open browser: http://<this-ip>:{args.http_port}')
    print('  >> Ctrl+C to stop')
    print('=' * 50)

    # Start UART reader thread
    _uart_reader = UartReader(uart_port, args.baud, args.log)
    uart_thread = threading.Thread(target=_uart_reader.run, daemon=True)
    uart_thread.start()

    # Start HTTP server (blocking)
    try:
        server = ThreadedHTTP(('0.0.0.0', args.http_port), DebugHandler)
        print(f'[HTTP] Listening on port {args.http_port}')
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n[STOP] Shutting down...')
    finally:
        _uart_reader.stop()
        print('[DONE] Debug monitor stopped.')


if __name__ == '__main__':
    main()
