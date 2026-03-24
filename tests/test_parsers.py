import sys
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main


def test_parse_prefers_clash_yaml():
    yaml = """proxies:\n  - name: a\n    type: ss\n    server: 1.1.1.1\n    port: 8388\n    cipher: aes-128-gcm\n    password: p"""
    links = main._parse_subscription_content(yaml)
    assert links and links[0].startswith("ss://")
