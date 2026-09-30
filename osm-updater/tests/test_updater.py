from __future__ import annotations

import datetime as dt
import json
import os
import subprocess

import pytest

from conftest import NOW, make_registry, node_ids, publish_extract, write_pbf
from osm_updater import update as upd
from osm_updater.alerts import CRITICAL, WARNING, check_staleness, send_alert
from osm_updater.bootstrap import BootstrapError, bootstrap_country
from osm_updater.config import Settings
from osm_updater.cycle import run_cycle
from osm_updater.fsutil import LockBusy, exclusive_lock
from osm_updater.pbf import read_replication_info
from osm_updater.registry import RegistryError, load_registry
from osm_updater.scheduler import next_run
from osm_updater.state import State

H = dt.timedelta(hours=1)


def feed(server, first_seq=1, last_seq=4):
    """Diffs first_seq..last_seq, one per hour, ending one hour ago; diff n creates node 100+n."""
    for seq in range(first_seq, last_seq + 1):
        server.publish(seq, NOW - (last_seq - seq + 1) * H, new_node_id=None if seq == first_seq else 100 + seq)


def setup_master(server, data_dir, seq=1):
    """A master PBF as bootstrap would leave it, at sequence seq."""
    write_pbf(data_dir / "mc-latest.osm.pbf", [1, 2, 3], url=server.updates_url, seq=seq,
              ts=server.seq_times[seq])


@pytest.fixture
def country_env(server, data_dir, tmp_path, make_settings):
    registry = make_registry(tmp_path / "countries.json", server)
    settings = make_settings(registry)
    country = load_registry(registry)["MC"]
    return settings, country, State(settings.state_path)


# --- registry -------------------------------------------------------------

def test_default_registry_has_the_nine_countries():
    reg = load_registry(Settings.from_env().default_registry)
    assert set(reg) == {"DK", "GR", "RO", "FI", "BE", "ES", "IT", "CZ", "EE"}
    for c in reg.values():
        assert c.download_url.startswith("https://download.geofabrik.de/europe/")
        assert c.updates_url.endswith("-updates/")


def test_registry_rejects_paths_outside_data_dir(tmp_path):
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"countries": {"XX": {
        "name": "x", "pbf_file": "../x.osm.pbf", "download_url": "u", "updates_url": "u"}}}))
    with pytest.raises(RegistryError):
        load_registry(p)


# --- bootstrap ------------------------------------------------------------

def test_bootstrap_downloads_verifies_and_installs(server, country_env):
    settings, country, state = country_env
    feed(server)
    publish_extract(server, "MC", seq=2, ts=server.seq_times[2])

    assert bootstrap_country(country, settings, state) is True
    master = country.pbf_path(settings.data_dir)
    assert node_ids(master) == [1, 2, 3]
    assert read_replication_info(master).sequence == 2
    assert country.poly_path(settings.data_dir).exists()
    assert not list(settings.data_dir.glob(".tmp-*"))
    # second call is a no-op
    assert bootstrap_country(country, settings, state) is False


def test_bootstrap_md5_mismatch_installs_nothing(server, country_env):
    settings, country, state = country_env
    feed(server)
    publish_extract(server, "MC", seq=2, ts=server.seq_times[2], bad_md5=True)

    with pytest.raises(BootstrapError, match="md5 mismatch"):
        bootstrap_country(country, settings, state)
    assert not country.pbf_path(settings.data_dir).exists()
    assert not list(settings.data_dir.glob(".tmp-*"))
    assert "md5 mismatch" in state.country("MC")["last_error"]


def test_bootstrap_retries_transient_errors(server, country_env, monkeypatch):
    from osm_updater import bootstrap as bs
    settings, country, state = country_env
    feed(server)
    publish_extract(server, "MC", seq=2, ts=server.seq_times[2])
    monkeypatch.setattr(bs, "RETRY_WAIT_S", 0)
    real_fetch, calls = bs.fetch_md5, []

    def flaky(url, timeout):
        calls.append(url)
        if len(calls) == 1:
            raise TimeoutError("The read operation timed out")
        return real_fetch(url, timeout)

    monkeypatch.setattr(bs, "fetch_md5", flaky)
    assert bootstrap_country(country, settings, state) is True
    assert len(calls) == 2


def test_bootstrap_gives_up_after_retries(server, country_env, monkeypatch):
    from osm_updater import bootstrap as bs
    settings, country, state = country_env
    monkeypatch.setattr(bs, "RETRY_WAIT_S", 0)
    monkeypatch.setattr(bs, "fetch_md5", lambda url, t: (_ for _ in ()).throw(TimeoutError("slow")))
    with pytest.raises(BootstrapError, match="slow"):
        bootstrap_country(country, settings, state)
    assert not country.pbf_path(settings.data_dir).exists()


