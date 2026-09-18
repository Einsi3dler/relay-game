"""The mail diagnostic: DNS wire format, and the judgements it makes.

`query` is stubbed throughout. A test that reaches real DNS fails on a plane,
in CI without egress, and on the day somebody else's zone changes — none of
which say anything about this code. The wire-format helpers are tested against
bytes, which is the part that either parses or does not.
"""

from __future__ import annotations

import struct

import pytest

from backend import mailcheck


@pytest.fixture
def answers(monkeypatch):
    """Point `query` at a fixed {(name, type): [records]} map."""
    table: dict[tuple[str, int], list[str]] = {}

    def fake_query(name, record_type=mailcheck.TYPE_TXT, timeout=4.0):
        return table.get((name, record_type), [])

    monkeypatch.setattr(mailcheck, "query", fake_query)
    return table


# --- wire format ----------------------------------------------------------

def test_encode_name():
    assert mailcheck._encode_name("example.com") == b"\x07example\x03com\x00"
    # A trailing dot is the same name, and callers write it both ways.
    assert mailcheck._encode_name("example.com.") == b"\x07example\x03com\x00"


def test_skip_name_walks_labels():
    data = b"\x07example\x03com\x00REST"
    assert mailcheck._skip_name(data, 0) == len(b"\x07example\x03com\x00")


def test_skip_name_stops_on_a_compression_pointer():
    """A length byte with the top two bits set is a pointer, two bytes wide,
    and ends the name. Walking it as a label length would desynchronise the
    whole parse."""
    data = b"\xc0\x0cREST"
    assert mailcheck._skip_name(data, 0) == 2


def test_truncated_udp_answer_is_retried_over_tcp(monkeypatch):
    """The github.com case: a UDP reply with the TC bit set carries no records
    at all, so without the TCP retry a present record reads as missing."""
    truncated = struct.pack(">HHHHHH", 1, 0x8200, 1, 0, 0, 0)   # TC set, 0 answers
    calls = []

    def fake_udp(request, resolver, timeout):
        calls.append("udp")
        return truncated

    def fake_tcp(request, resolver, timeout):
        calls.append("tcp")
        # header + question + one TXT answer of "v=spf1 -all"
        text = b"v=spf1 -all"
        packet = struct.pack(">HHHHHH", 1, 0x8000, 1, 1, 0, 0)
        packet += mailcheck._encode_name("example.com") + struct.pack(">HH", 16, 1)
        packet += b"\xc0\x0c" + struct.pack(">HHIH", 16, 1, 300, len(text) + 1)
        packet += bytes([len(text)]) + text
        return packet

    monkeypatch.setattr(mailcheck, "_ask_udp", fake_udp)
    monkeypatch.setattr(mailcheck, "_ask_tcp", fake_tcp)
    assert mailcheck.query("example.com") == ["v=spf1 -all"]
    assert calls == ["udp", "tcp"]


def test_a_dead_resolver_moves_on(monkeypatch):
    monkeypatch.setattr(mailcheck, "_ask_udp", lambda *a: None)
    monkeypatch.setattr(mailcheck, "_ask_tcp", lambda *a: None)
    assert mailcheck.query("example.com") == []


# --- SPF ------------------------------------------------------------------

def test_spf_present(answers, capsys):
    answers[("relay.example.com", 16)] = ["v=spf1 include:_spf.resend.com ~all"]
    answers[("resend._domainkey.relay.example.com", 16)] = ["v=DKIM1; p=MIGf0real"]
    answers[("_dmarc.relay.example.com", 16)] = ["v=DMARC1; p=none;"]
    assert mailcheck.check_dns("relay.example.com", None) == 0


def test_spf_missing_is_a_failure(answers):
    assert mailcheck.check_dns("relay.example.com", None) > 0


def test_two_spf_records_are_a_failure(answers, capsys):
    """A domain may publish exactly one. Two is treated by receivers as none,
    which is a uniquely annoying way to fail."""
    answers[("relay.example.com", 16)] = ["v=spf1 include:a ~all", "v=spf1 include:b ~all"]
    mailcheck.check_dns("relay.example.com", None)
    assert "exactly one" in capsys.readouterr().out


def test_unrelated_txt_records_are_not_mistaken_for_spf(answers, capsys):
    answers[("relay.example.com", 16)] = ["google-site-verification=abc123"]
    mailcheck.check_dns("relay.example.com", None)
    assert "no SPF record" in capsys.readouterr().out


# --- DKIM -----------------------------------------------------------------

def test_dkim_found_under_a_known_selector(answers, capsys):
    answers[("postmark._domainkey.relay.example.com", 16)] = ["v=DKIM1; k=rsa; p=MIGfMA0real"]
    assert mailcheck._check_dkim("relay.example.com", None) == 0
    assert "postmark._domainkey" in capsys.readouterr().out


