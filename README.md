<p align="center">
  <img src="docs/logo.png" alt="Whiteout Survival autopilot" width="220" />
</p>

# Whiteout Survival autopilot

Multi-account bot: one worker per BlueStacks instance, queue and state in Redis, screen text via a separate OCR HTTP service.

<p align="center">
  <a href="https://discord.gg/G8encVpD9"><img src="https://img.shields.io/badge/Join_our_Discord-%235865F2?style=for-the-badge&logo=discord&logoColor=white" alt="Discord" /></a>
</p>

## ✨ Key Features

<div align="center">
  <i>Scenario-driven automation across the core daily loops.</i>
</div>

<br/>

<table>
<tr>
<td width="50%" valign="top">

<h3 align="center">⚔️ Combat & Events</h3>

| Feature | Description |
|:--------|:------------|
| **Squad Fight** | 12h cadence; re-deploys after every Victory until the squad finally loses |
| **Trials** | Claim trial rewards via the events flow |
| **Snowstorm** | Snowstorm event automation |
| **Event Blocks Scanner** | Detects and taps event tiles on the main city (4 slots) |
| **Arena** | Optional auto-check (disabled by default) |

</td>
<td width="50%" valign="top">

<h3 align="center">🏰 City Management</h3>

| Feature | Description |
|:--------|:------------|
| **Building Upgrades** | Pick the next upgrade and queue it through the build loop |
| **Furnace · Max Power** | Auto-tap the Max Power upgrade button |
| **Worker Assignment** | Auto-assign idle workers to open construction slots |
| **VIP · Daily** | Daily VIP login check |
| **Shop · Daily** | Auto-claim daily store rewards |

</td>
</tr>
<tr>
<td width="50%" valign="top">

<h3 align="center">🦸 Heroes</h3>

| Feature | Description |
|:--------|:------------|
| **Free Recruitments** | Daily claim of the free advanced/normal recruit (~5h) |
| **Hero Upgrades** | Auto-level and promote heroes |
| **Drain Red Dots** | Sweep every red dot on the heroes screen until clean |
| **Hero Unit Sync** | Sync hero unit data into local state |

</td>
<td width="50%" valign="top">

<h3 align="center">📦 Daily & Quality-of-Life</h3>

| Feature | Description |
|:--------|:------------|
| **Mail Gifts** | Read mail and claim all attached gifts |
| **Gift Code Hub** | Fetch + redeem the latest gift codes from the in-UI hub (per account) |
| **Ads · Auto-Claim** | Rookie Value Pack, Natalia, info popups |
| **Backpack** | Use resources, speedups, gear, bonuses on a schedule |
| **Overlay Dismissers** | Confirm / claim / box-gift / reconnect / tap-anywhere popups |
| **Onboarding Skipper** | Auto-dismiss hand-pointers, tutorial skip buttons, "where am I" prompts |
| **Chapter Tasks** | Chapter router pushes the right per-chapter scenario |
| **Exploration Rewards** | Claim exploration chests every ~4h |

</td>
</tr>
</table>

<br/>

<div align="center">

### ⚙️ Advanced Bot Capabilities

</div>

<br/>

<table>
<tr>
<td align="center" width="25%">
  <br/>
  <img src="https://img.shields.io/badge/Multi--Instance-0d1b2a?style=for-the-badge&logo=shuffle&logoColor=a8dadc" alt="Multi-Instance" />
  <br/><br/>
  <sub>One worker per BlueStacks instance — accounts run in parallel, isolated by ADB serial</sub>
  <br/><br/>
</td>
<td align="center" width="25%">
  <br/>
  <img src="https://img.shields.io/badge/YAML_DSL-0d1b2a?style=for-the-badge&logo=yaml&logoColor=a8dadc" alt="YAML DSL" />
  <br/><br/>
  <sub>Declarative scenarios: <code>match</code>, <code>click</code>, <code>while_match</code>, <code>ocr</code>, <code>cond</code>, <code>push_scenario</code></sub>
  <br/><br/>
</td>
<td align="center" width="25%">
  <br/>
  <img src="https://img.shields.io/badge/Approval_Gate-0d1b2a?style=for-the-badge&logo=shield&logoColor=a8dadc" alt="Approval Gate" />
  <br/><br/>
  <sub>Every tap can be approved in the UI before it fires — preview snapshot included</sub>
  <br/><br/>
