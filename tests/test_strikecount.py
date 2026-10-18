import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import strikecount as sc  # noqa: E402


def write_log(tmp_path, text, name="storm.log"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_parse_time_accepts_hm_and_hms():
    assert sc.parse_time("14:02") == 14 * 3600 + 2 * 60
    assert sc.parse_time("14:02:10") == 14 * 3600 + 2 * 60 + 10


@pytest.mark.parametrize("bad", ["25:00:00", "1400", "12:60", "12:00:61", "nope"])
def test_parse_time_rejects_bad_values(bad):
    with pytest.raises(sc.LogError):
        sc.parse_time(bad)


def test_format_time_marks_next_day():
    assert sc.format_time(3600 * 9 + 5) == "09:00:05"
    assert sc.format_time(86400 + 60) == "00:01:00 (next day)"


def test_speed_of_sound_follows_temperature():
    assert sc.speed_of_sound(0) == pytest.approx(331.3)
    assert sc.speed_of_sound(20) == pytest.approx(343.42)


def test_distance_scales_with_delay_and_temperature():
    assert sc.distance_km(3, 20) == pytest.approx(3 * 343.42 / 1000)
    assert sc.distance_km(0, 20) == 0


def test_directives_set_log_settings():
    log = sc.parse_log("temp 18\nunits mi\nlocation Hill Farm\n")
    assert log.errors == []
    assert log.temp_c == 18
    assert log.units == "mi"
    assert log.location == "Hill Farm"


def test_comments_and_blank_lines_are_skipped():
    log = sc.parse_log("# header\n\n10:00:00 flash\n# note\n10:00:02 thunder\n")
    assert log.errors == []
    assert [e.kind for e in log.events] == ["flash", "thunder"]


def test_errors_are_line_numbered():
    text = "# c\n14:00 flash\n14:00:05 zap\n13:00:00 thunder\ntemp hot\nunits furlongs\n"
    log = sc.parse_log(text)
    assert len(log.errors) == 4
    assert log.errors[0].startswith("line 3: ")
    assert log.errors[1].startswith("line 4: ")
    assert "backwards" in log.errors[1]
    assert log.errors[2].startswith("line 5: ")
    assert log.errors[3].startswith("line 6: ")


def test_temperature_out_of_range_is_an_error():
    log = sc.parse_log("temp 99\n")
    assert len(log.errors) == 1
    assert log.errors[0].startswith("line 1: ")


def test_thunder_pairs_with_most_recent_flash():
    log = sc.parse_log("10:00:00 flash\n10:00:02 flash\n10:00:05 thunder\n")
    strikes, unpaired_flashes, unpaired_thunders = sc.pair(log)
    assert len(strikes) == 1
    assert strikes[0].flash.t == 10 * 3600 + 2
    assert strikes[0].delay_s == 3
    assert len(unpaired_flashes) == 1
    assert unpaired_thunders == []


def test_thunder_without_flash_is_unpaired():
    log = sc.parse_log("10:00:00 thunder\n")
    strikes, _, unpaired_thunders = sc.pair(log)
    assert strikes == []
    assert len(unpaired_thunders) == 1


def test_flash_older_than_pair_window_expires():
    log = sc.parse_log("10:00:00 flash\n10:11:00 thunder\n")
    strikes, unpaired_flashes, unpaired_thunders = sc.pair(log)
    assert strikes == []
    assert len(unpaired_flashes) == 1
    assert len(unpaired_thunders) == 1


def test_thirty_thirty_rule_flags_close_strikes():
    close = sc.pair(sc.parse_log("10:00:00 flash\n10:00:30 thunder\n"))[0][0]
    far = sc.pair(sc.parse_log("10:00:00 flash\n10:00:31 thunder\n"))[0][0]
    assert close.close is True
    assert far.close is False


def test_approach_speed_is_positive_when_closing():
    text = "10:00:00 flash\n10:00:30 thunder\n10:01:00 flash\n10:01:20 thunder\n"
    strikes = sc.pair(sc.parse_log(text))[0]
    expected = 10 * 0.34342 / 50 * 3600
    assert sc.approach_kmh(strikes) == pytest.approx(expected, rel=1e-6)


def test_approach_speed_needs_two_strikes():
    strikes = sc.pair(sc.parse_log("10:00:00 flash\n10:00:03 thunder\n"))[0]
    assert sc.approach_kmh(strikes) is None


def test_peak_rate_counts_flashes_in_window():
    times = [36000, 36120, 36240, 36540, 37200]
    assert sc.peak_rate(times) == (4, 36000)


def test_check_waits_then_clears():
    log = sc.parse_log("10:00:00 thunder\n")
    waiting = sc.check_at(log, 10 * 3600 + 600)
    assert waiting["safe"] is False
    assert waiting["wait_s"] == 1200
    assert waiting["all_clear"] == "10:30:00"
    assert sc.check_at(log, 10 * 3600 + 1800)["safe"] is True


def test_check_ignores_thunder_after_check_time():
    log = sc.parse_log("11:00:00 thunder\n")
    status = sc.check_at(log, 10 * 3600)
    assert status["safe"] is True
    assert status["last_thunder"] is None


def test_summary_numbers_for_one_strike():
    log = sc.parse_log("temp 20\n10:00:00 flash\n10:00:03 thunder\n")
    info = sc.summarise(log)
    assert info["strikes"] == 1
    assert info["closest_km"] == pytest.approx(3 * 343.42 / 1000)
    assert info["mean_km"] == info["closest_km"]
    assert info["close_strikes"] == 1
    assert info["approach_kmh"] is None
    assert info["last_thunder"] == "10:00:03"
    assert info["all_clear"] == "10:30:03"


def test_cli_lint_exit_codes(tmp_path, capsys):
    good = write_log(tmp_path, "10:00:00 flash\n10:00:03 thunder\n")
    bad = write_log(tmp_path, "10:00:00 flash\n9:99 thunder\n", name="bad.log")
    assert sc.main(["lint", good]) == 0
    assert sc.main(["lint", bad]) == 1
    assert "line 2:" in capsys.readouterr().out


def test_cli_check_exit_codes(tmp_path, capsys):
    path = write_log(tmp_path, "10:00:00 thunder\n")
    assert sc.main(["check", path, "--at", "10:10"]) == 1
    assert "WAIT" in capsys.readouterr().out
    assert sc.main(["check", path, "--at", "10:45"]) == 0
    assert "SAFE" in capsys.readouterr().out


def test_cli_summary_json(tmp_path, capsys):
    path = write_log(tmp_path, "temp 20\n10:00:00 flash\n10:00:03 thunder\n")
    assert sc.main(["summary", path, "--json"]) == 0
    info = json.loads(capsys.readouterr().out)
    assert info["strikes"] == 1
    assert info["all_clear"] == "10:30:03"


def test_cli_strikes_table_marks_shelter(tmp_path, capsys):
    path = write_log(tmp_path, "10:00:00 flash\n10:00:03 thunder\n")
    assert sc.main(["strikes", path]) == 0
    assert "30-30 SHELTER" in capsys.readouterr().out


def test_cli_distance_in_miles(capsys):
    assert sc.main(["distance", "3", "--temp", "20", "--units", "mi"]) == 0
    out = capsys.readouterr().out
    assert "0.64 mi" in out
    assert "30-30 rule" in out


def test_cli_parse_errors_exit_two(tmp_path, capsys):
    path = write_log(tmp_path, "10:00:00 zap\n")
    assert sc.main(["summary", path]) == 2
    assert "line 1:" in capsys.readouterr().err


def test_cli_missing_file_exits_two(tmp_path, capsys):
    assert sc.main(["summary", str(tmp_path / "missing.log")]) == 2
    assert "missing.log" in capsys.readouterr().err
