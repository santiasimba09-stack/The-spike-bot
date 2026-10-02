# SpikeBot: Tournament bot for *The Spike Cross* (Steam, offline modes)

SpikeBot loops the offline **Tournament** mode of *The Spike Cross*: it navigates the menus, plays the matches, collects rewards, resets the bracket and repeats until a limit you choose is reached. It works only from screenshots (`mss` + OpenCV template matching) and sends real keyboard/mouse input. It never reads or modifies game memory.

> **Status: Phase 1, not yet tested against the live game.** The menu flow and vision were built from research on the current PC build (v8.0.109w) and tested against real screenshots and a simulated tournament. Expect one tuning session. See [Test plan for tonight](#test-plan-for-tonight).

> **Use at your own risk.** The game is free-to-play with in-app purchases and a third-party EULA. Automation may break its terms of service even in offline modes. The bot only touches offline Tournament, **never clicks the trophy screen's "Recharge" button**, and **stops immediately** if it sees a shop or purchase screen that you captured as a `danger_*` template.

---

## Features

| | |
|---|---|
| **Tournament loop** | Home → Match → Tournament → *Prepare for Match* → *Start Game* → match → rewards → result → … three wins → *Tournament Winner!* → reset → tier → repeat |
| **Plays matches** | Runs to the landing spot and bumps, runs to the spike ring and jumps and spikes, serves. Timings are tunable. You can also choose "I play, bot does the menus". |
| **Limits** | Max runs (a run is one full tournament), max matches, max minutes, stop at a clock time (`HH:MM`), max losses in a row. A time limit can let the current run finish first. |
| **Safety** | **F8** emergency stop (global), **F7** pause/resume, **F9** screenshot. Input is only sent while the game window has focus. Buttons are re-checked right before every click. If the screen is unrecognised for 120 s it saves a screenshot and stops. It also stops if a match runs longer than 25 min, and on any `danger_*` template. |
| **Human-like input** | Random delays (0.4–1.5 s), random click points inside buttons, curved and eased mouse paths, random press lengths. |
| **Config UI** | Every setting, key binding with "press a key to set", template capture and crop tool, a live view of what the bot sees, status and log. |
| **Tuning tools** | Recording of matches (frames, detections and your own game-key presses), a replay tool that writes an annotated video, and a template tester. |

---

## Install (Windows 10/11)

1. Install **Python 3.10+** from python.org and tick *"Add python.exe to PATH"*.
2. Download or clone this repository.
3. Double-click **`start_ui.bat`**. The first run creates `.venv` and installs `requirements.txt`, which takes about a minute.
   - Manual alternative: `py -m venv .venv`, `.venv\Scripts\pip install -r requirements.txt`, then `.venv\Scripts\python run_ui.py`.
4. In the game, use **windowed or borderless at 16:9**. 1920×1080 is recommended, because the bundled starter templates were cut at that size.
5. If the game runs as administrator, run the bot as administrator too. Otherwise Windows blocks the injected input.

---

## First-time setup (about 10 minutes)

### 1. Window (*Window* tab)
Start the game, click **Find game window**, then double-click it in the list. This fills in the title and process name.

### 2. Keys (*Gameplay* tab)
Open the game's **Control Settings** and copy your binds with the **Set…** buttons: Move left/right, Jump/Spike, Bump, Block, Slide, Serve. The in-match HUD also shows them next to *Jump*, *Bump*, *Block* and *Slide*.
The defaults (Z = Jump/Spike, Space = Bump, X = Block, C = Slide, arrow keys = move) come from the official screenshots, but players remap them, so check yours.

### 3. Templates (*Templates* tab)
Templates are small crops of buttons and text. The starters below are **included** (cut from v8.0.109w screenshots):
`hud_pause`, `hud_label_jump`/`spike`/`cancel`/`bump`/`block`/`feint`, `home_match`, `continue_prompt`.

Capture the rest from your game. For each one: go to that screen in the game, select the template in the list, click **Capture from game (3 s)**, switch to the game, then drag a tight box around the button. Use **Check uniqueness** in the dialog to confirm the crop matches only once on screen.

| Template (needed ones first) | Where |
|---|---|
| `mode_tournament` | *Match* menu → the **Tournament** tile. Crop the title, not "Remaining Rewards". |
| `prepare_for_match` | Tournament bracket → **Prepare for Match** (bottom-right) |
| `start_game` | Team screen → yellow **Start Game** |
| `result_win` / `result_lose` | Stats screen after a match → the **WIN!** / defeat text |
| `tournament_winner` | Trophy screen → **Tournament Winner!** text |
| `bracket_reset` | Bracket → the circular-arrows button next to Prepare for Match |
| `tier_confirm` (+ `tier_gold` etc.) | The **Tier** popup that opens after reset → Confirm (and the tier icon you want) |
| `claim_reward` | *Tournament Win Achievement Reward* → **Claim Reward** |
| `skip`, `retry`, `popup_ok`, `popup_close`, `result_next`, `winner_close` | Optional |
| `danger_shop`, `danger_purchase`, `danger_recharge`, `danger_premium_confirm` | **Recommended.** Anything that spends money or V Points. The bot stops when it sees them. |

Tips: crop **text or icons that never change**, not numbers or timers. Use mode `white` for white text over changing backgrounds. Then click **Test all on game screen** on a few screens to confirm each template shows as `visible` only where it should.

> Not at 1920×1080? Templates rescale automatically, but HUD text is small. If phases aren't detected (the live view shows `phase: unknown` during rallies), recapture the `hud_label_*` templates at your resolution.

---

## Using it

* **UI:** `start_ui.bat`. Set *Max runs* and choose *Bot plays the matches* or *I play, bot does the menus*, then press **Start**. The bot brings the game to the front. **F8 stops it at any time.**
* **CLI:** `start_bot.bat` or `python run_bot.py --max-runs 5` (`--menus-only`, `--dry-run`, `--live-view`, `--record`, `--max-minutes`).
* Logs are in `logs/spikebot.log`. Screenshots taken when stuck or on errors go to `logs/screens/`. F9 saves captures to `captures/`.

---

## Test plan for tonight

Do these in order and stop at the first step that misbehaves. A screenshot plus `logs/spikebot.log` is all I need to fix it.

1. **Dry run.** Tick *Dry run*, open the Tournament bracket in the game and press Start. The log should show which button it *would* click (`Click prepare_for_match …`). Press F8.
2. **Menus only.** Untick Dry run and choose *I play, bot does the menus*, with Max runs = 1. Play the matches yourself and watch it handle Prepare → Start → Continue → result → next round → … → reset → tier.
3. **Record your own play.** On the *Gameplay* tab tick **Record matches**, still in "I play" mode, and play one match normally. This saves frames, detections and *your* key timings to `recordings/`. That data lets me tune the bot's receive and spike timing to yours.
4. **Live view check.** Choose *Bot plays the matches*, leave *Live view* on, and start a match. In the live view check:
   - **ME** (magenta circle) follows your player
   - **LAND** (green) appears where the ball will land
   - **SPIKE** (red) appears on spike sets
   - `phase` switches between `defense` and `attack`
5. **Bot plays.** Let it play a full match and watch receives and spikes. Tune on the Gameplay tab:
   | Symptom | Setting |
   |---|---|
   | Bumps too early / late | `Bump lead (ms)` up / down |
   | Doesn't run to the ball / runs to teammates' balls | `Receive zone (px)` up / down |
   | Jumps too early / late for spikes | `Jump lead` and `Jump delay fallback` |
   | Spikes too early / late | `Spike delay fallback`, `Spike inside ring` |
   | Never serves / serves at odd times | `Serve automatically`, `Serve after idle`, `Second serve press` |
6. Send me `logs/spikebot.log`, anything in `logs/screens/`, and a recording folder. `python tools/replay_recording.py recordings/<folder>` writes an annotated `replay.mp4` and prints detection rates.

---

## How it works

```
run_ui.py / run_bot.py
  └── BotRunner (spikebot/runner.py)        main loop, limits, safety, stats
        ├── GameScreen (capture.py)          finds the window (Win32), grabs its client area (mss)
        ├── TemplateLibrary (vision.py)      OpenCV matching; templates rescaled to window size
        ├── FlowEngine (flow.py + flow.yaml) "when these templates are visible → do this" rules
        ├── InputController (actions.py)     humanised SendInput (win32.py) + focus guard
        └── GameplayAgent (gameplay/)        perception → policy → keys, 60 fps
```

**Menu flow (`flow.yaml`).** Each poll the first matching rule runs. Rules are data, so the flow can change without touching code. Run and match counts come from rule events (`match_won`, `match_lost`, `tournament_won`). A run ends on a tournament win or on elimination. After a run the `needs_reset` flag sends the bot to the reset button and the tier popup.

**Matches (`spikebot/gameplay/`).** All cues below were checked on real screenshots:
* **Phase:** the bottom HUD button reads *Jump* when defending, *Spike* when your attack is on, and *Cancel* plus *Spike* in the air. Key icons are ignored, so any binding works.
* **You:** the light-blue ▼ above your player.
* **Landing spot:** the white ring on the floor. **Spike point:** the big white ring in the air ("White-Circle Hit").
* **Net:** the blue post, used to tell which side is ours. **Ball:** its floor shadow; how fast it closes on a ring gives time-to-arrival.
* **Policy:** in defense, run under the landing ring if it's ours and bump just before arrival. In attack, run to the spike ring, jump ahead of the ball and spike when the ball reaches the ring. When idle, serve. When the ball can't be tracked, fixed delays (configurable) are used.

---

## Project layout

```
flow.yaml               tournament screen flow (rules)
templates/              template PNGs + templates.yaml (threshold, mode, search region)
spikebot/               the bot (config, capture, vision, flow, runner, gameplay/, ui/)
tools/                  test_templates.py, replay_recording.py
tests/                  pytest suite (real-screenshot fixtures + simulated tournament)
start_ui.bat / start_bot.bat
```

Development: `pip install -r requirements-dev.txt`, then `pytest`. The suite runs on any OS. Windows-only parts are imported lazily, and the input layer falls back to a dry-run backend.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| "No window whose title contains …" | Use *Find game window* on the Window tab |
| Log says "waiting for game focus" | Click the game. Input is only sent while it's focused. |
| Clicks or keys do nothing | Run the bot as administrator. Use windowed or borderless mode, not exclusive fullscreen. |
| Clicks land in the wrong place | Windows display scaling is handled (DPI-aware). Make sure the game isn't stretched or letterboxed. |
| Stuck on a screen | Capture a template for it. The screenshot is in `logs/screens/`. Add a rule to `flow.yaml` if it's a new screen. |
| Template matched on the wrong screen | Raise its threshold or recrop it smaller (*Templates → Edit threshold/mode*) |

---

## Roadmap

* **Phase 2: play quality.** Tune receive, spike and serve timing from your recordings. Ball-sprite tracking for height-aware bumps. Blocking. Aim spikes (*White-Circle Hit* every time). Skills.
* **Phase 3: more content.** Daily missions, 3-star story completion, other offline modes.

---

## Research sources

* Steam store page and official screenshots (controls, HUD, modes): <https://store.steampowered.com/app/3983810/The_Spike_Cross/>
* Steam Community screenshots of PC build v8.0.109w (HUD, key prompts, home screen): <https://steamcommunity.com/app/3983810/screenshots/>
* Tournament gameplay videos (screen flow: bracket, Prepare for Match, Start Game, WIN!, Tournament Winner!, Tier popup, Claim Reward), e.g. <https://www.youtube.com/watch?v=wgsnZmOmrPY>, <https://www.youtube.com/watch?v=AgnPGhuLfw0>, <https://www.youtube.com/watch?v=z7FUo-LcueM>
* Game overview: <https://gamerant.com/spike-cross-volleyball-steam-free-game-september-2025/>, NamuWiki (*The Spike Cross*)
* Older keyboard guide (*The Spike*): <https://gamepretty.com/the-spike-controls-for-keyboard/>
* `SendInput` scan codes for games and DPI awareness: <https://pypi.org/project/PyDirectInput/>, <https://screencoordinates.com/pyautogui-screen-coordinates/>

The test fixtures in `tests/fixtures/` are downscaled public Steam screenshots, used only for regression tests.
