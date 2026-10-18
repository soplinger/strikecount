#!/usr/bin/env python3
"""Strikecount: offline thunderstorm log analyser (standard library only).

Reads a plain-text storm log of flash and thunder times. Each thunder is paired
with the flash it followed, the delay is turned into a distance with the
temperature-corrected speed of sound, and the log reports storm approach speed,
peak strike rate, 30-30 shelter flags and the 30-minute all-clear time.
"""
import argparse
import json
import re
import sys
from bisect import bisect_left
from dataclasses import dataclass, field

PAIR_WINDOW_S = 600      # a thunder pairs with a flash at most this many seconds old
CLUSTER_WINDOW_S = 600   # window used to find the peak strike rate
CLOSE_DELAY_S = 30       # 30-30 rule: thunder within 30 s of the flash means seek shelter
ALL_CLEAR_S = 30 * 60    # wait 30 minutes after the last thunder before going out
KM_PER_MI = 1.609344
TEMP_RANGE_C = (-60.0, 60.0)

TIME_RE = re.compile(r"^(\d{1,2}):([0-5]\d)(?::([0-5]\d))?$")


class LogError(ValueError):
    """A problem with one line of the storm log."""


def parse_time(text):
    """Return seconds since midnight for 'HH:MM' or 'HH:MM:SS'."""
    match = TIME_RE.match(text)
    if not match:
        raise LogError(f"bad time {text!r}, expected HH:MM or HH:MM:SS")
    hours = int(match.group(1))
    if hours > 23:
        raise LogError(f"bad time {text!r}, hour must be 00-23")
    return hours * 3600 + int(match.group(2)) * 60 + int(match.group(3) or 0)


