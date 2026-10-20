# GLITCH BOOTH

An experimental digital installation designed to explore the aesthetics of technology in states of distress while giving the user agency and control. **Glitch Booth** doesn't just take photos; it transforms the fleeting electronic anomalies into a raw, tactile urban artifact.

Powered by [**Dirtcake Studio**](https://dirtcakestudio.com)

---

## ⚡ Core Concepts

### 🔬 The Glitch Pipeline

Bare-metal input modules process raw sensor data in real-time. Custom logic, controlled interactively by the user, manipulates the visuals pre-capture, applying the precise amount of intentional disruption directly to the core image processing pipeline.

### 🧾 Physical Materialization

Each distorted digital capture is translated on the fly into a low-fi, high-contrast analog thermal receipt print. This process bridges the gap between digital decay and tangible physical ephemera, leaving you with an artifact to hold in your hand.

### 🔗 The Hybrid Loop

Every physical receipt includes an embedded direct token link. Scanning it instantly hooks you back into the online digital archive gallery, allowing you to view, inspect, or download the original uncompressed high-contrast capture.

---

## 🌐 Connected Ecosystem

- **Glitch Booth Web:** [glitchbooth.online](https://glitchbooth.online)
- **Glitch Booth Instagram:** [@glitchbooth.online](https://www.instagram.com/glitchbooth.online)

- **Glitch Booth on X:** [@glitchboothx](https://www.x.com/glitchboothx)

---

## 🛠️ Behind the Installation

**Dirtcake Studio** is a DIY-focused, tech-forward creative studio based in Istanbul. Established in 2023 by Tuğba Girgiç and Cihan Gelerli, the studio designs and produces limited-edition designer toys, interactive installations, and urban collectibles that challenge the contemporary art establishment.

## Powered by [**Dirtcake Studio**](https://www.dirtcakestudio.com)

- Website: [https://www.dirtcakestudio.com](https://www.dirtcakestudio.com)
- Instagram: [@dirtcakestudio](https://www.instagram.com/dirtcakestudio/)
- Twitter / X: [@dirtcakestudio](https://www.x.com/dirtcakestudio/)
- Behance: [dirtcakestudio](https://www.behance.net/dirtcakestudio)
- Bluesky: [dirtcakestudio.bsky.social](https://bsky.app/profile/dirtcakestudio.bsky.social)
- Youtube: [@dirtcakestudio](https://www.youtube.com/@dirtcakestudio)
- **Cihan Gelerli Instagram:** [@gelerli](https://www.instagram.com/gelerli/)

---

## OPERATION

---

### EVENT MODE CONFIGURATION

- NAKED MODE: Set EVENT_SLUG = ""
  Target: /booth_captures
  URL:    https://glitchbooth.online/p/{timestamp}

- EVENT MODE: Set EVENT_SLUG = "your-event-slug" (e.g., "synthistan2026")
  Target: /booth_captures/your-event-slug
  URL:    https://glitchbooth.online/your-event-slug/p/{timestamp}

CRITICAL CHECKLIST WHEN CHANGING EVENT_SLUG:
1. WEBSITE SLUG MATCH: 'EVENT_SLUG' MUST match 'SLUG' in 'event.config.ts' EXACTLY.
2. IMAGEKIT FOLDER: Ensure '/booth_captures/<EVENT_SLUG>/' exists on ImageKit.
3. THERMAL RECEIPT: Manually update event title, subtitle, and social handles inside 'print_booth_receipt()' below!
4. PENDING UPLOADS: Ensure 'pending_uploads.txt' is empty or flushed before switching EVENT_SLUG to prevent previous event captures syncing to the new folder.
   To quickly flush/empty in bash:
   `rm -f pending_uploads.txt`

---

## DEVELOPMENT INFO

Right now development continues in two branches:

- "main" is the stable, working branch tested with the existing hardware,

- "dev" is the actively developed, experimental branch where new ideas, features and effects are tested and hopefully implemented.


---

## ADDITIONAL INFO & CODE

- ARDUINO CODE:

"""
void setup() {
  Serial.begin(9600);

  pinMode(2, INPUT_PULLUP);
}

void loop() {

  int k1 = analogRead(A0);
  int k2 = analogRead(A1);
  int k3 = analogRead(A2);
  int k4 = analogRead(A3);
  int k5 = analogRead(A4);
  int k6 = analogRead(A5);

  int button = digitalRead(7);

  Serial.print(k1);
  Serial.print(",");

  Serial.print(k2);
  Serial.print(",");

  Serial.print(k3);
  Serial.print(",");

  Serial.print(k4);
  Serial.print(",");

  Serial.print(k5);
  Serial.print(",");

  Serial.print(k6);
  Serial.print(",");

  Serial.println(button);

  delay(300);
}

"""

- glitchbooth.service

glitchbooth.service is used for running the glitchbooth in kiosk mode.

- limit-captures.sh

This now redundant file was used to keep the capture number and the size of the captures folder under control. Now the same functionality is implemented in server.py, but this script can still be used to limit the number of captures for size limited environments.

- test files

These files are used for hardware checks before setup and can be used for troubleshooting. Later, custom test files used during development will be added here for documentation and referencing.

This folder is not critical for the app to run.

---

## Current Development

# GLITCHBOOTH Effects Overhaul: Definitive Plan (v6.2)

**Status legend**

| Mark | Meaning |
| :-- | :-- |
| ✅ | Decided by the owner. Safe to plan and (after approval of a change proposal, see R0) implement. |
| 🔶 | Proposed default. Needs the owner's OK before implementation. |
| ⬜ | Open question. Only the owner can answer. Do not guess. See §11. |

**What this document is.** It replaces the "Current Development" masterplan in `readme.md` once the owner approves it. v6 supersedes v5: it contains the owner's decisions, the owner's phase order, the event schedule, and facts from the files received since (`telemetry.py`, `index.html`, `glitchbooth.service`).

**Changes since v6.1 (owner's answers, the owner-tuned Melt, the console photo, the benchmark output):**
- Owner answers recorded: interim knob wiring handled by stickers (Q1); `no_glitch` = every knob at or below 50 (Q3); the website shows a NO GLITCH bar plus only currently mapped effects (Q9); scratch experiments on the assistant's own computer are allowed, edits to the project through any editor or tool are not (Q6, rule R0); error output is console-only (Q8).
- Melt is now the **owner-tuned version** (8-px drips, neon ramp to full at the tip, 100 drips): §6.1, Appendix A. The old Melt tests fail against it by design and need updating (proposal P1-01); the benchmark verdict is advisory (P1-02).
- New facts about the display loop and the "Frame copy latency" warnings (§3.9) and a no-code experiment to find their cause (§10).
- Idea from the owner: Data Mosh **after** Melt; planned as a live A/B/C test (§6.3, D23).

**Earlier (v6 to v6.1):** corruption content settled (squares always noise; bands 90/6/4); website dashboard analyzed (§3.8, no website change needed if the §7 constraints hold); `index.html` is a backup only; no recorded footage of people.

**Inputs verified:** `server.py` (Phase 1 under development), `pygame_display.py`, `readme.md`, `telemetry.py`, `index.html`, `glitchbooth.service`, `StatsDashboard.tsx` (the website's stats component).

**Who reads what**

- **Owner:** §1, §4, §5.9 (what each knob does at each stage), §8.0 (event schedule), §10, §11.
- **AI assistant:** Entire Document, especially §2 and §3 in full, then §6 (effect specs) and the one phase you were assigned in §8. Do not start any other phase except the one you are currently working on specified by the owner.

---

## 1. Owner intent

| # | Intent | Handled in |
| :-- | :-- | :-- |
| G1 | Modular **effects pool**: any effect on any of the six knobs; **never the same effect on two knobs**, and **no similar effects together** (one of `pixel_damage`, `packet_corruption`, `pixelation`; the same rule for future similar effects). | §5.3, Phase 4 |
| G2 | Processing order belongs to the effect, not the knob. The current order is approved; one idea (Data Mosh after Melt) will be tested live (§6.3). | §5.2 |
| G3 | Phased; after every phase the booth works without problems. | §8, §9 |
| G4 | **Melt**: add neon colors, possibly improve; tune after seeing it on the machine. | §6.1, Phase 1 |
| G5 | **Decouple Data Mosh** from packet corruption and improve it: **frame-hold + color-plane bleed** if viable. | §6.3, Phase 2 |
| G6 | **Fuse pixelation and packet corruption** into the new Pixel Damage. Knob 0–512: corruption only (squares always noise; bands about 90% noise, 6% black, 4% white ✅). 512–1023: corruption keeps getting stronger and gradual pixelation is added, **5 levels**. Standalone pixelation stays as it is. | §6.4, §6.5, Phase 3 |
| G7 | **Posterize** with the Melt neon palette (blacks untouched, other colors neon, 2–3 px noisy glitchy outlines, glitchy not pop art). **Visual decisions come after the event.** | §6.6, Phase 6 |
| G8 | **Telemetry** updated for the modular system. Total capture count continues; effect-usage stats may restart; an effect counts as used above raw 50; keep `no_glitch` (every knob at or below 50). | §7, Phase 5 |
| G9 | **Strong visuals with high performance** for all effects. Aesthetics first; improve the strength-vs-quality balancer. | §2 R2, §5.6 |
| G10 | **Never change code before the owner approves.** | §2 R0 |
| G11 | Next event in **5 days**, with a **1-day freeze** before it. Finish Melt, Data Mosh and the fusion before then. | §8.0 |
| G12 | The plan guides weaker AI assistants and the owner when the assistants cannot help. Ask when unsure. | §2, §10 |

**Final default mapping (✅):** knob 0 ASCII Matrix, 1 Signal Drift, 2 Posterize, 3 Pixel Damage (new fusion), 4 Melt, 5 Chroma Shift. Data Mosh stays in the pool, unassigned by default. **Remapping happens by restart only (✅).**

---

## 2. Rules for AI assistants (non-negotiable)

**R0. No code change without the owner's approval.** Procedure:
1. The assistant posts a **Change Proposal** (template in §9.5): code or files, exact scope, visible behavior change, risks, tests, rollback.
2. The owner answers `APPROVED <proposal id>` if present or approves the changes (or edits the proposal). **Silence is not approval.**
3. Only then and if allowed specifically are files changed, and only the files and scope in the approved proposal. Anything new found along the way becomes a new proposal.
4. **Allowed without approval (✅ owner):** reading, analysis, reports, and experiments the AI runs **on its own computer** (for example scripts that render preview images or measure something), clearly labeled and never deployed. **Not allowed without approval:** any change to any file in the project through **any editor or tool** (VS Code, Antigravity, Codex, nano, micro, shell commands, scripts, and so on).

**R1. Ask, do not assume.** If intent or the desired look is unclear, or a fact cannot be verified from the files given, stop and ask. List any unavoidable assumption in the report.

**R2. Aesthetics first.** Do not reduce visual richness (detail, randomness, neon intensity, effect range, drip counts, color count) to gain speed without written owner approval. **Optimization ladder:** (1) remove waste that does not change the picture (allocations, redundant copies, Python loops that can be vectorized); (2) cheaper but equivalent operations (lookup tables, in-place ops, integer math); (3) lower internal resolution, only with approval after viewing the result; (4) reduce caps or quality, only with approval.

**R3. No silent behavior change.** Change only what the approved proposal lists. A visible change outside scope means stop and report.

**R4. Evidence over claims.** "Done", "verified", "implemented" need pasted command output. Never write them into `readme.md` without it. State what was **not** run (especially anything that needs the Raspberry Pi).

**R5. One source of truth.** No copies such as `_v2`, `_1`, `_final`. Edit the real file; git is the history. One test file per purpose.

**R6. Version control belongs to the owner.** The AI never runs `git push` and never creates commits or tags. It writes the exact commands; the owner runs them.

**R7. Tunables live in one block.** Every number that shapes a look is a named, commented constant with its allowed range, in one tunables block (in `server.py` until Phase 4, then `effects_config.py`). No magic numbers inside effect code.

**R8. Hot-path discipline.** Inside the frame loop: if possible, no allocation of frame-sized arrays, C-contiguous `uint8` buffers, preallocated scratch. Never overrides R2.

**R9. Determinism for tests.** Seed `np.random`, `random` **and** `cv2.setRNGSeed` (OpenCV's `cv2.randu` has its own generator). Synchronize import-time random state (for example `RANDOM_SIGN_POOL`) when two copies of the module are compared.

**R10. Thread and hardware rules.** All pixel work runs in the `background_loop` thread. Effects never block and never do I/O. Telemetry writes are asynchronous. Printing, upload, countdown, audio, QR and the capture state machine are not touched by the overhaul.

**R11. Shared buffers.** A scratch buffer may be shared only if all users run sequentially on the same thread, none relies on its contents across stages or frames, and every user is listed in the buffer inventory.

**R12. Phase protocol.** One phase at a time. The owner tags `pre-phaseN` first. After finishing: tests, A/B guard, aesthetic gate (visual phases), booth smoke test, report (§9.4). Do not start the next phase until the owner writes "accepted".

**R13. Forward compatibility (pre-registry phases).** Until the registry exists (Phase 4), new effect code is still written as **self-contained functions with the handler contract of §5.4** (own state, `reset()`, tunables at the top, no calls to other effects), called from the existing pipeline at the existing stage position. Phase 4 then only wires them.

**R14. Fail safe.** An invalid configuration must never leave the booth dead. The service restarts on failure every 2 seconds (`Restart=on-failure`), so a crash loop is the worst outcome; see §5.3.

**R15. Missing information.** If a needed file was not given, ask. Do not reconstruct it from memory.

**R16. No new dependencies** (pip or system packages) without approval.

---

## 3. Ground truth: how the booth works today (verified in the files)

### 3.1 Pipeline order and knob wiring today

`apply_glitch_pipeline()` runs these stages every frame, in this order:

| Order | Stage | Knob | Notes |
| :-- | :-- | :-- | :-- |
| 1 | **Packet corruption** (noise blocks, noise/black/white vertical bands on a 15×26 grid) | **2** | Energized by Data Mosh (3.2) |
| 2 | **Pixelation** ("Pixel Damage") | **3** | Continuous block size |
| 3 | **Chroma Shift** (red shift, ghost blend, slice shifts) | **5** | |
| 4 | **Data Mosh** (block displacement from motion) | **2** (strength = knob²) | Same knob as corruption |
| 5 | **Signal Drift** | **1** | |
| 6 | **Melt** (Phase 1 version) | **4** | |
| 7 | **ASCII Matrix** | **0** | |
| 8 | **CRT overlay** | none (switch) | Last; not an effect |

There is **no Posterize** in the code today.

### 3.2 Behavior details the plan depends on

- **Packet corruption.** Grid 15 rows × 26 columns (cells 32 px tall × 24 px wide; the right-most 16 px of the frame are never touched). Each frame, if the knob is above 0.02, a spawn happens with probability `knob × 0.33`. A spawn is 80% a square of 1–3 cells (always **noise**) or 20% a vertical band 1–2 cells wide (60% noise, 30% black, 10% white). Energy decays ×0.9 per frame; blocks blend with the picture by energy. Below 0.01 the state is cleared.
- **The coupling.** `CORRUPTION_ENERGY[DATAMOSH_VX != 0] += 0.1`: every cell where Data Mosh has a motion vector also feeds corruption energy. This is the "fusion" on knob 2.
- **Data Mosh.** Motion is sampled at each cell's center (difference to the previous frame). A moving cell gets a random horizontal shift of ±4 px that decays; its content is replaced by history content (eroded edges 0–2 px); history is blended about 63% new / 37% old. Vectors update every second call. **Only horizontal vectors exist.**
- **Pixelation.** Block size `int(12 + knob × 9)`: **12 to 21 px, continuous**, nearest-neighbor, through the scratch buffer `COMPRESSION_STAGE_BUFFER`.
- **ASCII** uses the knob value as strength and as the blend factor with the camera image.
- **Dormancy thresholds today** (keep until changed on purpose): corruption > 0.02 (clears below 0.01); pixelation > 0; Chroma Shift > 0.05; Data Mosh when knob² > 0.02 (knob above about 0.14); Signal Drift > 0; Melt > 0.03; ASCII > 0.02.

### 3.3 The strength-vs-quality balancer (the "governor")

`effect_intensity` is a weighted sum **by knob index**: knob0 0.10 (ASCII), knob1 0.07 (drift), knob2 0.27 (corruption + datamosh), knob3 0.07 (pixelation), knob4 0.42 (melt), knob5 0.07 (chroma). It drives:
1. the **loop frame rate**: ≥ 0.75 → 10 FPS, ≥ 0.55 → 12 FPS, otherwise 15 FPS;
2. the **stream JPEG quality**: `65 − 30 × intensity`, clamped to 35–65;
3. the **stream preview width** (downscaled at ≥ 0.55).

Observations (to verify, not yet measured): the Pygame kiosk copies `UI_DISPLAY_BUFFER` directly, so items 2 and 3 appear to affect only the web stream; the balancer is **predicted** from knob positions, not **measured**, so it can lower the frame rate even when the Pi has headroom; its weights belong to today's effects; and the camera is requested at 10 FPS (`CAP_PROP_FPS`), so a 12–15 FPS loop may process the same camera frame more than once, which matters for motion-based effects like Data Mosh. The improvement program is in §5.6. 

### 3.4 Input hardware

The Arduino (installed sketch = the one in `readme.md`, ✅) sends six 10-bit knob values and a button every **300 ms** at 9600 baud. The server reads **one line per frame**, with no smoothing. This is accepted for now (re-flash only if very necessary, ✅). The readme sketch sets `pinMode(2, INPUT_PULLUP)` but reads `digitalRead(7)`; it works today, so it is left alone. **Never change only one side** (sketch or reader) if the update rate is ever changed: a faster Arduino needs a reader that drains the buffer.

### 3.5 Where knob labels live

- `pygame_display.py` (`KNOB_ROWS`): ASCII Matrix, Signal Drift, Pixel Damage, Melt, Data Corruption, Chroma Shift.
- `index.html` (served at `/`; label rows hard-coded): "EMPTY" for knob 0 (a mishap ✅), then Signal Drift, Pixel Damage, Melt, Data Corruption, Chroma Shift. It is **not in active use**: it is a **backup** for when the dedicated screen breaks, so it must still be correct if that happens. Its labels are updated together with the kiosk labels (as an approved change) and "EMPTY" becomes "ASCII Matrix". The physical knobs carry **stickers** (✅), which are easy to change.
- `telemetry.py`: `ALL_EFFECTS` is indexed by knob number (see 3.6).

### 3.6 Telemetry today (`telemetry.py`, "v2.7")

- `record_capture(session_id, knobs, *args, **kwargs)` is called at capture; it updates in-memory stats and queues a record `{ts, session_id, knobs}` for an append-only ledger `telemetry/capture_log.jsonl`. It accepts extra arguments, so it can be extended without breaking the caller.
- **Effect identity comes from the knob index**: `ALL_EFFECTS = [ascii_matrix, signal_drift, data_corruption, pixel_damage, melt, chroma_shift, no_glitch]`. An effect counts as "used" when its knob is **≥ 10** (`KNOB_ACTIVATION_THRESHOLD`); if **no** knob is ≥ 10 the capture counts as `no_glitch`. This is why remapping breaks the stats.
- **Startup replays the entire ledger** and recomputes all counters from the knob values, using the *current* index names. Old records therefore get reinterpreted whenever the mapping changes.
- `compile_summary_stats()` writes `stats.json` every 120 s (keys: `total_captures`, `uptime_hours`, `first_capture_iso`, `last_capture_iso`, `effects_popularity` with `<name>_usage_pct` entries, `most_popular_combo`). The write is **not atomic** (a power cut mid-write can leave a broken file) and is done **outside** the lock that the uploader uses (the uploader can read a half-written file). The sync worker uploads it to ImageKit `/telemetry/stats.json` (overwritten each time), where `glitchbooth.online` reads it (§3.8).
- The popularity percentages divide by the total number of effect "uses" (a capture with three effects counts three times), not by the number of captures.
- `/stats` and `/api/stats` in `server.py` read `stats.json`; `/api/stats` uses `total_captures`.

### 3.7 Runtime facts (`glitchbooth.service`)

- Runs `/home/glitchbooth/glitchbooth/.venv/bin/python pygame_display.py` as user `glitchbooth` with `WorkingDirectory=/home/glitchbooth/glitchbooth`. The Flask/Waitress backend runs **inside** that process.
- `Restart=on-failure`, `RestartSec=2`: a crash at startup becomes a crash loop.
- `StandardOutput=tty` on `/dev/tty1`: **program output goes to the booth's console only**. Confirmed on the Pi: `journalctl -u glitchbooth.service` returns "-- No entries --" and there are no log files. Nothing is kept after the console scrolls away (proposal in §11.3).
- `Conflicts=getty@tty1.service`; SDL uses `kmsdrm` (no desktop).

### 3.8 The website dashboard (`StatsDashboard.tsx`, owner-provided)

- It downloads `https://ik.imagekit.io/w6lsfsw8j/telemetry/stats.json` (the file `telemetry.py` uploads) every 2 minutes, bypassing caches.
- It reads `total_captures`, `uptime_hours`, `first_capture_iso`, `last_capture_iso` and **every entry of `effects_popularity`**. It draws **one bar per entry**, so new or renamed effects appear automatically. The label is the key with `_usage_pct` removed, underscores turned into spaces, in capitals (`pixel_damage_usage_pct` shows as "PIXEL DAMAGE"). The "dominant algorithm" box shows the entry with the highest percentage (ties alphabetical).
- Values must be **plain numbers**: the bar width is `percentage%` and the sort subtracts the values. A nested object or a text value would break the display.
- `no_glitch` is one of the entries, so it appears as a bar called "NO GLITCH" and can become the "dominant algorithm" when it is the largest.
- `most_popular_combo` is declared but not displayed by this component (other pages may use it; unknown).
- Declared but never produced by `telemetry.py`: `successful_uploads`, `failed_uploads`, `prints_completed`. Their displays are commented out, so nothing breaks.
- A hard-coded note says the analytics have been collected "since July 1st, 2026". After the effect-stats reset that is true for captures and uptime but not for the effect percentages.
- If the stats file is ever uploaded half-written (possible today, see 3.6), the page shows its "TELEMETRY_MATRIX_OFFLINE" panel until the next refresh.
- Not verified: whether other pages of the site read `stats.json` too.

### 3.9 Display loop and frame hand-off (`pygame_display.py`, `server.py`)

**ALREADY IMPLEMENTED!**

- The display targets **60 FPS**; the pipeline runs at 10–15 FPS. On every display tick (4 to 6 per pipeline frame) the display copies the 640×480 frame from `UI_DISPLAY_BUFFER`, wraps it as a surface, mirrors it, scales it (640×480 to 640×480) and blits it, **even if no new pipeline frame has arrived**.
- The message `[PERF WARNING] Frame copy latency spike` is printed by the display thread whenever that copy takes more than **2 ms**. In my test on a PC the same copy takes about 0.12 ms, so the 2.2–3.9 ms seen in the console photo are most likely **waiting** (competition for the CPU or for Python's global interpreter lock with the pipeline thread), not the copy itself. **Not proven on the Pi**; the experiment in §10 will show whether Melt or another effect is responsible.
- A warning is printed for **every** such tick (the photo shows dozens in a row), which buries real messages on the console.
- The server writes `UI_DISPLAY_BUFFER` without holding `frame_lock` (the lock is only taken for the JPEG hand-off), so the display can occasionally copy a half-updated frame (one torn display frame). Not observed; low severity.
- `FRAME_ID` increases only when a JPEG is encoded, so it cannot tell the display that a new frame is ready.

---

## 4. Corrections to the previous masterplan (v4.1)

| # | Old plan | Problem | This plan |
| :-- | :-- | :-- | :-- |
| C1 | Default mapping 0 Drift, 1 Chroma, 2 Posterize, 3 Pixel Damage, 4 Melt, 5 ASCII | Would silently rewire the knobs the booth has today | Interim wiring is explicit (§5.9); final default is the owner's (§1) |
| C2 | `packet_corruption` and `pixelate` "internal sub-effects" rejected as knob targets | Contradicts the owner's pool | Both are pool effects; similar effects are kept apart by **families** (§5.3) |
| C3 | Pixelation tiers of 2, 4, 8 px | Today's blocks are 12–21 px | Levels relative to today's look; existing scratch buffer kept (§6.5) |
| C4 | Corruption held at maximum in the second half | Owner wants it to keep growing | §6.5 |
| C5 | Posterize = Canny + quantization | Almost no definition; Canny finds photo edges ("simple neon edge detection") | §6.6, outlines follow color-block boundaries |
| C6 | "Visually improved Data Mosh" | Undefined | Owner chose frame-hold + color-plane bleed (§6.3) |
| C7 | Phase 1 "code-complete" with only timing/invariant criteria | No visual acceptance; neon is only 15% on two outer columns of each drip | Phase 1 is **implemented, not accepted** until the owner has seen it on the machine |
| C8 | Telemetry = new `capture_log_v2.jsonl` and image buffers | Ignores the real module: knob-index naming, ledger replay, non-atomic `stats.json`, uploaded stats; images are already saved elsewhere | §7 |
| C9 | Governor weights "derived from mapped effects" | Undefined, and the weights belong to today's effects | §5.6 |
| C10 | Performance targets with no visual gate | Wrong priority | Aesthetic gate in every visual phase (§9.2) |
| C11 | Registry first | The owner's priority is the event | Owner's order (§8), with R13 so nothing is rewritten later |
| C12 | Journal-based troubleshooting (in plan v5) | Service logs to the tty, not the journal | §3.7, §10 |

---

## 5. Target architecture

### 5.1 The effects pool ✅

| ID | Display name | Notes |
| :-- | :-- | :-- |
| `packet_corruption` | Packet Corruption | Standalone; no Data Mosh coupling (family `pixel_damage`) |
| `pixelation` | Pixelation | Standalone; today's continuous 12–21 px (family `pixel_damage`) |
| `pixel_damage` | Pixel Damage | **New fusion** of corruption + pixelation (family `pixel_damage`) |
| `posterize` | Posterize | New (Phase 6) |
| `chroma_shift` | Chroma Shift | Unchanged |
| `datamosh` | Data Mosh | Decoupled and improved (Phase 2) |
| `signal_drift` | Signal Drift | Unchanged |
| `melt` | Melt | Phase 1 version |
| `ascii_matrix` | ASCII Matrix | Unchanged |

The CRT overlay is not in the pool. The old fused corruption+datamosh effect is **git history only** (✅); it is not kept working.

### 5.2 Fixed processing order ✅ (🔶 position of Posterize is the owner's: after pixel effects, before Chroma Shift ✅)

`STAGE_ORDER`: `pixel_damage`, `packet_corruption`, `pixelation`, `posterize`, `chroma_shift`, `datamosh`, `signal_drift`, `melt`, `ascii_matrix`, then CRT.
The three family members occupy the same slot (only one of them can be active). Which knob an effect is on never changes the order. **Open idea (D23):** Data Mosh may look better **after Melt**; it will be decided by a live test (§6.3).

### 5.3 Mapping, validation and families

- **Mapping:** knob index (0–5) → effect ID or none. **Restart-only remapping ✅**: edit the mapping in `effects_config.py`, restart the service. Named profiles are allowed.
- **Rules checked at startup** (and by `python3 effects_config.py --check`, no hardware needed): every knob present; every ID exists in the pool **and has a handler**; **no effect on two knobs**; **no two effects of the same family** (✅: "no similar effects together", also for future effects: each new effect must declare its family when it is added to the pool); palette values valid; response curves valid.
- **Fail safe 🔶:** on a validation error the booth logs the error and starts with the built-in known-good default mapping instead of exiting. Reason: the service restarts every 2 s on failure, so an exit becomes a crash loop at an event.
- Handlers register in `server.py` (`EFFECT_HANDLERS`); `effects_config.py` stays declarative (no execution code, no hardware libraries).

### 5.4 Handler contract ✅

- **Input:** raw knob value (integer 0–1023) and frame time. The handler converts raw to strength with its own response curve. Existing effects keep their original formulas until the owner asks otherwise.
- **Output:** modifies `MUTABLE_PROCESSING_BUFFER` in place.
- **State:** effects that remember something (corruption grid, Data Mosh history) provide `reset()`; it is called when the effect becomes dormant, at startup, and at the end of warm-up.
- **Dormancy:** at or below its threshold an effect does nothing and costs nothing. Today's exact comparisons are preserved until deliberately changed.
- **No handler calls another handler.** Shared logic lives in plain helper functions; the fusion calls the same helpers as the standalone effects.
- **Duplicate camera frames:** motion-based effects must behave sensibly when the camera frame did not change since the last loop (see 3.3).
- Each effect documents which buffers it owns, what randomness it uses, and what `reset()` clears.

### 5.5 Intensity resolution ✅

`EFFECT_TO_KNOB` is built once at startup (Phase 4). Unmapped effects get raw 0 and are skipped. Add hysteresis near dormant thresholds only if the Phase 4 knob-noise probe shows flicker.

### 5.6 Balancer improvement program 🔶 (proposals only; each needs approval; measure first)

Goal (G9): the strongest visuals the Pi can sustain.
1. **Measure** (Phase 0-lite and Phase 4): full-pipeline frame time at all-knobs-high; unique camera frames per second; cost of each effect alone.
2. **Measured, not predicted:** choose the frame-rate tier from the recent measured frame time (for example the 90th percentile of the last 30 frames, with headroom), keeping today's predicted tiers as a safety floor, so heavy-but-affordable combinations keep 15 FPS.
3. **Per-effect quality ladders** that degrade **invisibly first** (R2 ladder steps 1–2) and visibly only with approval (for example the Melt drip cap).
4. **Stream-only degradation:** keep JPEG quality and preview downscale for the web stream only, and verify the kiosk is not affected.
5. **Weights keyed by effect**, not by knob, so frame pacing no longer depends on which knob an effect sits on (Phase 4). Initial weights come from today's table; the old 0.42 for knob 2 is split between the Data Mosh and corruption costs measured in Phase 2/3.

### 5.7 Code layout ✅ (reversible)

`effects_config.py` declarative; handlers stay in `server.py`; `pygame_display.py` and `index.html` take labels from a read-only `/api/mapping` endpoint (Phase 4). Moving handlers into a package is possible later and is not prevented by anything here.

### 5.8 Memory ✅

Memory is ample; the inventory exists to avoid reallocation churn, not to limit looks. The old plan's pixelation tier buffers are not needed. Keep a buffer table (name, shape, owner effect, sharing, lifetime), updated in each phase that adds or removes a buffer.

### 5.9 What each knob does at each stage ✅

| Stage of the project | k0 | k1 | k2 | k3 | k4 | k5 |
| :-- | :-- | :-- | :-- | :-- | :-- | :-- |
| Today (main) | ASCII | Drift | Data Corruption (corruption+mosh) | Pixel Damage (pixelation) | Melt (old) | Chroma |
| After Phase 1 | ASCII | Drift | Data Corruption | Pixel Damage | **Melt (new)** | Chroma |
| After Phase 2 | ASCII | Drift | **Data Mosh (new)** | Pixel Damage (pixelation) | Melt | Chroma |
| After Phase 3 (**event build**) | ASCII | Drift | Data Mosh | **Pixel Damage (new fusion)** | Melt | Chroma |
| After Phase 4 (config-driven) | same as Phase 3, now defined in `effects_config.py` | | | | | |
| After Phase 6 (**final default**) | ASCII | Drift | **Posterize** | Pixel Damage (fusion) | Melt | Chroma |

Note for Phases 2–3: after Phase 2, **corruption has no knob** until the fusion exists (Phase 3). The booth still works. The knobs carry **stickers** (✅), so relabeling for the event is cheap; the kiosk and `index.html` labels are updated in the same approved change. The owner accepted this interim wiring (D22) and relabels the stickers by hand.

---

## 6. Effect specifications

Numbers are **starting values** unless marked ✅; they are tunable and judged by eye (§9.2), not by tests.

### 6.1 Melt (Phase 1: implemented, accepted, end of phase, might have to come back to this to tune and change further down the overhaul in the future.)

**Today (the current version of server.py).** Bright points in sampled columns become drip origins (column stride 6 / 3 / 2 by strength). Each drip is **8 columns wide** (`MELT_DRIP_WIDTH`), sampled from a pre-melt snapshot and stretched along its length; capped at `MAX_MELT_DRIPS = 100`; longer drips are drawn last. **Neon:** none for the first 75% of a drip's length, rising linearly to full neon at 95%, across the whole width (each neon color each drip uses a mix of two palette colors).
**Measured on the Pi 5** (synthetic scenes, kiosk stopped, 40 runs): 10–20 ms per frame; p95 20.2 ms at strength 1.0 with 100 drips; about 105–168 µs per drip; temperature 50–55 °C, no throttling. **Owner's verdict on the machine:** nearly happy, feels usable. A/B check against the original pre-Phase-1 code (84 knob and frame combinations with Melt dormant): identical, so nothing outside Melt changed.

After various tries and testing, the owner has decided to leave the older versions of the melt code inside as commented out code, since it is possible that we will want to come back to this in the future. The owner will remove the unused, commented code after the whole overhaul is completed.

The existing newest vectorized melt renderer is accepted for the moment, since the tests show it does not cause latency spikes and the owner accepts the look of the effect. It is possible that a more advanced effect will be developed in the future, but for now this is fine. This marks the end of Phase 1.

**Remaining steps - After Completion of the whole Overhaul:**
1. The owner finishes tuning on the machine. The tunables are the constants at the top of the Melt code (`MELT_DRIP_WIDTH`, strides, `MAX_MELT_DRIPS`, the neon ramp).
2. No need to update the melt tests since the owner is already testing versions and recording results on the hardware (the benchmark result is only meant to be advisory anyway since the only way to really tell if it is good is to see it working on the hardware and with the kiosk and the owner has already accepted the current version).
3. Final Acceptance: owner approval on the booth screen. Further tuning is allowed after the event.

**Observations (no action required):** each drip has one gradient neon color, so the old gradient between the two colors along the drip is gone (cheap to restore if wanted). With width 8 and strides 2–4 neighboring drips overlap a lot, so pixels are drawn several times (cost, little visible change). If this is important for the performance, then it should be looked into making the overlap smaller or non-existent by more careful calculation. Drips cannot start in the outer 4 columns on either side. On solid bright areas all drips still start on one row, and lengths still change together four times a second.

### 6.2 Packet corruption (standalone helper, built in Phase 3)

Starts from today's behavior (§3.2). Changes: **Data Mosh coupling removed**; own state with `reset()`. **Content mix ✅:** squares are always **noise** (never black or white, as today); vertical bands are about **90% noise, 6% black, 4% white** (today 60/30/10). Shape mix stays as today (80% squares of 1–3 cells, 20% bands 1–2 cells wide). All numbers are tunables, to be retuned after the fusion is seen working.

### 6.3 Data Mosh (Phase 2): frame-hold + color-plane bleed ✅ (if viable)

**Goal.** Decoupled from corruption, with the two improvements the owner chose, strong visuals at high performance.

**Behavior definition**
- **B. Frame-hold.** When a cell shows motion, its previous content is **held** (frozen) for a hold time while the rest of the picture continues, and the held content is **dragged** along the cell's motion vector. Held cells release gradually (eroded edges as today). Cells that keep moving re-trigger the hold. Starting values: hold 4–12 frames (rising with strength), vectors as today (±4 px, horizontal), motion threshold as today (18 to 58 by strength).
- **D. Color-plane bleed.** In held or dragged cells, brightness comes from the held content and color from the live frame (or the reverse), so color smears away from the shape. Strength sets the mix, starting 0 to 0.8. Done in a luma/chroma color space only where cells are active.
- **Strength curve:** today's knob² until the owner wants to change it.
- **Same grid** as today (15×26) for now.

**Viability notes (to be checked, not assumed).** B is a natural extension of the current mechanism (history substitution plus vectors). D needs color-space conversion of the live and held content, which costs time; options to keep it cheap, in R2 order: convert only the region covering active cells; keep chroma at half resolution (usually hard to see); and as a last resort, ask the owner before reducing anything else. Replacing the per-cell Python loop with a single displacement-map operation is a **lossless** optimization (R2 step 1) to be done **after** the look is approved.

**Requirements**
- Independent state; `reset()` when dormant (no leftover smear), a warm-up rule so the first active frame shows no garbage, no dependency on corruption.
- Must handle duplicate camera frames (§5.4).
- **Position test (D23).** The new Data Mosh is built so its place in the order is one named setting. During Phase 2 the owner compares live: **A** before Signal Drift (today); **B** after Melt, detecting motion on the picture it displaces (Melt's own animation also triggers it, including a 4 Hz rhythm from the drip-length changes); **C** after Melt, but detecting motion from the picture **before** Melt (only the 15×26 sample points are needed, which is cheap), so it reacts to real movement and smears the melted result. Expected looks: B and C cut the vertical drips into rectangular blocks and can freeze neon tips; A keeps the drips intact on top of a datamoshed picture. Nothing is decided until the owner has seen them.
- **No recorded footage of people is used** (Q7 answered no). The owner judges and tunes the look **live on the booth**; algorithm checks use synthetic moving test patterns. The cost of old versus new Data Mosh is measured on the Pi and reported; the owner decides on any trade-off.

### 6.4 Pixelation (standalone) ✅

Unchanged from today: block size `int(12 + strength × 9)`, 12–21 px, continuous, same scratch buffer. Must stay byte-identical to today's output at equal knob values (A/B guard).

### 6.5 New Pixel Damage = fusion (Phase 3) ✅

| Raw | Packet corruption | Pixelation |
| :-- | :-- | :-- |
| 0 | off | off |
| 1–511 | strength rises from 0 to about **0.6** | none |
| 512–540 | about 0.6 (short **dead band**, no jump at the midpoint) | none |
| 541–1023 | continues rising from 0.6 to **1.0** (✅ accepted, tunable) | **5 levels** (✅), appearing one after another |

- **Pixelation levels:** evenly spread over raw 541–1023, with hysteresis about ±8 raw so a resting knob does not flicker. Starting block sizes 🔶: 12, 15, 18, 22, 26 px (today's range is 12–21 px; tune on the machine).
- Pixelation is applied **after** corruption (today's order), so corrupted blocks get pixelated too.
- Uses the **same helper functions** as the standalone corruption and pixelation effects.
- **Family rule:** the fusion, standalone corruption and standalone pixelation are never used together (§5.3). Before Phase 4 this is automatic because only the fusion is wired.

### 6.6 Posterize (Phase 6, after the event)

**All visual decisions are deferred until after the event (✅).** This section is the working definition to be refined with the owner then.

**Purpose.** Restructure the picture's tones into hard-edged neon regions with glitchy edges. It changes colors, it does not move pixels, so it stays distinct from Chroma Shift, Data Mosh, Melt, Drift and Corruption.

**Look (owner's words).**
- Blacks untouched. All other colors become neon from the Melt palette (neon yellow, hot magenta, electric cyan, acid green, deep violet, tangerine, neon rose, chartreuse).
- Every color block has a **2–3 px noisy, glitchy outline**: broken sections, jitter, flicker; not a clean vector line.
- **Glitchy, not pop art:** no flat cartoon fills, no uniform rainbow washes, no decorative shapes unrelated to the subject, no plain edge detection on the photo.

**Algorithm (starting point).**
1. Luminance map, lightly smoothed.
2. **Black preservation:** pixels below a black threshold (start 24, soft ramp to about 34) keep the original pixel exactly. Outlines never paint on black.
3. **Tonal bands:** quantize the rest into N bands with a lookup table (8 at low strength down to 3 at full).
4. **Neon assignment:** each band maps to a palette color, darker bands to the deeper hues; color is modulated by the original brightness (detail retention, 35% at low to 15% at full) so faces stay recognizable. Optional slow rotation of the assignment.
5. **Blend with the original** at low strength (neon mix 0.5 rising to 1.0 by mid strength).
6. **Outlines at band boundaries** (morphological gradient of the band map, not Canny on the photo): 1 px at low, 2 px at medium, 3 px at high; then glitchy: section dropout (10% to 35%), row jitter 1–3 px, color flicker (brighter or white-hot), sparks.
7. **Fragmentation at high strength:** fine detail breaks apart, structure stays.
8. **Stability:** if region flicker from camera noise looks accidental, add hysteresis on band assignment (needs one buffer).

**Knob response (starting curve):** off near raw 12; clearly strong by 150; medium around 400; progressively extreme to 1023 (piecewise table, tunable).
**Placement ✅:** after the pixel effects, before Chroma Shift.
**Build order:** prototype on stills and the raw clip outside the booth, iterate with the owner on contact sheets (§9.2), integrate, then optimize with the R2 ladder.

### 6.7 Unchanged effects ✅

Chroma Shift, Signal Drift and ASCII Matrix keep their behavior and formulas until Phase 4 wraps them as handlers with no visible change.

---

## 7. Telemetry v2 (Phase 5), designed around the real `telemetry.py`

**Owner decisions ✅:** the total capture count continues; effect-usage stats may restart; an effect counts as **used when its knob value is above 50, even if the effect is not visible there**; **`no_glitch` = every knob at or below 50**, so every capture counts exactly one way (no_glitch, or using at least one effect); the lifetime `no_glitch` count carries forward and is **recounted from the whole ledger with this rule** (my assumption; say so if you would rather keep the old number); the website shows a **"NO GLITCH" bar plus bars only for the currently mapped effects**. The owner wants effect popularity to be easy to see (idea for Phase 5, to be proposed: a read-only console command that prints the current popularity).

**Principles**
- **Keep what works:** the ledger file, the replay at startup, the queue and workers, `stats.json`, the ImageKit upload, the `/stats` and `/api/stats` endpoints and their existing keys (the website reads them, §3.8).
- **Effect identity is stored in each record**, not inferred from the knob index at replay time.
- **Counts:** `total_captures` and `no_glitch` are computed from **all** records (they depend only on the raw knobs, not on the mapping, so they survive every remapping). Per-effect counts, popularity percentages and combos are computed only from records of the **current epoch**.

**Ledger record (additive; old fields kept):** `ts`, `session_id`, `knobs` (unchanged) plus `v` (schema version 2), `epoch`, `mapping` (six effect IDs), `active` (effect IDs whose knob is above 50), optional `cfg` (config hash), optional `fps_tier`.

**Epoch.** A number in `effects_config.py` that is increased by hand whenever effects or mapping change in a way that makes old effect stats meaningless. The first v2 epoch starts the fresh effect stats the owner accepted. Old records (no `v`) count only toward total captures and `no_glitch`.

**Stats file.** Existing keys stay. Added keys (ignored by anything that does not know them): `schema_version`, `epoch`, `epoch_started_iso`, `no_glitch_count`. `effects_popularity` keys follow the **effect IDs mapped in the current epoch**, plus `no_glitch` (allowed by §3.8).

**Fixes to existing weaknesses (each needs a Change Proposal):** write `stats.json` atomically (temp file then replace) and under the same lock the uploader uses; skip the upload when nothing changed; note that percentages are per "use", not per capture (keep, for website compatibility, unless the owner wants otherwise); never rotate or delete the ledger (the continuing total depends on it).

**Website constraints (§3.8) that telemetry v2 must respect:** keep the file name and upload path; keep the existing top-level keys and the ISO time format; keep `effects_popularity` a flat map of name → number (0–100) with the `_usage_pct` suffix; put any new information in **new top-level keys**. With these, **no website change is needed**: new effect names appear as new bars automatically. Decided ✅: list in `effects_popularity` only the effects mapped in the current epoch plus `no_glitch`, so unmapped pool effects do not show as permanent 0% bars. Optional later website change: the hard-coded "since July 1st, 2026" note.

**Tests:** replay of a mixed legacy+v2 ledger gives the right total, `no_glitch`, and per-epoch effect counts; remapping changes effect stats but not totals; a kill during a write leaves a valid `stats.json`; `/stats` and `/api/stats` still return the old keys; the queue-full path stays non-fatal.

**Until Phase 5** telemetry is unchanged (✅ owner's call). During the event build, knob 2 is still recorded under the name `data_corruption` although it controls Data Mosh. That is expected and harmless, because those records fall into the legacy part.

---

## 8. Phased roadmap (the owner's order)

**Every phase:** the owner tags `pre-phaseN` first; Change Proposals are approved (R0); tests and A/B guard pass; visual phases pass the aesthetic gate (§9.2); the booth smoke test (§9.3) passes; the report (§9.4) is delivered; the owner writes "accepted" and tags `phaseN-accepted`. A phase that is not accepted is rolled back (§10).

### 8.0 Event schedule and go/no-go ✅

Today is **D-5**. The freeze is **D-1** (no code changes; smoke test only). The event is **D0**.

| Day | Work |
| :-- | :-- |
| D-5 | Phase 0-lite. Phase 1: test Melt on the machine, report, tuning proposals. |
| D-4 | Phase 1 accepted. Phase 2: Data Mosh design proposal approved by the owner, then implementation; the look is judged live on the booth. |
| D-3 | Phase 2 implemented and tested on the Pi; accepted or rejected. Phase 3 starts. |
| D-2 | Phase 3 implemented, tested, accepted or rejected. Soak test of the combined build. Owner merges accepted work to `main` and tags `event-ready`. |
| D-1 | **Freeze.** Smoke test of `event-ready`; nothing else. |
| D0 | Event. |

**Go/no-go rule.** Each of Melt, Data Mosh and the fusion ships only if accepted by the end of D-2. Anything not accepted does **not** ship; the booth runs the last accepted tag (worst case today's `main`). The plan is tight (three visual phases in four working days), so owner-side delays are the main risk; if time slips, **drop the last phase first (the fusion), not the testing.**

### Phase 0-lite (before Phase 1, no visible change)
1. Owner tags `baseline-pre-overhaul` on the code now running.
2. **Test inputs without people** (Q7 answered no): synthetic moving test patterns for algorithm checks and, if wanted, one raw still of a scene without people (objects or a poster) for benchmarks. Looks are judged live on the booth.
3. **Baseline benchmark** of the full pipeline with all knobs high, plus **unique camera frames per second** (§3.3).
4. **Latency diagnosis, no code change:** the five 60-second runs in §10 (count the `PERF WARNING` lines with each effect on its own). They show whether Melt is responsible for the console warnings.
**Postponed until after the event:** per-effect cost table, knob-noise probe, contact-sheet tool beyond what Phases 1–3 need.

### Phase 1: Finish Melt (see §6.1)
**Status:** implemented, accepted, end of phase... This marks the end of Phase 1. **Exit:** owner accepts on the booth screen.

### Phase 2: Decouple and improve Data Mosh (§6.3)
**Steps:** (1) design proposal for the look and its parameters; the owner approves it (optional scratch previews on synthetic motion if the owner wants them); (2) Change Proposal for the code (new Data Mosh function per R13, coupling removed, knob 2 wired to it, labels in `pygame_display.py` and `index.html` updated, "EMPTY" fixed; corruption left unwired); (3) approved changes applied; (4) tests and Pi measurements; (5) the owner judges and tunes the look live on the booth, including the Data Mosh position test A/B/C (§6.3).
**Tests:** corruption off leaves Data Mosh unchanged and vice versa (code-level independence); `reset()` leaves no residue; A/B guard against `pre-phase2` with knob 2 at 0: everything else byte-identical; duplicate-frame behavior; cost old vs new on the Pi.
**Exit:** owner accepts the look and performance on the booth.

### Phase 3: Fuse pixelation and packet corruption (§6.5)
**Steps:** Change Proposal; build the corruption helper (without Data Mosh coupling) and a pixelation helper identical to today; the fusion function; wire to knob 3; update labels.
**Tests:** helper-level: standalone pixelation byte-identical to today at equal values; no jump at the midpoint; 5 levels with hysteresis; corruption curve points (0.6 at 512, 1.0 at 1023); A/B guard against `pre-phase3` with knob 3 at 0: everything else identical; contact sheets across the knob range.
**Exit:** owner accepts. **Then the `event-ready` build** (D-2).

### Event and freeze (D-1, D0).

### Phase 4: Registry, mapping, routing, labels, governor (after the event)
`effects_config.py` (pool, families, `STAGE_ORDER`, default mapping, profiles, dormant thresholds, tunables, weights, epoch), validation and `--check`, `EFFECT_HANDLERS`, `EFFECT_TO_KNOB`, the stage loop, fail-safe fallback, handlers wrapping the unchanged effects, `/api/mapping`, labels from the config in the kiosk and the web page, per-effect governor weights, the knob-noise probe, the per-effect cost table, and the balancer improvements the owner approves (§5.6).
**Tests:** A/B guard against `event-ready` (must be **byte-identical** at the event mapping); **permutation test** (any valid remapping with correspondingly permuted knob values equals the default); validation tests (bad ID, duplicate, same family, missing handler); governor test (same effects on different knobs give the same pacing); labels agree on kiosk, web page and `/api/mapping`.
**Exit:** owner remaps by editing the config, restarts, and sees correct labels and behavior.

### Phase 5: Telemetry v2 (§7)
**Exit:** replay and recovery tests pass; a real capture shows correct effect IDs; `/stats` and the uploaded stats still work.

### Phase 6: Posterize (§6.6)
Visual decisions with the owner first (reference images recommended), prototype, integrate, optimize. Default mapping switches knob 2 to Posterize here.
**Tests:** blacks byte-identical to input where the black rule applies; outlines never on black; curve points; deterministic with seeds; combination sheets with every other effect; live check for flicker.
**Exit:** owner accepts on laptop sheets and on the booth.

### Phase 7: Release hardening
Soak test (2+ hours, mixed mapping), thermal and memory flatness, print, QR, upload, `/stats`, state recovery, rollback rehearsal; **logging proposal PX-02** (§11.3; confirmed today that output exists only on the console); update `readme.md`: move this plan to Past Development, replace it with a short "how the effects system works" section.

---

## 9. Verification, gates and templates

### 9.1 Tools
- `ab_regression.py`: old and new `server.py` side by side on the same machine, same inputs and seeds, with a self-test; must be updated per phase to say which effects may differ. It proves "unchanged", not "good". It must also be given a knob-2/knob-3 aware matrix from Phase 2 on.
- `test_melt_phase1.py`, `test_melt_benchmark.py`: Melt invariants and timings (Pi, kiosk stopped). **Status:** `test_melt_phase1.py` assumes the original 4-column, edge-neon design and fails against the owner's Melt by design (P1-01); the benchmark's pass/fail verdict is advisory (P1-02); `ab_regression.py` passes against the owner's Melt (84 of 84 dormant cases identical, 4 of 4 active cases differ).
- Later: contact-sheet tool, knob-noise probe, per-effect cost benchmark, permutation test, `effects_config.py --check`.
- Never benchmark or A/B-test while the kiosk is running.

### 9.2 Aesthetic review gate (every visual phase)
1. Contact sheets from **synthetic or people-free test scenes** (no recorded footage of people): each effect at raw 0, 100, 150, 300, 512, 700, 900, 1023, plus relevant combinations. Temporal effects (Data Mosh, corruption) are judged **live on the booth**.
2. The owner marks each **Keep / Adjust (with notes) / Reject**.
3. At most **three** laptop rounds. If still not right, the owner supplies reference images.
4. Final approval on the **booth's own screen** (colors differ from the laptop).
5. Performance work follows approval, using the R2 ladder.

### 9.3 Booth smoke test (after every phase, about 10 minutes)
1. Start via the service; live video appears.
2. Each knob responds and its label (kiosk and web page) matches.
3. Capture: countdown, flash, frozen image, receipt, QR scans.
4. Upload succeeds (or falls back to `pending_uploads.txt`); gallery link opens.
5. `/stats` loads; capture count increased.
6. Steady frame rate for 10 minutes; `vcgencmd measure_temp` normal; `vcgencmd get_throttled` shows no flags.

### 9.4 Report template (end of each phase)
Phase and tag; approved proposals and files changed; visible changes; commands run **with pasted output** and the machine used; aesthetic evidence (paths); **not verified**; assumptions; questions.

### 9.5 Change Proposal template (before any code change)
**ID** (e.g. `P2-01`); **purpose**; **files and exact scope**; **visible behavior change** (yes/no, what); **risk**; **tests that will be run**; **rollback**; **needs from the owner**. The owner replies `APPROVED P2-01`.

---

## 10. Owner's guide (when the AI assistants cannot help)

**Paths and service (from `glitchbooth.service`).** Project folder `/home/glitchbooth/glitchbooth`, virtualenv `.venv`, service `glitchbooth.service`, runs `pygame_display.py`.

**Rollback to the known-good state:**
1. `sudo systemctl stop glitchbooth.service`
2. `cd /home/glitchbooth/glitchbooth && git checkout main` (or `git checkout <tag>`, for example `event-ready`)
3. `sudo systemctl start glitchbooth.service`

**Seeing errors (confirmed on the Pi).** `journalctl -u glitchbooth.service` returns "-- No entries --" and there are no log files: program output exists only on the booth's console. To keep a record during a test, stop the service and run the program by hand with the output sent to a file:
1. `sudo systemctl stop glitchbooth.service`
2. `cd ~/glitchbooth && source .venv/bin/activate`
3. `python pygame_display.py > ~/run_A.log 2>&1` (the Escape key exits)
4. `grep -c "PERF WARNING" ~/run_A.log` counts the warnings; `grep -v "PERF WARNING" ~/run_A.log | tail -50` shows everything else.
5. `sudo systemctl start glitchbooth.service` when finished.
Never run the service and a manual copy at the same time: they would fight over the camera, serial port and printer.

**Latency experiment (no code change, about 10 minutes).** Do runs 1 to 5 as above, 60 seconds each, camera looking at the same static scene, all other knobs at 0, and write down the number of `PERF WARNING` lines:

| Run | Knobs | Warnings in 60 s |
| :-- | :-- | :-- |
| 1 | all at 0 | |
| 2 | Melt only at maximum | |
| 3 | ASCII only at maximum | |
| 4 | Chroma Shift only at maximum | |
| 5 | Data Corruption only at maximum | |

Reading the result: about as many warnings in run 1 as in run 2 means Melt is not the cause (the display loop itself is). Many more in run 2 means Melt's many small Python operations are starving the display thread. Also run `vcgencmd get_throttled` afterwards.

**!CONDUCTED!**

**Health:** `vcgencmd measure_temp`, `vcgencmd get_throttled`.

**Event freeze.** One day before the event nothing changes. After the freeze begins only smoke tests (§9.3) are allowed.

**Quick diagnosis**

| Symptom | Likely cause | First check |
| :-- | :-- | :-- |
| Booth restarts every few seconds | Program error at start (crash loop, `RestartSec=2`) | Roll back (above) |
| Knob does nothing | Wiring in the pipeline, or serial link | Does the label on the kiosk match? Console message about the serial port |
| Wrong effect on a knob after Phase 4 | Mapping or labels out of sync | `/api/mapping` vs kiosk label |
| Choppy video | Heavy effect stack, throttling, another program using the CPU | `get_throttled`, `top` |
| Looks different from laptop previews | Screen differences, different test image | Compare the same raw frame on the booth |
| Stats look wrong | Epoch change or ledger replay | Check `schema_version` and `epoch` in `stats.json` |
| Tests fail on the laptop only | Hardware libraries missing there | Run on the Pi with the kiosk stopped |

**What to give a new AI session:** this plan, `server.py`, `pygame_display.py`, `telemetry.py`, `index.html`, `glitchbooth.service`, `effects_config.py` (once it exists), the latest test outputs, and:

> "You are continuing Phase N of the GLITCHBOOTH effects overhaul. Read §2 and §3 of the plan fully, then §6 for the effects involved and the Phase N entry in §8. Do not change any code until the owner has replied APPROVED to your Change Proposal (rule R0). Before proposing anything, restate the phase goal, list the files you expect to touch, and list your questions. Do not start another phase. Finish with the report in §9.4."

**Reference images win.** For any dispute about a look, one or two reference images beat any text description.

---

## 11. Decisions

### 11.1 Answered ✅

| ID | Decision |
| :-- | :-- |
| D1 | Final default mapping: 0 ASCII, 1 Drift, 2 Posterize, 3 new Pixel Damage, 4 Melt, 5 Chroma (§1). |
| D2 | No similar effects together: one of `pixel_damage`, `packet_corruption`, `pixelation`; same for future similar effects (families, §5.3). |
| D3 | Restart-only remapping. |
| D4 | Telemetry: total captures continue, effect stats may restart; an effect is "used" above raw 50; `no_glitch` = every knob at or below 50; the lifetime `no_glitch` count carries forward (recounted with this rule). |
| D5 | Data Mosh: frame-hold + color-plane bleed, if viable. |
| D7 | Standalone pixelation unchanged; 5 levels in the fusion. |
| D8 | Fusion corruption curve: about 0.6 at raw 512 up to 1.0 at 1023; tunable. |
| D9 | Posterize after the pixel effects, before Chroma Shift; visual decisions after the event. |
| D10 | Melt neon: decide after seeing it on the machine. |
| D11 | Arduino re-flash only if very necessary; installed sketch = the readme's. |
| D12 | Event in 5 days; freeze 1 day. |
| D14 | Old fused corruption+datamosh is git history only. |
| D15 | Phase order and the "no code before approval" rule (§1, §2). |
| D16 | Captures are public on ImageKit and the website; users know. |
| D17 | Corruption content: squares never black or white; bands about 90% noise, 6% black, 4% white. |
| D18 | `index.html` and its server are a backup only; "EMPTY" for knob 0 was a mishap. |
| D19 | Knobs are labeled with replaceable stickers. |
| D20 | No recorded footage of people (raw test clip: no). Looks are judged live on the booth. |
| D21 | Website file analyzed (§3.8); no website change needed if the §7 constraints hold. |
| D22 | Interim knob wiring for the event accepted; the owner relabels the stickers by hand. |
| D24 | Website shows a NO GLITCH bar and only currently mapped effects. |
| D25 | Experiments on the AI's own computer are allowed; edits to the project through any editor or tool need approval (R0). |
| D26 | Error output is console-only today; no journal entries and no log files (confirmed). |

### 11.2 Open ⬜

**Open questions (and Pending Proposals) will stay open until phase 1 is finished unless the answer is absolutely needed.**

| ID | Question | Why it matters |
| :-- | :-- | :-- |
| D23 | Should Data Mosh sit **after Melt**? Nothing to answer now: it is decided by the live A/B/C test in Phase 2 (§6.3). | Order of effects |
| Q11 | Is the current Melt (8-px drips, 100 drips, neon ramp to full at the tip) **your accepted look**, so the tests and benchmark can be updated to it, or do you plan more changes first? | P1-01 and P1-02 depend on it | 
**for Q11, P1-01, P1-02, P1-03: Phase 1 Complete. No need to update the tests for now. Later tuning might be done after the completion of the whole Overhaul. The Proposals are kept for documentation only.**

### 11.3 Pending proposals (reply `APPROVED <id>` to start; nothing has been changed)

| ID | What | Files | Visible change | When |
| :-- | :-- | :-- | :-- | :-- |
| P1-01 | Update `test_melt_phase1.py` to the owner's Melt (8-column slices, neon ramp, edge bounds 4 to 636) | test file only | none | now |
| P1-02 | Benchmark: verdict advisory with a small tolerance; add a whole-pipeline mode so Melt is judged against the full frame time; print per-drip cost; default budget tied to the 15 FPS frame interval | `test_melt_benchmark.py` only | none | now |
| PX-01 | Display efficiency (outline): the server publishes a new-frame counter; the display copies, mirrors and scales only when it changes and otherwise re-blits the cached picture; the server takes `frame_lock` while updating the shared buffer; `PERF WARNING` becomes a summary every 10 s. Lossless (same picture). Measure before and after. | `server.py`, `pygame_display.py` | none intended | after the event |
| PX-02 | Logging (outline): either send service output to the journal (`StandardOutput=journal`; requires the warning flood to be rate-limited first) or write a rotating log file; plus a periodic performance summary | service file or `server.py` | none | after the event |
| PX-03 | Melt render speed (only if wanted): fewer, larger operations; a candidate is OpenCV's remap, which has a tiny sub-pixel rounding difference and would need your visual approval | `server.py` | possibly tiny | after the event, only if needed |

---

## 12. Glossary

- **Knob / raw value:** 0–1023 reading from the Arduino. **Strength:** what an effect derives from raw through its response curve.
- **Pool:** all effects that can be plugged into a knob. **Mapping:** which effect is on which knob. **Profile:** a named mapping.
- **Family:** a group of similar effects of which at most one may be mapped.
- **Dormant:** at or below its threshold an effect does nothing.
- **Fusion:** an effect that contains two others on one knob (Pixel Damage).
- **A/B guard:** compares old and new code on identical inputs to prove nothing else changed.
- **Contact sheet:** an image grid of one effect at many knob positions.
- **Epoch:** counter in telemetry that changes when effects or mapping change in a way that invalidates old effect stats.
- **Balancer / governor:** logic that lowers frame rate and stream quality when many heavy effects are active.
- **Change Proposal:** the written request for approval before any code change (§9.5).

---

## Appendix A: Tunables (starting values)

| Group | Name | Starting value |
| :-- | :-- | :-- |
| Melt | `MELT_CHUNK_ROWS` | 4096 (owner) | # drip-rows processed per chunk. Keeps temporaries in cache.
| Melt | `MAX_MELT_DRIPS` | 100 (owner) |
| Melt | `MELT_DRIP_WIDTH` | 8 columns (owner) |
| Melt | neon ramp | 0% neon until 75%, ramping to 100% full neon at 95% (owner) |
| Melt | `MELT_STRIDE_LOW/MID/HIGH` | 6 / 3 / 2 (owner) |
| Data Mosh | motion threshold range | 18 to 58 (today) |
| Data Mosh | `MOSH_VECTOR_PX` | 4 (today) |
| Data Mosh | `MOSH_HOLD_FRAMES` | 4 to 12, rising with strength |
| Data Mosh | `MOSH_CHROMA_BLEED` | 0 to 0.8 |
| Data Mosh | strength curve | knob² (today) |
| Corruption | `CORR_SPAWN_RATE_MAX` / `CORR_DECAY` | 0.33 / 0.90 (today) |
| Corruption | `CORR_P_BLOCK` / `CORR_P_BAND` | 0.80 / 0.20 (today) |
| Corruption | squares / bands | squares 100% noise; bands 90% noise, 6% black, 4% white |
| Pixelation | `PIX_TILE_BASE` / `PIX_TILE_SCALE` | 12 / 9 (today) |
| Fusion | `FUSION_SPLIT_RAW` / `FUSION_DEADBAND_END` | 512 / 540 |
| Fusion | `FUSION_CORR_AT_SPLIT` | 0.6 |
| Fusion | `FUSION_LEVELS` / `FUSION_TILES` / `FUSION_HYST` | 5 / (12, 15, 18, 22, 26) / ±8 raw |
| Posterize | black threshold / ramp | 24 / 34 |
| Posterize | bands low / high | 8 / 3 |
| Posterize | outline px, dropout, jitter | 1–3 px, 10–35%, 1–3 px |
| Posterize | curve | (0,0) (12,0) (150,0.40) (400,0.65) (700,0.85) (1023,1.0) |
| Telemetry | used threshold / `no_glitch` rule | used above 50; `no_glitch` when every knob is at or below 50 ✅ (10 today) |
| Balancer | FPS tiers / thresholds | 15, 12, 10 FPS at 0.55 and 0.75 (today) |

## Appendix B: Buffer inventory rule

A table of every module-level buffer (name, shape, owner effect, who may share it, lifetime), updated in each phase that adds or removes a buffer. Known sharing: `STATIC_MELT_GRAY_BUFFER` is used by Melt during the frame loop and by capture print preparation, sequentially on the same thread. Pixelation keeps `COMPRESSION_STAGE_BUFFER`. Data Mosh and Posterize need their own working buffers (decided during prototyping).

STATIC_MELT_SNAPSHOT((480, 640, 3) uint8, ~921.6 KB), is actively allocated and used by the vectorized Melt pass.

MELT_DRIP_WORK_BUFFER was commented out / retired with the adoption of the as_strided chunk renderer.

---

## Past Development

To Be Filled Out Later...