from pathlib import Path

from cabbagecode.context import mask_old_tool_results
from cabbagecode.context.manager import MASK_TAG, PERSISTED_TAG
from cabbagecode.conversation import ConversationManager, ToolResultBlock, ToolUseBlock


def _conv(n: int, size: int) -> ConversationManager:
    conv = ConversationManager()
    conv.add_user_message("start")
    for i in range(n):
        tid = f"t{i}"
        conv.add_assistant_message("", [ToolUseBlock(tid, "ReadFile", {"file_path": f"f{i}.py"})])
        conv.add_tool_results_message([ToolResultBlock(tid, f"{i}:" + "x" * size)])
    return conv


def _results(conv):
    return [tr for m in conv.history for tr in m.tool_results]


def _saved_path(placeholder: str) -> Path:
    line = next(s for s in placeholder.splitlines() if "Full output: " in s)
    return Path(line.split("Full output: ", 1)[1].strip())


def test_masks_old_results_keeps_recent_and_saves_originals(tmp_path):
    conv = _conv(15, 5000)
    cleared = mask_old_tool_results(conv, 20_000, tmp_path, keep_recent=10, min_clear_chars=10_000)
    rs = _results(conv)
    assert cleared > 20_000
    assert all(r.content.startswith(MASK_TAG) for r in rs[:5])
    assert all(r.content.startswith(f"{i}:") for i, r in enumerate(rs[5:], start=5))
    assert _saved_path(rs[0].content).read_text(encoding="utf-8").startswith("0:")


def test_no_change_below_trigger_ratio(tmp_path):
    conv = _conv(15, 5000)
    assert mask_old_tool_results(conv, 1_000_000, tmp_path, keep_recent=10, min_clear_chars=10_000) == 0
    assert not any(r.content.startswith(MASK_TAG) for r in _results(conv))


def test_no_change_when_too_little_to_clear(tmp_path):
    conv = _conv(12, 5000)  # 只有 2 条旧结果，攒不够一批
    assert mask_old_tool_results(conv, 20_000, tmp_path, keep_recent=10) == 0


def test_can_be_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("CABBAGECODE_CONTEXT_MASKING", "0")
    conv = _conv(15, 5000)
    assert mask_old_tool_results(conv, 20_000, tmp_path, keep_recent=10, min_clear_chars=10_000) == 0


def test_skips_spilled_previews_and_lowers_usage_anchor(tmp_path):
    conv = _conv(15, 5000)
    first = _results(conv)[0]
    first.content = PERSISTED_TAG + "\n" + "p" * 5000
    conv.record_usage_anchor(input_tokens=50_000)
    mask_old_tool_results(conv, 20_000, tmp_path, keep_recent=10, min_clear_chars=10_000)
    assert first.content.startswith(PERSISTED_TAG)
    assert conv.baseline_tokens < 50_000
