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
  URL: https://glitchbooth.online/p/{timestamp}

- EVENT MODE: Set EVENT_SLUG = "your-event-slug" (e.g., "synthistan2026")
  Target: /booth_captures/your-event-slug
  URL: https://glitchbooth.online/your-event-slug/p/{timestamp}

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

---

## Rules for AI assistants (non-negotiable)

**R0. No code change without the owner's approval.**

Procedure:

1. The assistant posts a **Change Proposal** : code or files, exact scope, visible behavior change, risks, tests, rollback.
2. The owner answers `APPROVED <proposal id>` if present or approves the changes (or edits the proposal). **Silence is not approval.**
3. Only then and if allowed specifically are files changed, and only the files and scope in the approved proposal. Anything new found along the way becomes a new proposal.
4. **Allowed without approval (✅ owner):** reading, analysis, reports, and experiments the AI runs **on its own computer** (for example scripts that render preview images or measure something), clearly labeled and never deployed. **Not allowed without approval:** any change to any file in the project through **any editor or tool** (VS Code, Antigravity, Codex, nano, micro, shell commands, scripts, and so on).

**R1. Ask, do not assume.** If intent or the desired look is unclear, or a fact cannot be verified from the files given, stop and ask. List any unavoidable assumption in the report.

**R2. Aesthetics first.** Do not reduce visual richness (detail, randomness, neon intensity, effect range, drip counts, color count etc.) to gain speed without written owner approval. **Optimization ladder:** (1) remove waste that does not change the picture (allocations, redundant copies, Python loops that can be vectorized); (2) cheaper but equivalent operations (lookup tables, in-place ops, integer math); (3) lower internal resolution, only with approval after viewing the result; (4) reduce caps or quality, only with approval.

**R3. No silent behavior change.** Change only what the approved proposal lists. A visible change outside scope means stop and report.

**R4. Evidence over claims.** "Done", "verified", "implemented" need pasted command output. Never write them into `readme.md` without it. State what was **not** run (especially anything that needs the Raspberry Pi).

**R5. One source of truth.** No copies such as `_v2`, `_1`, `_final`. Edit the real file; git is the history. One test file per purpose.

**R6. Version control belongs to the owner.** The AI never runs `git push` and never creates commits or tags. It writes the exact commands; the owner runs them.

**R7. Tunables live in one block.** Every number that shapes a look is a named, commented constant with its allowed range, in one tunables block (in `server.py` for the moment). No magic numbers inside effect code.

**R8. Hot-path discipline.** Inside the frame loop: if possible, no allocation of frame-sized arrays, C-contiguous `uint8` buffers, preallocated scratch. Never overrides R2.

**R9. Determinism for tests.** Seed `np.random`, `random` **and** `cv2.setRNGSeed` (OpenCV's `cv2.randu` has its own generator). Synchronize import-time random state (for example `RANDOM_SIGN_POOL`) when two copies of the module are compared.

**R10. Thread and hardware rules.** All pixel work runs in the `background_loop` thread. Effects never block and never do I/O. Telemetry writes are asynchronous. Printing, upload, countdown, audio, QR and the capture state machine are not touched by the overhaul.

**R11. Shared buffers.** A scratch buffer may be shared only if all users run sequentially on the same thread, none relies on its contents across stages or frames, and every user is listed in the buffer inventory.

**R12. Phase protocol.** One phase or batch at a time. The owner tags `pre-phaseN` or `pre-batchN` first. After finishing: tests, aesthetic gate (visual phases), report. Do not start the next phase until the owner writes "accepted". Every phase should be planned as a stand alone update. The glitch booth operation should continue without flaws after the completion of each phase or batch.

**R13. Forward compatibility (pre-registry phases).** Until a registry exists, new effects code is still written as **self-contained functions with the handler contract** (own state, `reset()`, tunables at the top, no calls to other effects), called from the existing pipeline at the existing stage position.

**R14. Fail safe.** An invalid configuration must never leave the booth dead. The service restarts on failure every 2 seconds (`Restart=on-failure`), so a crash loop is the worst outcome; see §5.3.

**R15. Missing information.** If a needed file was not given, ask. Do not reconstruct it from memory.

**R16. No new dependencies** (pip or system packages) without approval.

---

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

These files were used for hardware checks before setup and can be used for troubleshooting. Later, custom test files used during development will be added here for documentation and referencing.

This folder is not critical for the app to run.

---

## Current Development

Improvig server.py code security and housekeeping, Fixing logging and telemetry across the whole pipeline.

---

## Past Development

To Be Filled Out Later...
