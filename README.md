# controller_validation_task

A lightweight PsychoPy task for validating an fMRI/MEG/EEG-compatible game controller.

A controller diagram is shown on screen. A cue tells the participant whether the next press
should be **short** (orange dot) or **long** (green bar), then one button lights up. The
participant presses — and, for long trials, holds and releases — the lit button. Press onset,
release, reaction time and press duration are logged per trial.

This is a standalone port of the `gamepad` task from
[`task_stimuli`](https://github.com/courtois-neuromod/task_stimuli), rebuilt in the style of
`mario_task`: one config file, a first-run wizard, pluggable triggers, and a pure-Python core
that is testable without a display.

---

## Quick start

```bash
git clone <this repo> && cd controller_validation_task
bash setup_env.sh          # system libs + uv + .venv + smoke test
bash run.sh                # first launch opens the config wizard, then a subject picker
```

`setup_env.sh` tries the local `uv` cache before hitting the network, so if
another PsychoPy project on the machine has already been installed it finishes
in seconds with no download. Force that with `OFFLINE=1 bash setup_env.sh`.

Or skip the pickers:

```bash
bash run.sh 01 001         # subject 01, session 001
```

Session defaults to the next unused number for that subject, so `bash run.sh 01` is usually
enough after the first visit.

## What a session looks like

Two runs by default. Each run is 10 blocks × 8 buttons = **80 trials**, roughly 5 minutes.
Every block is entirely short-press or entirely long-press, and cues all 8 buttons in a random
order. Instructions are shown before every run; the two explanation screens (what the bar and
the dot mean) appear on run 1 only.

## Controller wiring

The task reads the pad **as a keyboard**. The controller must be mapped to the keystrokes
`u d l r a b x y` before the task starts — the [AntiMicroX](https://github.com/AntiMicroX/antimicrox)
profile that does this ships in `controller_validation_task/assets/controller_config.gamecontroller.amgp`:

```bash
antimicrox --profile controller_validation_task/assets/controller_config.gamecontroller.amgp
```

Verify with any text editor that pressing A types `a`, the D-pad types `u/d/l/r`, and so on,
before you put anyone in the scanner.

### Which keys the controller sends

The task reads the pad **as a keyboard**, and `input.key_map` in `config.json`
translates the key names PsychoPy reports into the button names used by
`design.keys` and the layout. The default accepts two families at once, so most
controllers work with no configuration:

| Key the pad sends | Button |
| --- | --- |
| `left` `right` `up` `down` | `l` `r` `u` `d` |
| `l` `r` `u` `d` (AntiMicroX profile) | `l` `r` `u` `d` |
| `a` `b` `x` `y` | `a` `b` `x` `y` |

If your controller sends something else, list it:

```json
"input": { "key_map": { "kp_4": "l", "kp_6": "r", "1": "a", "2": "b" } }
```

Several keys may map to the same button. A key that is not in the map is still
recorded in `all_keypresses` — it just never counts as the cued response.
Startup fails loudly if a button in `design.keys` is unreachable from the map,
because such trials could never be answered.

To find out what your pad actually sends, run any text editor and press its
buttons — or check the log, which records every release as `Keyrelease: <key>`.

### Display

`display.fullscreen`, `display.screen_index` and `display.window_size` in
`config.json`, each overridable per run:

```bash
bash run.sh 01 001 --list-screens             # what monitors are attached
bash run.sh 01 001 --fullscreen               # native resolution (the default)
bash run.sh 01 001 --screen 1                 # second monitor
bash run.sh 01 001 --no-fullscreen --window-size 1280x720   # piloting
```

Fullscreen uses the chosen screen's **native resolution**, so the controller
image is not rescaled. With `screen_index: null` the task picks the *last*
attached screen — on a scanner rig that is the projector rather than the
operator's console.

`--instruction-duration` adjusts how long each instruction screen stays up
(default 3 s).

## Configuration

Everything lives in `config.json` (gitignored, written by the wizard). Re-open the wizard with
`bash run.sh --reconfigure`. Values are layered, **later wins**:

```
defaults  <  config.json  <  environment / .env  <  CLI flags
```

The wizard splits the settings across five tabs — **Session**, **Design**,
**Display**, **Scanner sync**, **Markers**. Field labels are just the
`config.json` key, so the dialog and the file read alike; the `ⓘ` at the end
of each row explains what the setting does on hover, so setting up a rig does
not mean reading this file in another window.

The **Design** tab holds the trial structure — `n_blocks_per_condition`, the
press durations, the ISI jitter and the initial/final waits — and a line under
the tabs shows the **estimated duration** of one run and of the whole session,
in seconds and minutes, updating as you type:

```
Estimated duration — Per run: 80 trials in 10 blocks, ~307 s (5 min 7 s).
Session: 2 runs, ~627 s (10 min 27 s) incl. instructions, excl. any scanner wait.
```

It is the expected value over the random ISI and long-press draws (a real run
lands within a few seconds of it), counts the instruction screens, and cannot
know how long the scanner takes to send its trigger. Ranges are typed as
`low, high`; leaving `isi_range` / `block_isi` blank keeps them derived from
the TR.

### Scanner start signal — `sync`

| `mode` | Behaviour |
| --- | --- |
| `none` | Start immediately. The default. |
| `wait` | Block until the sync signal arrives, then start. Upstream `task_stimuli` behaviour. |
| `send` | Emit `signal` once at run start. Use when the stimulus computer starts the scanner. |

`backend` says *over what*, and **defaults to `none`** — no hardware. In `wait` mode the
signal is then expected from the keyboard; most MR trigger boxes present as a USB keyboard,
so this covers the scanner as well as the desk. In `send` mode there is nothing to send to,
so the run starts with a warning.

`sync.signal` is the signal itself, and means the same thing both ways: in `send` mode it is
what goes out (`"s"` → the byte 115), in `wait` mode it is what we listen for. Default `"s"`.
List alternatives when one physical key reports under several names — `["5", "percent"]` is
the same trigger-box key with and without shift. Only the first entry is ever sent.

Otherwise, for `send`: `serial`, `parallel`, `lsl`, `key`, or `markers` (re-use the
already-open marker port, so one serial device carries both). For `wait`: `keyboard` or
`serial`.

**No port, no problem.** If `sync.port` is unset — or the port refuses to open — the session
warns and degrades to the `none` behaviour for that mode: `wait` listens on the keyboard,
`send` starts the run unsynchronised. The same `config.json` therefore works at the scanner
and on a desk. The waiting screen shows only `Waiting for the scanner`; which keys are being
watched is printed to the console and the session log, for the operator.

The current fMRI setup — send `s` to a serial port:

```bash
bash run.sh 01 001 --sync-mode send --sync-backend serial --sync-port /dev/ttyUSB0
```

### Event markers — `triggers`

Independent of `sync`. Backends: `lsl` (default for MEG/EEG rigs), `serial`, `parallel`, `null`.
Markers fire at task start/stop and at each trial's onset and offset; per-keypress markers are
available via `triggers.on_keypress`.

Check the chain before a participant arrives:

```bash
uv run python -m controller_validation_task.monitor    # prints decoded markers live
```

A trigger backend that fails to open **never aborts the session** — it logs a warning and
downgrades to dropping markers.

### Trial design — `design`

`keys`, `conditions`, `n_runs`, `n_blocks_per_condition`, `short_press_duration`,
`long_duration_range`, `initial_wait`, `final_wait`, `response_window`, `lr_mode`, `seed`, and
the jitter controls `tr` / `isi_range` / `block_isi`.

`isi_range` and `block_isi` are `null` by default, meaning they derive from the TR as
`[TR, 2×TR]` and `2×TR`. Set them to absolute seconds for non-fMRI use.

A run's expected length is available without generating a design —
`design.estimate_duration(settings.design)` — and is what the wizard displays.

Designs are deterministic: the seed comes from `sha1(subject_session_run)`, so re-running a
session re-uses the same sequence. They are written to
`output/sourcedata/sub-<S>/sub-<S>_ses-<SS>_run-<NN>_design.tsv` and re-used if present — change
`design.*` mid-study and existing subjects keep their original sequence until you delete the file.

### Left/right hand mode

`--lr` (or `design.lr_mode`) dims half the controller and cues only the lit hand's four buttons,
adding an `lr_condition` column to the output.

### Custom controllers

The button hit-boxes live in `controller_validation_task/assets/layout.json`, not in the code.
To use a different pad, put your PNG beside a copy of that file, update `image`, `image_size` and
the `buttons` shapes (coordinates are **image pixels, origin top-left** — what you read off the
image in an editor), then point `paths.layout_file` at it.

## Output

```
output/sourcedata/sub-01/
├── sub-01_ses-001_run-01_design.tsv
└── ses-001/
    ├── sub-01_ses-001_20260101-120000.log
    ├── sub-01_ses-001_20260101-120000_task-gamepad_run-01_events.tsv
    └── sub-01_ses-001_20260101-120000_task-gamepad_run-02_events.tsv
```

The events TSV columns match the original task exactly:

| column | meaning |
| --- | --- |
| `TrialNumber` | 1-indexed trial counter |
| `block` | 0-indexed block |
| `condition` | `short` or `long` |
| `key` | the cued button |
| `lr_condition` | lit hand (`--lr` only) |
| `duration` | how long the button stayed lit |
| `onset` | scheduled onset, seconds from run start |
| `onset_flip` | *measured* flip time of the highlight |
| `offset_flip` | *measured* flip time of the un-highlight |
| `key_press_time` | first press of the cued button |
| `key_press_rt` | `key_press_time - onset_flip` |
| `key_release_time` | first release of the cued button |
| `key_release_rt` | `key_release_time - offset_flip` |
| `key_duration` | how long they actually held it |
| `all_keypresses` | every press in the trial window, as `[(key, time), ...]` |
| `all_keyreleases` | every release, same format |

`all_keypresses` / `all_keyreleases` exist so trials confounded by a stray press of another
button can be excluded offline; parse them with `ast.literal_eval`. Missing responses are empty
cells, not `NaN`.

## Operator shortcuts

| Keys | Effect |
| --- | --- |
| `Ctrl+C` | abort the current run, move to the next |
| `Ctrl+N` | restart the current run |
| `Ctrl+Q` | quit the session |

They are live in every phase, including the `Waiting for the scanner` screen and
mid-trial. Quitting is a clean exit, not a kill: the run's events file is
written, the log is flushed, and the window, sync port and marker backend are
closed in order. The process exits `130`.

`c` / `n` / `q` need Ctrl because a bare letter is more likely to be a stray
keystroke than a decision — and during a run the participant's unmodified
keystrokes belong to the task and never reach PsychoPy at all. `Ctrl+Q` is the
only clean exit; `Escape` does nothing.

`Ctrl+Q` is additionally registered as a PsychoPy global key, so it is caught
the moment it arrives rather than only on the frames the shortcut poller runs
on — including during the pauses between instruction screens. It still needs
the experiment window to have keyboard focus; it is not an OS-level hotkey.

## What you see

Three screens precede run 1: the general instruction, then one screen each
explaining the long-press bar and the short-press dot. Later runs show only the
general instruction.

During a trial the cue sits at the centre of the controller and is visible for
the whole trial — **an orange dot means short press, a green bar means long
press**. The cued button is tinted salmon for exactly as long as you should
hold it. Move the cue elsewhere by editing `cues.*.pos` in the layout file.

## Development

```bash
just test        # pure-Python suite, no display
just lint        # ruff
just lock        # re-resolve uv.lock
```

The display-free modules (`settings`, `design`, `layout`, `events`, `paths`, `markers`, `sync`)
are unit-tested and run in CI. The display-bound ones (`task`, `session`, `gui`) are verified
manually on hardware.

## License

MIT — see [LICENSE](LICENSE).