</td>
<td align="center" width="25%">
  <br/>
  <img src="https://img.shields.io/badge/Redis_Queue-0d1b2a?style=for-the-badge&logo=redis&logoColor=a8dadc" alt="Redis Queue" />
  <br/><br/>
  <sub>Pending + history + state in Redis — restart-safe, visible from Streamlit</sub>
  <br/><br/>
</td>
</tr>
<tr>
<td align="center" width="25%">
  <br/>
  <img src="https://img.shields.io/badge/Overlay_Engine-0d1b2a?style=for-the-badge&logo=opencv&logoColor=a8dadc" alt="Overlay Engine" />
  <br/><br/>
  <sub>Template + OCR + red-dot + tab-active + white-border detectors with per-rule gates</sub>
  <br/><br/>
</td>
<td align="center" width="25%">
  <br/>
  <img src="https://img.shields.io/badge/Annotator_UI-0d1b2a?style=for-the-badge&logo=streamlit&logoColor=a8dadc" alt="Annotator UI" />
  <br/><br/>
  <sub>Capture references over ADB and label OCR regions directly in the browser</sub>
  <br/><br/>
</td>
<td align="center" width="25%">
  <br/>
  <img src="https://img.shields.io/badge/PaddleOCR-0d1b2a?style=for-the-badge&logo=docker&logoColor=a8dadc" alt="PaddleOCR" />
  <br/><br/>
  <sub>Screen text via a separate OCR HTTP service (PaddleOCR in <code>docker compose</code>)</sub>
  <br/><br/>
</td>
<td align="center" width="25%">
  <br/>
  <img src="https://img.shields.io/badge/Cron_Scheduler-0d1b2a?style=for-the-badge&logo=clockify&logoColor=a8dadc" alt="Cron Scheduler" />
  <br/><br/>
  <sub>Interval-driven scenarios publish immediately on boot, then throttle by interval</sub>
  <br/><br/>
</td>
</tr>
</table>

<br/>

---

## 🎬 Showcase & Media

<div align="center">

<details>
<summary><b>📸 Screenshots — Click to expand</b></summary>
<br/>

The Streamlit app (`uv run wos`) covers gift codes, labeling/YAML scenarios, and runtime scenario debugging.

| | |
|:---:|:---:|
| ![Gift codes](docs/gift_code.png) | ![Labeling and scenario tooling](docs/labeling.png) |
| ![Scenario runtime debug](docs/debug_mode.png) | |

</details>

</div>

<br/>

---

<br/>

## 🛠️ Installation & Setup

> [!TIP]
> Pre-built images on GitHub Container Registry — no Python / uv / paddleocr install needed.
> Want to **edit the code**? See [`CONTRIBUTOR.md`](CONTRIBUTOR.md) for the uv-based dev setup.

<br/>

### 1️⃣ Prerequisites

<div align="center">

