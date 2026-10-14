from pathlib import Path

from zcanary.config import load

ROOT = Path(__file__).resolve().parents[1]
APPS = {
    "clean-shop",
    "transparent-fallback",
    "wrong-network",
    "log-leaker",
    "memo-dropper",
    "cross-pool-payer",
}


def test_all_six_sample_configs_have_lifecycle_log_and_report_capture():
    files = {path.stem: path for path in (ROOT / "examples" / "configs").glob("*.toml")}
    assert set(files) == APPS
    for app, path in files.items():
        cfg = load(path)
        assert cfg.network == "regtest"
        assert cfg.start_command and cfg.pay_command and cfg.stop_command
        assert cfg.log_commands and cfg.log_file
        assert cfg.result_file == "../../out/result.json"
        assert (cfg.base_dir / cfg.log_file).resolve() == ROOT / "out" / "app.log"
        assert (cfg.base_dir / cfg.report_path).resolve().parent == ROOT / "out" / "reports"
        assert cfg.pay_command == f"../../examples/{app}/pay.sh"
