# Strikecount ⚡

**An offline thunderstorm log: flash and thunder times in, strike distances, storm speed and a shelter-or-wait answer out.**

Strikecount reads a plain-text file of the flashes you saw and the thunder you heard. It pairs each thunder with its flash, converts the delay into a distance with a temperature-corrected speed of sound, and tells you when it is safe to go back outside. It uses only the Python standard library, makes no network calls and needs no account.

## Features

- **Flash-to-bang distance**: converts each delay to kilometres or miles using the speed of sound at your air temperature (`331.3 + 0.606 × °C` m/s).
- **Automatic pairing**: each thunder is matched to the most recent unpaired flash, and flashes with no thunder within 10 minutes are reported.
- **30-30 shelter rule**: strikes with a delay of 30 s or less are flagged `30-30 SHELTER`.
- **All-clear timer**: 30 minutes after the last thunder heard, with a `check --at` command that exits 1 while you should still wait.
- **Storm approach speed**: a least-squares fit of distance against time gives how fast the storm is closing in (or moving away).
- **Peak strike rate**: the most flashes in any 10-minute window, with its start time.
- **Strike table**: every paired strike with its line number, flash and thunder times, delay, distance and shelter flag.
- **Summary report**: closest, farthest and mean distances, counts of paired and unpaired events, in text or JSON.
- **Line-numbered lint**: reports bad times, unknown events, out-of-order times, bad directives and bad temperatures with `line N:` messages.
- **Warnings for loose ends**: flashes with no thunder and thunder with no flash are listed separately from errors.
- **Per-file settings**: `temp`, `units` (km or mi) and `location` directives go at the top of the log.
- **Quick distance calculator**: `strikecount distance 7` works without a log file, with `--temp` and `--units`.
- **Scripting-friendly**: exit codes 0, 1, 2 for OK, still waiting or parse error, and stdin input with `-`.

## Quick start

Save this as `storm.log`:

```text
# afternoon storm over the hill farm
location Hill Farm
temp 18
units km

14:02:10 flash   cloud-to-ground, north ridge
14:02:12 thunder
14:09:03 flash
14:09:04 thunder   sharp crack
14:24:15 flash
14:24:33 thunder
14:34:05 flash
14:34:05 thunder
```

Then run:

```sh
python3 strikecount.py summary storm.log
python3 strikecount.py strikes storm.log
python3 strikecount.py check storm.log --at 14:50
```

The sample file in `sample/afternoon-storm.log` is a longer version of this example. Try `python3 strikecount.py lint sample/afternoon-storm.log` to see the warnings for a flash with no thunder and a thunder with no flash.

## Log format

One event per line, in time order:

| Line | Meaning |
| --- | --- |
| `HH:MM[:SS] flash [note]` | a lightning flash was seen |
| `HH:MM[:SS] thunder [note]` | thunder was heard |
| `temp <°C>` | air temperature, default 20 |
| `units km` or `units mi` | output units, default km |
| `location <name>` | label shown in the summary |
| `# text` | comment, ignored |

## Commands

| Command | What it does |
| --- | --- |
| `summary FILE [--json]` | storm totals, distances, approach speed, peak rate and all-clear |
| `strikes FILE` | table of paired strikes with 30-30 flags |
| `lint FILE` | errors (exit 1) and warnings for the log |
| `check FILE --at HH:MM[:SS] [--json]` | SAFE (exit 0) or WAIT (exit 1) at that time |
| `distance DELAY [--temp C] [--units km\|mi]` | distance for one flash-to-thunder delay |

Use `-` in place of `FILE` to read the log from stdin.

## Running the tests

The tests use `pytest`, which is the only dependency outside the standard library.

```sh
pip install pytest
python3 -m pytest -q tests
```

The tests cover time parsing, the speed of sound, line-numbered parse errors, pairing and expiry, the 30-30 flag, approach speed, peak rate, the all-clear check and the command-line exit codes.

## Notes

- Distances are estimates. Sound speed changes with temperature, and the 30-30 rule is a safety guideline, not a guarantee.
- Pairing uses the most recent flash. When two flashes come before one thunder, the earlier flash stays pending.
- Shelter advice follows the common 30-30 guidance: seek shelter when thunder follows a flash within 30 seconds, and wait 30 minutes after the last thunder.