| Requirement | Version | Download |
|:-----------:|:-------:|:--------:|
| ![Docker](https://img.shields.io/badge/Docker-2496ED?style=flat-square&logo=docker&logoColor=white) | `compose v2` | **[Get Docker](https://docs.docker.com/get-docker/)** |
| ![ADB](https://img.shields.io/badge/Android_Platform_Tools-3DDC84?style=flat-square&logo=android&logoColor=white) | latest | **[Download ADB](https://developer.android.com/tools/releases/platform-tools)** |
| ![BlueStacks](https://img.shields.io/badge/BlueStacks-1F76C9?style=flat-square&logo=bluestacks&logoColor=white) | `5` or newer | **[Download BlueStacks](https://www.bluestacks.com/)** |

</div>

> [!IMPORTANT]
> Emulator must be **720 × 1280, 320 DPI, English game language** — see [📱 Emulator Configuration](#-emulator-configuration) below.

<br/>

### 2️⃣ Run — pick your platform

<details open>
<summary><b>🍎 macOS (Docker Desktop)</b></summary>
<br/>

> **One-time:** Docker Desktop → *Settings → Resources → Network* → check **Enable host networking** (beta). Required so the bot container can talk to the host's ADB on `127.0.0.1:5037`.

```sh
# Clone (just for the compose files + config templates)
git clone https://github.com/batazor/whiteout-survival-autopilot.git
cd whiteout-survival-autopilot

# Bring up the host's ADB and confirm BlueStacks is visible
adb start-server
adb devices

# Pull and start: redis + ocr + bot
docker compose -f docker-compose.prod.yml up -d

open http://127.0.0.1:8501
```

</details>

<details>
<summary><b>🐧 Linux (Docker Engine + Compose v2)</b></summary>
<br/>

> Native Linux — `network_mode: host` works out of the box; nothing to toggle.

```sh
# Clone (just for the compose files + config templates)
git clone https://github.com/batazor/whiteout-survival-autopilot.git
cd whiteout-survival-autopilot

# Bring up the host's ADB and confirm BlueStacks is visible
adb start-server
adb devices

# Pull and start: redis + ocr + bot
docker compose -f docker-compose.prod.yml up -d

xdg-open http://127.0.0.1:8501
```

</details>

<details>
<summary><b>🪟 Windows (Docker Desktop + WSL2)</b></summary>
<br/>

> **One-time setup:**
>
> 1. Docker Desktop → *Settings → Resources → Network* → check **Enable host networking** (beta). Required so the bot container can talk to the host's ADB on `127.0.0.1:5037`.
> 2. Install [Android Platform Tools](https://developer.android.com/tools/releases/platform-tools), unzip to e.g. `%LOCALAPPDATA%\Android\Sdk\platform-tools\`, and add that folder to your `PATH` (*System Properties → Environment Variables*).
> 3. In BlueStacks: *Settings → Advanced → Android Debug Bridge* → **Enabled**.

```powershell
# Clone (just for the compose files + config templates)
git clone https://github.com/batazor/whiteout-survival-autopilot.git
cd whiteout-survival-autopilot

# Optional but recommended: run the doctor first — it tells you in plain
# English what is missing or misconfigured, and how to fix it.
.\scripts\doctor.ps1
# If blocked by execution policy:
# powershell -ExecutionPolicy Bypass -File .\scripts\doctor.ps1

# Bring up the host's ADB and confirm BlueStacks is visible
adb start-server
adb devices

# Pull and start: redis + ocr + bot
docker compose -f docker-compose.prod.yml up -d

start http://127.0.0.1:8501
```

> `scripts\doctor.ps1` is read-only: it checks Docker / ADB / BlueStacks / open ports and prints `[OK]` / `[FAIL]` / `[WARN]` with concrete fix instructions next to each line. It never installs or starts anything on its own — safe to run any time.

> If your antivirus flags `adb.exe` as PUA — whitelist the Android Platform Tools folder. ADB is a legitimate dev tool but can give root shells, so some scanners treat it as suspicious.

</details>

<br/>

#### Images that get pulled

| Service | Image | Notes |
|:--------|:------|:------|
| `bot` | `ghcr.io/batazor/whiteout-survival-autopilot/bot:latest` | Worker + scheduler + Streamlit UI. Multi-arch (amd64+arm64). |
| `ocr` | `ghcr.io/batazor/whiteout-survival-autopilot/ocr:latest` | PaddleOCR HTTP API. amd64 only (no paddlepaddle arm64 wheel for py3.13 yet). |
| `redis` | `redis:alpine` | Queue + state. |

<details>
<summary><b>🌐 How the container reaches the host's ADB server</b></summary>
<br/>

`bot` runs in `network_mode: host` so the container **shares the host's loopback** — `adb start-server` stays bound to `127.0.0.1:5037` (safe, no LAN exposure) and the container talks to it as `127.0.0.1:5037` from inside. No `adb -a`, no socat sidecar, no `host.docker.internal` indirection.

Side-effect: `bot` can't use Compose-internal DNS for the other services. `redis` and `ocr` publish to `127.0.0.1:<port>` on the host, and `bot` talks to them via those — already wired in `docker-compose.prod.yml`.

</details>

<br/>

---

<br/>

## 📱 Emulator Configuration

<div align="center">
  <i>The bot interfaces with your Android emulator via ADB. Officially supported:</i>
  <br/><br/>

  <a href="#-emulator-configuration"><img src="https://img.shields.io/badge/BlueStacks_5+-✅_Supported-4CAF50?style=for-the-badge&labelColor=1b3a5c" alt="BlueStacks" /></a>
</div>

<br/>

### Required Instance Settings

<div align="center">

| Setting | Value | Status |
|:-------:|:-----:|:------:|
| **Resolution** | `720 × 1280` (Portrait) | 🔴 **Mandatory** |
| **DPI** | `320` | 🔴 **Mandatory** |
| **Game Language** | English | 🔴 **Mandatory** |
| **ADB** | Enabled (Advanced settings → Android Debug Bridge) | 🔴 **Mandatory** |
| **ADB Serial** | Matches `bluestacks_window_title` in `config/settings.yaml` | 🔴 **Mandatory** |
| **CPU / RAM** | 2 Cores / 2 GB | 🟡 Recommended |
| **Frame Rate** | 30 FPS | 🟡 Recommended |

</div>

> [!TIP]
> In the game's settings, disable *Snowfall* and *Day/Night Cycle*, and avoid *Ultra* graphics. This considerably improves performance and visual reliability for the bot.

<br/>

---

<br/>

## 🩺 Troubleshooting

### Self-diagnosis

<details open>
<summary><b>🪟 Windows — <code>scripts\doctor.ps1</code></b></summary>
<br/>

Walks every prerequisite (Docker / ADB / BlueStacks / open ports / repo layout) and prints `[OK]` / `[FAIL]` / `[WARN]` with a one-line fix hint next to each result. Read-only — never installs / starts / stops anything. Exits non-zero on any failure so you can chain it: `.\scripts\doctor.ps1 ; if ($LASTEXITCODE -eq 0) { docker compose … }`.

```powershell
.\scripts\doctor.ps1
# Blocked by execution policy:
powershell -ExecutionPolicy Bypass -File .\scripts\doctor.ps1
```

Typical findings the doctor surfaces:
- `[FAIL] docker daemon reachable` → Docker Desktop is closed or its WSL2 backend is asleep
- `[WARN] Host networking enabled (Docker Desktop beta)` → toggle missing in *Settings → Resources → Network*
- `[FAIL] adb on PATH` → Platform Tools not unzipped to a folder in `PATH`
- `[FAIL] At least one ADB device is online` → BlueStacks ADB switch off, or `adb kill-server` needs a follow-up `adb start-server`

</details>

<details>
<summary><b>🍎 macOS / 🐧 Linux — quick checks</b></summary>
<br/>

```sh
docker info | grep -i 'server version\|host'      # daemon reachable, host-net mode
docker compose version --short                    # Compose v2 installed
adb version && adb devices                        # ADB on PATH + emulator online
which adb                                         # actual binary used (Streamlit/Cursor PATH can differ)
```

A native shell doctor for macOS / Linux is on the TODO list — for now the four commands above cover every check the Windows script does.

</details>

<br/>

### Inspecting a running stack

```sh
docker compose -f docker-compose.prod.yml ps             # service status + healthchecks
docker compose -f docker-compose.prod.yml logs -f bot    # worker + UI logs
docker compose -f docker-compose.prod.yml logs -f ocr    # PaddleOCR logs
docker compose -f docker-compose.prod.yml exec bot adb devices   # ADB visibility from inside the bot container
```

<br/>

### Common symptoms

| Symptom | Likely cause | Where to look |
|:--------|:-------------|:--------------|
| Bot UI loads, no work runs | All instances `paused=1` / `auto_paused=1` in Redis | `docker compose … logs bot` — the `game_health_watchdog` line shows why. Usually no ADB device online. |
| `tap_*` scenarios stall on "waiting for approval" | `click_approval` mode left on with the approvals page closed | Open the **Click approvals** page in the Streamlit UI or unset `wos:ui:click_approval:enabled:<inst>` in Redis. |
| Bot can't see the emulator inside the container | `network_mode: host` not active | Docker Desktop → enable Host networking (see [Installation](#-installation--setup) Windows / macOS tabs). |
| OCR returns garbage / empty text | Wrong emulator resolution or DPI | Verify [Emulator Configuration](#-emulator-configuration) — must be **720 × 1280 @ 320 DPI, English**. |
| Startup blocked with `validation acknowledged via WOS_VALIDATION_ACK` prompt | Mismatch between `area.json` / `analyze/*.yaml` / `scenarios/*.yaml` | The error message names the file + key. Set `WOS_VALIDATION_ACK=1` only as a temporary unblock — fix the YAML and remove the env var afterwards. |

<br/>

---

<br/>

## 🤝 Contributing

Editing the code? See [`CONTRIBUTOR.md`](CONTRIBUTOR.md) for the uv-based dev workflow (`uv sync`, `uv run wos`, lint, tests, building images locally).
