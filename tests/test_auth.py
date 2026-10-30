"""
auth.py 单元测试

覆盖：save_credential / load_cached_credential / get_credential
所有对 bilibili_api 的依赖均通过 mock 隔离。
"""

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# 在 import auth 之前，mock 掉 bilibili_api 和 rich
sys.modules.setdefault("bilibili_api", MagicMock())
sys.modules.setdefault("bilibili_api.login_v2", MagicMock())
sys.modules.setdefault("bilibili_api.user", MagicMock())
sys.modules.setdefault("bilibili_api.utils.network", MagicMock())

# 确保 src 在 path 中
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.crawl.auth import (
    get_credential,
    load_cached_credential,
    save_credential,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_credential(sessdata="test_sessdata", bili_jct="test_bili_jct",
                    dedeuserid="12345", ac_time_value="test_ac"):
    cred = MagicMock()
    cred.sessdata = sessdata
    cred.bili_jct = bili_jct
    cred.dedeuserid = dedeuserid
    cred.ac_time_value = ac_time_value
    return cred


def make_config(sessdata="", bili_jct="", buvid3="", cache_path=None):
    """Build a minimal AppConfig-like mock."""
    cfg = MagicMock()
    cfg.bilibili.sessdata = sessdata
    cfg.bilibili.bili_jct = bili_jct
    cfg.bilibili.buvid3 = buvid3
    cfg.credentials_cache = cache_path or Path("/tmp/_test_cred_cache.json")
    return cfg


# ===================================================================
# save_credential
# ===================================================================

class TestSaveCredential:
    def test_creates_json_with_expected_keys(self, tmp_path):
        cache = tmp_path / "creds" / "cache.json"
        cred = make_credential()
        save_credential(cred, "buvid3_val", cache)

        assert cache.exists()
        data = json.loads(cache.read_text(encoding="utf-8"))
        assert data["sessdata"] == "test_sessdata"
        assert data["bili_jct"] == "test_bili_jct"
        assert data["dedeuserid"] == "12345"
        assert data["buvid3"] == "buvid3_val"
        assert data["ac_time_value"] == "test_ac"
        assert "saved_at" in data

    @pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits are not enforced by Windows")
    def test_file_permission_is_600(self, tmp_path):
        cache = tmp_path / "cache.json"
        save_credential(make_credential(), "", cache)
        mode = oct(cache.stat().st_mode & 0o777)
        assert mode == "0o600"

    def test_creates_parent_dirs(self, tmp_path):
        cache = tmp_path / "a" / "b" / "c" / "cache.json"
        save_credential(make_credential(), "", cache)
        assert cache.exists()

    def test_overwrites_existing_file(self, tmp_path):
        cache = tmp_path / "cache.json"
        save_credential(make_credential(sessdata="old"), "", cache)
        save_credential(make_credential(sessdata="new"), "", cache)
        data = json.loads(cache.read_text(encoding="utf-8"))
        assert data["sessdata"] == "new"

    def test_handles_missing_optional_attrs(self, tmp_path):
        """Credential without dedeuserid/ac_time_value attributes."""
        cache = tmp_path / "cache.json"
        cred = MagicMock(spec=[])  # empty spec = no attrs
        cred.sessdata = "s"
        cred.bili_jct = "j"
        # dedeuserid and ac_time_value will use getattr default ""
        save_credential(cred, "", cache)
        data = json.loads(cache.read_text(encoding="utf-8"))
        assert data["dedeuserid"] == ""
        assert data["ac_time_value"] == ""


# ===================================================================
# load_cached_credential
# ===================================================================

class TestLoadCachedCredential:
    def test_returns_none_when_file_missing(self, tmp_path):
        result = load_cached_credential(tmp_path / "nonexistent.json")
        assert result is None

    def test_returns_none_on_invalid_json(self, tmp_path):
        cache = tmp_path / "bad.json"
        cache.write_text("not json!", encoding="utf-8")
        result = load_cached_credential(cache)
        assert result is None

    def test_returns_none_when_sessdata_empty(self, tmp_path):
        cache = tmp_path / "cache.json"
        cache.write_text(json.dumps({"sessdata": "", "bili_jct": "x"}), encoding="utf-8")
        result = load_cached_credential(cache)
        assert result is None

    def test_returns_none_when_bili_jct_missing(self, tmp_path):
        cache = tmp_path / "cache.json"
        cache.write_text(json.dumps({"sessdata": "x"}), encoding="utf-8")
        result = load_cached_credential(cache)
        assert result is None

    def test_loads_valid_cache(self, tmp_path):
        cache = tmp_path / "cache.json"
        data = {
            "sessdata": "s",
            "bili_jct": "j",
            "dedeuserid": "d",
            "buvid3": "b",
            "ac_time_value": "a",
            "saved_at": "2026-01-01T00:00:00",
        }
        cache.write_text(json.dumps(data), encoding="utf-8")

        with patch("src.crawl.auth.Credential", create=True):
            # We need to mock the Credential import inside load_cached_credential
            mock_module = MagicMock()
            mock_cred_instance = MagicMock()
            mock_module.Credential.return_value = mock_cred_instance
            with patch.dict(sys.modules, {"bilibili_api": mock_module}):
                result = load_cached_credential(cache)

        assert result is not None
        cred_obj, buvid3, saved_at = result
        assert buvid3 == "b"
        assert saved_at == "2026-01-01T00:00:00"

    def test_returns_none_when_both_fields_empty(self, tmp_path):
        cache = tmp_path / "cache.json"
        cache.write_text(json.dumps({"sessdata": "", "bili_jct": ""}), encoding="utf-8")
        assert load_cached_credential(cache) is None


# ===================================================================
# get_credential
# ===================================================================

class TestGetCredential:
    """Test the 3-tier strategy in get_credential."""

    def test_strategy1_env_config(self):
        """When .env has sessdata, return immediately without cache/login."""
        cfg = make_config(sessdata="env_sess", bili_jct="env_jct", buvid3="env_buv")

        mock_module = MagicMock()
        mock_cred_instance = MagicMock()
        mock_module.Credential.return_value = mock_cred_instance

        with patch.dict(sys.modules, {"bilibili_api": mock_module}):
            cred, buv = get_credential(cfg)

        assert cred == mock_cred_instance
        assert buv == "env_buv"

    def test_strategy2_fresh_cache_skips_validation(self, tmp_path):
        """Cache < 24h old should be used without API validation."""
        cache = tmp_path / "cache.json"
        data = {
            "sessdata": "cached_s",
            "bili_jct": "cached_j",
            "dedeuserid": "",
            "buvid3": "cached_b",
            "ac_time_value": "",
            "saved_at": datetime.now().isoformat(),  # fresh
        }
        cache.write_text(json.dumps(data), encoding="utf-8")
        cfg = make_config(cache_path=cache)

        mock_module = MagicMock()
        mock_cred_instance = MagicMock()
        mock_module.Credential.return_value = mock_cred_instance

        with patch.dict(sys.modules, {"bilibili_api": mock_module}):
            cred, buv = get_credential(cfg)

        assert buv == "cached_b"

    def test_strategy2_stale_cache_triggers_validation(self, tmp_path):
        """Cache > 24h should trigger is_credential_valid check."""
        cache = tmp_path / "cache.json"
        old_time = (datetime.now() - timedelta(hours=25)).isoformat()
        data = {
            "sessdata": "s",
            "bili_jct": "j",
            "dedeuserid": "",
            "buvid3": "b",
            "ac_time_value": "",
            "saved_at": old_time,
        }
        cache.write_text(json.dumps(data), encoding="utf-8")
        cfg = make_config(cache_path=cache)

        mock_module = MagicMock()
        mock_cred_instance = MagicMock()
        mock_module.Credential.return_value = mock_cred_instance

        with patch.dict(sys.modules, {"bilibili_api": mock_module}), \
             patch("src.crawl.auth.is_credential_valid", return_value=True), \
             patch("src.crawl.auth.save_credential"):
            cred, buv = get_credential(cfg)

        assert buv == "b"

    def test_strategy3_login_fallback(self, tmp_path):
        """No env, no cache -> trigger QR login."""
        cache = tmp_path / "no_cache.json"
        cfg = make_config(cache_path=cache)

        mock_module = MagicMock()
        mock_cred = MagicMock()
        mock_module.Credential.return_value = mock_cred

        with patch.dict(sys.modules, {"bilibili_api": mock_module}), \
             patch("src.crawl.auth.run_qrcode_login", return_value=(mock_cred, "login_buv")), \
             patch("src.crawl.auth.save_credential"):
            cred, buv = get_credential(cfg)

        assert buv == "login_buv"

    def test_noninteractive_lookup_never_starts_a_qr_browser_login(self, tmp_path):
        """Workers must return control to the Dashboard when credentials are absent."""
        cache = tmp_path / "no_cache.json"
        cfg = make_config(cache_path=cache)

        with patch("src.crawl.auth.run_qrcode_login") as login:
            with pytest.raises(RuntimeError, match="Dashboard login required"):
                get_credential(cfg, allow_interactive=False)

        login.assert_not_called()

    def test_all_strategies_fail_exits(self, tmp_path):
        """All strategies fail -> sys.exit(1)."""
        cache = tmp_path / "no_cache.json"
        cfg = make_config(cache_path=cache)

        mock_module = MagicMock()
        with patch.dict(sys.modules, {"bilibili_api": mock_module}), \
             patch("src.crawl.auth.run_qrcode_login", side_effect=RuntimeError("timeout")):
            with pytest.raises(SystemExit) as exc_info:
                get_credential(cfg)
            assert exc_info.value.code == 1