# --- update with the real pyosmium-up-to-date -----------------------------

def test_update_applies_all_diffs(server, country_env):
    settings, country, state = country_env
    feed(server)
    setup_master(server, settings.data_dir)

    r = upd.update_country(country, settings, state)
    assert (r.status, r.sequence_before, r.sequence_after) == (upd.UPDATED, 1, 4)
    master = country.pbf_path(settings.data_dir)
    assert node_ids(master) == [1, 2, 3, 102, 103, 104]
    assert state.country("MC")["sequence"] == 4
    assert not list(settings.data_dir.glob(".tmp-*"))

    # nothing new on the server: up to date, file untouched
    inode = master.stat().st_ino
    r = upd.update_country(country, settings, state)
    assert r.status == upd.UP_TO_DATE and master.stat().st_ino == inode


def test_open_readers_keep_the_old_version(server, country_env):
    """§3.3: a reader that opened the master before the swap keeps reading the old inode."""
    settings, country, state = country_env
    feed(server)
    setup_master(server, settings.data_dir)
    master = country.pbf_path(settings.data_dir)
    before = master.read_bytes()

    with open(master, "rb") as reader:
        assert upd.update_country(country, settings, state).status == upd.UPDATED
        assert reader.read() == before
    assert master.read_bytes() != before


def test_update_loops_while_exit_code_is_1(server, country_env):
    """Simulate the 1 GB size cap: each call applies one diff and reports more remain."""
    settings, country, state = country_env
    feed(server)
    setup_master(server, settings.data_dir)
    calls = []

    def one_diff_at_a_time(cmd):
        target = cmd[-1]
        seq = read_replication_info(target).sequence
        proc = subprocess.run(cmd[:-1] + ["--end-id", str(seq + 1), target], capture_output=True, text=True)
        calls.append(proc.returncode)
        return subprocess.CompletedProcess(cmd, 0 if seq + 1 == 4 else 1, proc.stdout, proc.stderr)

    r = upd.update_country(country, settings, state, runner=one_diff_at_a_time)
    assert (r.status, r.iterations, r.sequence_after) == (upd.UPDATED, 3, 4)
    assert node_ids(country.pbf_path(settings.data_dir)) == [1, 2, 3, 102, 103, 104]


def test_update_iteration_cap_leaves_file_consistent(server, country_env, make_settings, tmp_path):
    settings, country, state = country_env
    settings = make_settings(settings.default_registry, max_update_iterations=1)
    feed(server)
    setup_master(server, settings.data_dir)

    def capped(cmd):
        target = cmd[-1]
        proc = subprocess.run(cmd[:-1] + ["--end-id", "2", target], capture_output=True, text=True)
        return subprocess.CompletedProcess(cmd, 1, proc.stdout, proc.stderr)

    r = upd.update_country(country, settings, state, runner=capped)
    assert (r.status, r.sequence_after) == (upd.BEHIND, 2)
    assert r.ok


def test_exit_1_without_progress_is_a_failure(server, country_env):
    settings, country, state = country_env
    feed(server)
    setup_master(server, settings.data_dir)
    r = upd.update_country(country, settings, state,
                           runner=lambda cmd: subprocess.CompletedProcess(cmd, 1, "", "network error"))
    assert r.status == upd.FAILED and "no progress" in r.message


def test_pyosmium_error_leaves_master_untouched(server, country_env):
    settings, country, state = country_env
    feed(server)
    setup_master(server, settings.data_dir)
    master = country.pbf_path(settings.data_dir)
    before = master.read_bytes()

    def broken(cmd):
        tmp = cmd[cmd.index("-o") + 1]
        open(tmp, "wb").write(b"half written")
        return subprocess.CompletedProcess(cmd, 3, "", "Cannot download state information")

    r = upd.update_country(country, settings, state, runner=broken)
    assert r.status == upd.FAILED and "exit 3" in r.message
    assert master.read_bytes() == before
    assert not list(settings.data_dir.glob(".tmp-*"))


def test_server_unreachable_is_a_clean_failure(server, country_env):
    settings, country, state = country_env
    write_pbf(country.pbf_path(settings.data_dir), [1], url="http://127.0.0.1:9/updates/",
              seq=1, ts=NOW - H)
    r = upd.update_country(country, settings, state)
    assert r.status == upd.FAILED


def test_too_old_file_is_rebootstrapped(server, country_env):
    settings, country, state = country_env
    feed(server)
    publish_extract(server, "MC", seq=4, ts=server.seq_times[4], nodes=(1, 2, 3, 999))
    write_pbf(country.pbf_path(settings.data_dir), [1], url=server.updates_url, seq=1,
              ts=NOW - dt.timedelta(days=95))

    r = upd.update_country(country, settings, state)
    assert r.status == upd.REBOOTSTRAPPED
    assert node_ids(country.pbf_path(settings.data_dir)) == [1, 2, 3, 999]


