import os

os.environ.setdefault("OPENAI_API_KEY", "test-key")

from routes.chat import DocumentEvidenceRange, EvidenceWindow, _filter_candidates_by_evidence_ranges


def _candidate(document_id, start=None, end=None):
    metadata = {"file_type": "video", "chunk_type": "video_segment"}
    if start is not None:
        metadata.update({"start_seconds": start, "end_seconds": end})
    return {"document_id": document_id, "chunk_metadata": metadata}


def test_evidence_ranges_keep_only_overlapping_media_chunks():
    ranges = [DocumentEvidenceRange(
        document_id="video-1",
        ranges=[EvidenceWindow(start_seconds=60, end_seconds=120)],
    )]
    candidates = [
        _candidate("video-1", 0, 59),
        _candidate("video-1", 90, 150),
        _candidate("document-2"),
    ]

    filtered = _filter_candidates_by_evidence_ranges(candidates, ranges)

    assert filtered == [candidates[1], candidates[2]]


def test_unbounded_query_keeps_all_candidates():
    candidates = [_candidate("video-1", 0, 30)]
    assert _filter_candidates_by_evidence_ranges(candidates, []) == candidates
