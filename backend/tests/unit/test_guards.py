from correction.guards import apply_guards


def proposal(text, original, replacement, *, confidence=0.95, kind="letterform"):
    start = text.index(original)
    return {
        "span_start": start,
        "span_end": start + len(original),
        "original": original,
        "replacement": replacement,
        "confidence": confidence,
        "kind": kind,
    }


def test_rejects_digit_invented_by_model():
    text = "Total: SAR 12,500"
    change = proposal(text, "12,500", "13,500")

    accepted, rejected, report = apply_guards(text, [change])

    assert accepted == []
    assert rejected[0]["rule"] == "digit_invented"
    assert report["rejections_by_rule"] == {"digit_invented": 1}


def test_allows_digit_seen_by_another_engine():
    text = "Total: SAR 12,5OO"
    change = proposal(text, "12,5OO", "12,500")

    accepted, rejected, _ = apply_guards(
        text, [change], other_engine_texts=["Total: SAR 12,500"]
    )

    assert [item["replacement"] for item in accepted] == ["12,500"]
    assert rejected == []


def test_rejects_change_over_consensus_locked_span():
    text = "شركة النور للتجارة المحدودة"
    change = proposal(text, "النور", "النورر")
    start, end = change["span_start"], change["span_end"]

    accepted, rejected, _ = apply_guards(text, [change], locked=[[start, end]])

    assert accepted == []
    assert rejected[0]["rule"] == "consensus_locked"


def test_rejects_misaligned_span():
    text = "annual lease"
    change = {
        "span_start": 0,
        "span_end": 6,
        "original": "lease!",
        "replacement": "lease",
    }

    accepted, rejected, _ = apply_guards(text, [change])

    assert accepted == []
    assert rejected[0]["rule"] == "span_mismatch"


def test_overlapping_changes_keep_higher_confidence():
    text = "abcdef"
    lower = proposal(text, "abc", "abb", confidence=0.8)
    higher = proposal(text, "bcd", "bcc", confidence=0.95)

    accepted, rejected, _ = apply_guards(text, [lower, higher])

    assert accepted == [higher]
    assert rejected[0]["rule"] == "overlap"