def test_too_old_file_without_auto_rebootstrap_fails(server, country_env, make_settings):
    settings, country, state = country_env
    settings = make_settings(settings.default_registry, auto_rebootstrap=False)
    write_pbf(country.pbf_path(settings.data_dir), [1], url=server.updates_url, seq=1,
              ts=NOW - dt.timedelta(days=95))
    r = upd.update_country(country, settings, state)
    assert r.status == upd.FAILED and "bootstrap --force" in r.message


def test_low_disk_space_skips_update(server, country_env, monkeypatch):
    settings, country, state = country_env
    feed(server)
    setup_master(server, settings.data_dir)
    monkeypatch.setattr(upd, "has_free_space", lambda d, n: False)
    r = upd.update_country(country, settings, state)
    assert r.status == upd.FAILED and "disk space" in r.message


def test_command_passes_server_only_without_header(server, country_env):
    settings, country, _ = country_env
    t = settings.data_dir / "x.osm.pbf"
    assert "--server" not in upd.build_command(t, t, country, server.updates_url, settings)
    assert "--server" in upd.build_command(t, t, country, None, settings)


# --- staleness and alerts -------------------------------------------------

def test_staleness_levels(server, country_env, tmp_path, make_settings):
    settings, country, _ = country_env
    master = country.pbf_path(settings.data_dir)
    assert check_staleness([country], settings)[0].level == CRITICAL  # missing

    for age_days, expected in [(1, None), (10, WARNING), (70, CRITICAL)]:
        write_pbf(master, [1], url=server.updates_url, seq=1, ts=NOW - dt.timedelta(days=age_days))
        alerts = check_staleness([country], settings)
        assert (alerts[0].level if alerts else None) == expected


def test_alert_command_receives_the_alert(server, country_env, tmp_path, make_settings):
    settings, country, _ = country_env
    out = tmp_path / "alert.json"
    settings = make_settings(settings.default_registry,
                             alert_command=f'printf "%s" "$OSM_ALERT_JSON" > {out}')
    alert = check_staleness([country], settings)[0]
    send_alert(alert, settings)
    payload = json.loads(out.read_text())
    assert payload["country"] == "MC" and payload["level"] == CRITICAL


def test_broken_alert_hooks_do_not_raise(country_env, make_settings):
    settings, country, _ = country_env
    settings = make_settings(settings.default_registry, alert_command="exit 7",
                             alert_webhook_url="http://127.0.0.1:9/hook")
    send_alert(check_staleness([country], settings)[0], settings)


# --- full cycle, locking, scheduler ---------------------------------------

def test_cycle_bootstraps_then_updates(server, data_dir, tmp_path, make_settings):
    registry = make_registry(tmp_path / "countries.json", server, codes=("MC", "LI"))
    settings = make_settings(registry)
    feed(server)
    publish_extract(server, "MC", seq=2, ts=server.seq_times[2])
    publish_extract(server, "LI", seq=3, ts=server.seq_times[3])

    assert run_cycle(settings) is True
    assert settings.registry_path.exists()  # seeded on the volume
    for slug in ("mc", "li"):
        assert read_replication_info(data_dir / f"{slug}-latest.osm.pbf").sequence == 4
    st = State(settings.state_path).data
    assert st["last_cycle"]["ok"] is True

    # a later diff arrives: the next cycle only updates
    server.publish(5, NOW, new_node_id=105)
    assert run_cycle(settings) is True
    assert 105 in node_ids(data_dir / "mc-latest.osm.pbf")


def test_cycle_reports_failures_and_keeps_going(server, data_dir, tmp_path, make_settings):
    registry = make_registry(tmp_path / "countries.json", server, codes=("MC", "LI"))
    out = tmp_path / "alerts.log"
    settings = make_settings(registry, alert_command=f'echo "$OSM_ALERT_COUNTRY" >> {out}')
    feed(server)
    publish_extract(server, "MC", seq=2, ts=server.seq_times[2])
    # LI has no extract on the server: its bootstrap fails

    assert run_cycle(settings) is False
    assert read_replication_info(data_dir / "mc-latest.osm.pbf").sequence == 4
    assert out.read_text().split() == ["LI"]


def test_second_run_is_refused_while_locked(server, tmp_path, make_settings):
    settings = make_settings(make_registry(tmp_path / "countries.json", server))
    with exclusive_lock(settings.lock_path):
        with pytest.raises(LockBusy):
            run_cycle(settings)


def test_next_run():
    t = dt.datetime(2026, 9, 29, 10, 0, tzinfo=dt.timezone.utc)
    assert next_run(t, "22:30") == t.replace(hour=22, minute=30)
    assert next_run(t, "09:00") == dt.datetime(2026, 9, 30, 9, 0, tzinfo=dt.timezone.utc)
