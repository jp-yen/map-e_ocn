# web-dashboard/config.py
"""
Section 1: Configuration & Environment Management (アダプターモジュール)

後方互換性のため、分割されたモジュールから必要なシンボルを再エクスポートする。
他モジュールは引き続き `from config import AppConfig, ConfigManager, APP_CONFIG` で利用可能。

  config_app.py     : AppConfig クラス (map-e.conf 読み込み・パラメータ提供)
  config_manager.py : ConfigManager クラス (設定ファイル読み書き・ステージング・反映)
"""

from config_app import AppConfig  # noqa: F401
from config_manager import ConfigManager  # noqa: F401

# ---------------------------------------------------------------------------
# シングルトン設定インスタンス (他モジュールは config.APP_CONFIG を参照する)
# ---------------------------------------------------------------------------
APP_CONFIG = AppConfig()