def format_time(seconds):
    """Format seconds since midnight as HH:MM:SS, marking times past midnight."""
    day_offset, rest = divmod(int(seconds), 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    text = f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return text + " (next day)" if day_offset else text


def speed_of_sound(temp_c):
    """Speed of sound in air in m/s at the given temperature in degrees C."""
    return 331.3 + 0.606 * temp_c


def distance_km(delay_s, temp_c):
    """Distance in km to a strike whose thunder arrived delay_s seconds after the flash."""
    return delay_s * speed_of_sound(temp_c) / 1000.0


def km_to_units(km, units):
    return km / KM_PER_MI if units == "mi" else km


def fmt_distance(km, units):
    return f"{km_to_units(km, units):.2f} {units}"


@dataclass
class Event:
    line: int
    t: int
    kind: str
    note: str = ""


@dataclass
class Log:
    temp_c: float = 20.0
    units: str = "km"
    location: str = ""
    events: list = field(default_factory=list)
    errors: list = field(default_factory=list)


@dataclass
class Strike:
    flash: Event
    thunder: Event
    delay_s: int
    km: float

    @property
    def close(self):
        return self.delay_s <= CLOSE_DELAY_S


def parse_log(text):
    """Parse storm-log text. Bad lines are recorded in log.errors, not raised."""
    log = Log()
    last_t = None
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        head = line.split(None, 1)[0].lower()
        try:
            if head in ("temp", "units", "location"):
                value = line[len(head):].strip()
                if head == "temp":
                    try:
                        log.temp_c = float(value)
                    except ValueError:
                        raise LogError(f"bad temperature {value!r}") from None
                    if not TEMP_RANGE_C[0] <= log.temp_c <= TEMP_RANGE_C[1]:
                        raise LogError("temperature must be between -60 and 60 C")
                elif head == "units":
                    if value not in ("km", "mi"):
                        raise LogError("units must be km or mi")
                    log.units = value
                else:
                    log.location = value
                continue

            fields = line.split(None, 2)
            if len(fields) < 2:
                raise LogError("expected 'HH:MM:SS flash|thunder [note]'")
            t = parse_time(fields[0])
            kind = fields[1].lower()
            if kind not in ("flash", "thunder"):
                raise LogError(f"unknown event {fields[1]!r}, expected flash or thunder")
            if last_t is not None and t < last_t:
                raise LogError("time goes backwards, events must be in order")
            last_t = t
            note = fields[2] if len(fields) > 2 else ""
            log.events.append(Event(lineno, t, kind, note))
        except LogError as exc:
            log.errors.append(f"line {lineno}: {exc}")
    return log


def pair(log):
    """Pair thunders with the nearest earlier unpaired flash.

    Returns (strikes, unpaired_flashes, unpaired_thunders).
    """
    strikes, pending, unpaired_flashes, unpaired_thunders = [], [], [], []
    for ev in log.events:
        still_pending = []
        for flash in pending:
            if ev.t - flash.t > PAIR_WINDOW_S:
                unpaired_flashes.append(flash)
            else:
                still_pending.append(flash)
        pending = still_pending
        if ev.kind == "flash":
            pending.append(ev)
        elif pending:
            flash = pending.pop()
            delay = ev.t - flash.t
            strikes.append(Strike(flash, ev, delay, distance_km(delay, log.temp_c)))
        else:
            unpaired_thunders.append(ev)
    unpaired_flashes.extend(pending)
    return strikes, unpaired_flashes, unpaired_thunders


def approach_kmh(strikes):
    """Least-squares storm speed in km/h; positive means the storm is closing in.

    Returns None when fewer than two strikes at different times are available.
    """
    points = [(s.thunder.t, s.km) for s in strikes]
    n = len(points)
    if n < 2:
        return None
    mean_t = sum(t for t, _ in points) / n
    mean_d = sum(d for _, d in points) / n
    sxx = sum((t - mean_t) ** 2 for t, _ in points)
    if sxx == 0:
        return None
    sxy = sum((t - mean_t) * (d - mean_d) for t, d in points)
    return -(sxy / sxx) * 3600.0


def peak_rate(flash_times):
    """Most flashes inside any CLUSTER_WINDOW_S window, with the window start."""
    times = sorted(flash_times)
    best_count, best_start = 0, None
    for i, start in enumerate(times):
        count = bisect_left(times, start + CLUSTER_WINDOW_S) - i
        if count > best_count:
            best_count, best_start = count, start
    return best_count, best_start


def summarise(log):
    strikes, unpaired_flashes, unpaired_thunders = pair(log)
    flashes = [e for e in log.events if e.kind == "flash"]
    thunders = [e for e in log.events if e.kind == "thunder"]
    kms = [s.km for s in strikes]
    count, start = peak_rate([e.t for e in flashes])
    speed = approach_kmh(strikes)
    last_thunder = thunders[-1].t if thunders else None
    return {
        "location": log.location,
        "units": log.units,
        "temp_c": log.temp_c,
        "flashes": len(flashes),
        "thunders": len(thunders),
        "strikes": len(strikes),
        "unpaired_flashes": len(unpaired_flashes),
        "unpaired_thunders": len(unpaired_thunders),
        "closest_km": min(kms) if kms else None,
        "farthest_km": max(kms) if kms else None,
        "mean_km": sum(kms) / len(kms) if kms else None,
        "close_strikes": sum(1 for s in strikes if s.close),
        "approach_kmh": speed,
        "peak_rate": {"count": count, "window_start": format_time(start) if start is not None else None},
        "last_thunder": format_time(last_thunder) if last_thunder is not None else None,
        "all_clear": format_time(last_thunder + ALL_CLEAR_S) if last_thunder is not None else None,
    }


def check_at(log, at):
    """Shelter status at time `at` (seconds since midnight), using only events up to it."""
    heard = [e.t for e in log.events if e.kind == "thunder" and e.t <= at]
    if not heard:
        return {"safe": True, "reason": "no thunder heard yet", "last_thunder": None,
                "all_clear": None, "wait_s": 0}
    last = max(heard)
    all_clear = last + ALL_CLEAR_S
    wait = max(0, all_clear - at)
    return {"safe": wait == 0, "reason": "all clear" if wait == 0 else "wait for the all-clear",
            "last_thunder": format_time(last), "all_clear": format_time(all_clear), "wait_s": wait}


def load_text(path):
    if path == "-":
        return sys.stdin.read()
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def render_summary(info):
    units = info["units"]
    lines = []
    if info["location"]:
        lines.append(f"Location: {info['location']}")
    lines.append(f"Temperature {info['temp_c']:g} C, sound {speed_of_sound(info['temp_c']):.1f} m/s")
    lines.append(f"Flashes {info['flashes']}, thunders {info['thunders']}, paired strikes {info['strikes']}")
    lines.append(f"Unpaired flashes {info['unpaired_flashes']}, unpaired thunders {info['unpaired_thunders']}")
    if info["strikes"]:
        lines.append(f"Closest {fmt_distance(info['closest_km'], units)}, "
                     f"farthest {fmt_distance(info['farthest_km'], units)}, "
                     f"mean {fmt_distance(info['mean_km'], units)}")
    lines.append(f"Strikes inside 30-30 range: {info['close_strikes']}")
    if info["approach_kmh"] is None:
        lines.append("Approach speed: not enough strikes")
    else:
        kmh = info["approach_kmh"]
        direction = "closing" if kmh > 0 else "moving away"
        lines.append(f"Approach speed: {km_to_units(abs(kmh), units):.1f} {units}/h ({direction})")
    peak = info["peak_rate"]
    if peak["count"]:
        lines.append(f"Peak rate: {peak['count']} flashes in {CLUSTER_WINDOW_S // 60} min "
                     f"from {peak['window_start']}")
    if info["last_thunder"]:
        lines.append(f"Last thunder {info['last_thunder']}, all clear {info['all_clear']}")
    return "\n".join(lines)


def cmd_summary(args):
    log = parse_log(load_text(args.file))
    if log.errors:
        return report_errors(log)
    info = summarise(log)
    print(json.dumps(info, indent=2) if args.json else render_summary(info))
    return 0


def cmd_strikes(args):
    log = parse_log(load_text(args.file))
    if log.errors:
        return report_errors(log)
    strikes, _, _ = pair(log)
    if not strikes:
        print("No paired strikes.")
        return 0
    units = log.units
    print(f"{'line':>5}  {'flash':<8}  {'thunder':<8}  {'delay':>5}  {'distance':>10}  flag")
    for s in strikes:
        flag = "30-30 SHELTER" if s.close else ""
        print(f"{s.flash.line:>5}  {format_time(s.flash.t):<8}  {format_time(s.thunder.t):<8}  "
              f"{s.delay_s:>4}s  {fmt_distance(s.km, units):>10}  {flag}".rstrip())
    return 0


def cmd_lint(args):
    log = parse_log(load_text(args.file))
    strikes, unpaired_flashes, unpaired_thunders = pair(log)
    for error in log.errors:
        print(f"{args.file}:{error}")
    for flash in unpaired_flashes:
        print(f"{args.file}:line {flash.line}: warning: flash has no thunder within "
              f"{PAIR_WINDOW_S // 60} min")
    for thunder in unpaired_thunders:
        print(f"{args.file}:line {thunder.line}: warning: thunder has no earlier flash")
    if log.errors:
        print(f"{len(log.errors)} error(s)")
        return 1
    print(f"OK: {len(log.events)} events, {len(strikes)} paired strikes")
    return 0


def cmd_check(args):
    log = parse_log(load_text(args.file))
    if log.errors:
        return report_errors(log)
    at = parse_time(args.at)
    status = check_at(log, at)
    if args.json:
        print(json.dumps(status, indent=2))
    elif status["safe"]:
        if status["last_thunder"] is None:
            print(f"SAFE at {format_time(at)}: {status['reason']}")
        else:
            print(f"SAFE at {format_time(at)}: last thunder {status['last_thunder']}, "
                  f"all clear {status['all_clear']}")
    else:
        mins = -(-status["wait_s"] // 60)
        print(f"WAIT at {format_time(at)}: last thunder {status['last_thunder']}, "
              f"{mins} min left, all clear {status['all_clear']}")
    return 0 if status["safe"] else 1


def cmd_distance(args):
    km = distance_km(args.delay, args.temp)
    print(f"{args.delay:g} s at {args.temp:g} C ({speed_of_sound(args.temp):.1f} m/s): "
          f"{fmt_distance(km, args.units)}")
    if args.delay <= CLOSE_DELAY_S:
        print("30-30 rule: within range, seek shelter")
    return 0


def report_errors(log):
    for error in log.errors:
        print(error, file=sys.stderr)
    print(f"{len(log.errors)} error(s); run 'strikecount lint' for details", file=sys.stderr)
    return 2


def build_parser():
    parser = argparse.ArgumentParser(prog="strikecount",
                                     description="Offline thunderstorm log analyser.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("summary", help="storm totals, distances, approach speed and peak rate")
    p.add_argument("file", help="storm log, or - for stdin")
    p.add_argument("--json", action="store_true", help="print JSON")
    p.set_defaults(func=cmd_summary)

    p = sub.add_parser("strikes", help="table of every paired strike")
    p.add_argument("file")
    p.set_defaults(func=cmd_strikes)

    p = sub.add_parser("lint", help="check the log for errors and warnings")
    p.add_argument("file")
    p.set_defaults(func=cmd_lint)

    p = sub.add_parser("check", help="shelter status at a given time (exit 1 = still waiting)")
    p.add_argument("file")
    p.add_argument("--at", required=True, metavar="HH:MM[:SS]", help="time to check")
    p.add_argument("--json", action="store_true", help="print JSON")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("distance", help="distance for a flash-to-thunder delay, no file needed")
    p.add_argument("delay", type=float, help="seconds between flash and thunder")
    p.add_argument("--temp", type=float, default=20.0, help="air temperature in C (default 20)")
    p.add_argument("--units", choices=("km", "mi"), default="km")
    p.set_defaults(func=cmd_distance)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (OSError, LogError) as exc:
        print(f"strikecount: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
