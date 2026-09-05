import json
from pathlib import Path

from llmpdf.metrics_task import combined_usage, usage_from_pi_log


def test_usage_from_pi_log_counts_cached_and_noncached_input(tmp_path: Path) -> None:
    log = tmp_path / "pi.jsonl"
    events = [
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "usage": {
                    "input": 100,
                    "cacheRead": 40,
                    "output": 20,
                    "cost": {"total": 0.12},
                },
            },
        },
        {"type": "message_end", "message": {"role": "user", "usage": {"input": 999}}},
    ]
    log.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")
    usage = usage_from_pi_log(log)
    assert usage["noncached_input_tokens"] == 100
    assert usage["cached_input_tokens"] == 40
    assert usage["input_tokens"] == 140
    assert usage["output_tokens"] == 20
    assert usage["total_tokens"] == 160
    assert usage["pi_api_price_estimate_usd"] == 0.12


def test_combined_usage_reports_cache_ratio() -> None:
    usage = combined_usage(
        [
            {
                "noncached_input_tokens": 60,
                "cached_input_tokens": 40,
                "input_tokens": 100,
                "output_tokens": 10,
                "total_tokens": 110,
                "pi_api_price_estimate_usd": 0.1,
            }
        ]
    )
    assert usage["cache_hit_ratio"] == 0.4