def test_dkim_with_an_explicit_selector(answers):
    answers[("custom._domainkey.relay.example.com", 16)] = ["v=DKIM1; p=MIGfreal"]
    assert mailcheck._check_dkim("relay.example.com", "custom") == 0


def test_revoked_dkim_key_is_not_a_pass(answers, capsys):
    """The example.com case: `v=DKIM1; p=` is a record whose key has been
    revoked. Mail signed against it still fails, so a record existing is not
    the question worth asking."""
    answers[("resend._domainkey.relay.example.com", 16)] = ["v=DKIM1; p="]
    assert mailcheck._check_dkim("relay.example.com", None) == 1
    assert "revoked" in capsys.readouterr().out


def test_dkim_cname_not_yet_resolving_is_a_warning_not_a_failure(answers, capsys):
    """Providers publish DKIM as a CNAME. Between adding it and propagation
    there is a window where it exists but resolves to nothing, and that is
    normal rather than broken."""
    answers[("resend._domainkey.relay.example.com", mailcheck.TYPE_CNAME)] = ["<cname>"]
    assert mailcheck._check_dkim("relay.example.com", None) == 0
    assert "does not resolve to a key yet" in capsys.readouterr().out


def test_dkim_missing_entirely(answers, capsys):
    assert mailcheck._check_dkim("relay.example.com", None) == 1
    assert "no DKIM record found" in capsys.readouterr().out


# --- DMARC ----------------------------------------------------------------

def test_dmarc_is_a_warning_not_a_failure(answers, capsys):
    """Missing DMARC hurts deliverability but does not stop mail, so it must
    not fail a check whose job is to say 'this will not work'."""
    answers[("relay.example.com", 16)] = ["v=spf1 -all"]
    answers[("resend._domainkey.relay.example.com", 16)] = ["v=DKIM1; p=MIGreal"]
    assert mailcheck.check_dns("relay.example.com", None) == 0
    assert "no DMARC record" in capsys.readouterr().out


def test_everything_present(answers, capsys):
    answers[("relay.example.com", 16)] = ["v=spf1 include:_spf.resend.com ~all"]
    answers[("resend._domainkey.relay.example.com", 16)] = ["v=DKIM1; k=rsa; p=MIGfMA0GCS"]
    answers[("_dmarc.relay.example.com", 16)] = ["v=DMARC1; p=none; rua=mailto:x@y.com"]
    assert mailcheck.check_dns("relay.example.com", None) == 0
    out = capsys.readouterr().out
    assert out.count("PASS") == 3


# --- SPF at the envelope domain -------------------------------------------

def test_spf_found_on_the_envelope_subdomain(answers, capsys):
    """Regression: SPF is evaluated against the envelope sender (Return-Path),
    not the From header. Resend, Postmark and SES-backed senders bounce through
    send.<domain> and publish SPF there, so checking only the From domain
    reports a correct setup as broken. This is a real case, caught against a
    live domain."""
    answers[("send.relay.example.com", 16)] = ["v=spf1 ip4:52.3.252.119 ~all"]
    assert mailcheck._check_spf("relay.example.com") == 0
    out = capsys.readouterr().out
    assert "PASS" in out
    # It has to say WHERE, or the next person goes looking at the wrong name.
    assert "send.relay.example.com" in out


def test_spf_at_the_from_domain_is_not_labelled_as_an_envelope(answers, capsys):
    answers[("relay.example.com", 16)] = ["v=spf1 include:_spf.example.com ~all"]
    assert mailcheck._check_spf("relay.example.com") == 0
    assert "envelope domain" not in capsys.readouterr().out


def test_the_from_domain_wins_over_an_envelope_subdomain(answers, capsys):
    """Both present: report the one receivers check first for a same-domain
    envelope, rather than whichever the loop happened to reach."""
    answers[("relay.example.com", 16)] = ["v=spf1 include:primary.example.com ~all"]
    answers[("send.relay.example.com", 16)] = ["v=spf1 ip4:1.2.3.4 ~all"]
    mailcheck._check_spf("relay.example.com")
    assert "primary.example.com" in capsys.readouterr().out


@pytest.mark.parametrize("prefix", ["send", "mail", "bounce", "em"])
def test_common_envelope_prefixes_are_searched(answers, prefix):
    answers[(f"{prefix}.relay.example.com", 16)] = ["v=spf1 ip4:1.2.3.4 ~all"]
    assert mailcheck._check_spf("relay.example.com") == 0


def test_spf_missing_everywhere_names_both_places(answers, capsys):
    assert mailcheck._check_spf("relay.example.com") == 1
    assert "envelope subdomains" in capsys.readouterr().out
