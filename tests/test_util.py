"""Tests for the shared defensive numeric extractor (webull_api._util.num).

Locks the contract that replaced ~7 near-identical private `_num` copies: first parseable float
among the keys, skipping None / empty-string / unparseable, tolerant of a None dict."""
from __future__ import annotations

from webull_api._util import num


def test_num_first_parseable_wins():
    assert num({"a": "1.5", "b": "2"}, ["a", "b"]) == 1.5


def test_num_skips_none_and_empty_string():
    assert num({"a": None, "b": "", "c": "3"}, ["a", "b", "c"]) == 3.0


def test_num_skips_unparseable():
    assert num({"a": "x", "b": "4"}, ["a", "b"]) == 4.0


def test_num_returns_default_when_nothing_matches():
    assert num({"a": "x"}, ["a", "z"]) is None
    assert num({}, ["a"], default=0.0) == 0.0


def test_num_tolerates_none_dict():
    assert num(None, ["a"]) is None
