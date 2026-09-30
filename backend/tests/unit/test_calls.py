import csv
import io
import os
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from phonesystem.db import Database
from phonesystem.models import CallRecord, Extension, Phone
from phonesystem.services import calls

T0 = datetime(2026, 9, 29, 15, 0, tzinfo=UTC).timestamp()  # 11:00 in New York


def cdr_line(n: int = 0, **over) -> str:
    """A cdr_custom line in our v1 format, like Asterisk writes it."""
    start = over.pop("start", T0 + n * 60)
    fields = {
        "uniqueid": f"{int(start)}.{n}",
        "linkedid": f"{int(start)}.{n}",
        "sequence": str(n),
        "start": f"{start:.6f}",
        "answer": f"{start + 2:.6f}",
        "end": f"{start + 32:.6f}",
        "duration": "32",
        "billsec": "30",
        "disposition": "ANSWERED",
        "src": "101",
        "clid": '"Alice" <101>',
        "dst": "102",
        "channel": f"PJSIP/101-{n:08x}",
        "dstchannel": f"PJSIP/102-{n + 1:08x}",
        "lastapp": "Dial",
        "ps_dialed": "102",
        "ps_route": "extension",
    }
    fields.update(over)
    buf = io.StringIO()
    csv.writer(buf, quoting=csv.QUOTE_ALL).writerow(
        [fields[k] for k in calls.V1_FIELDS]
    )  # Asterisk's CSV_QUOTE quotes every field
    return "v1," + buf.getvalue().replace("\r\n", "\n")


def record(line: str) -> CallRecord:
    fields = calls.parse_line(line.strip())
    assert fields is not None
    rec = calls.to_record(fields)
    assert rec is not None
    return rec


# ------------------------------------------------------------ labelling


def test_internal_answered_call():
    r = record(cdr_line())
    assert (r.direction, r.status) == ("internal", "answered")
    assert (r.from_extension, r.to_extension) == ("101", "102")
    assert (r.src_number, r.src_name, r.dst_number) == ("101", "Alice", "102")
    assert r.talk_seconds == 30
    assert r.started_at == datetime.fromtimestamp(T0, UTC)


def test_extension_that_is_offline():
    r = record(
        cdr_line(disposition="FAILED", dstchannel="", answer="0.000000", billsec="0", dst="103",
                 ps_dialed="103")
    )  # fmt: skip
    assert (r.direction, r.status, r.to_extension) == ("internal", "failed", "103")
    assert r.answered_at is None


@pytest.mark.parametrize(
    ("disposition", "status"),
    [("NO ANSWER", "missed"), ("BUSY", "busy"), ("CONGESTION", "failed"), ("FAILED", "failed")],
)
def test_statuses(disposition, status):
    r = record(cdr_line(disposition=disposition, billsec="0"))
    assert r.status == status
    assert r.talk_seconds == 0


def test_number_that_is_not_ours_is_a_failed_external_call():
    # The dialplan answers to play "not in service", so Asterisk says ANSWERED.
    r = record(
        cdr_line(dst="not-in-service", dstchannel="", lastapp="Playback",
                 ps_dialed="5551234", ps_route="invalid")
    )  # fmt: skip
    assert (r.direction, r.status, r.dst_number) == ("outbound", "failed", "5551234")
    assert r.to_extension is None
    assert r.talk_seconds == 0


def test_feature_code_is_internal():
    r = record(cdr_line(dst="*43", dstchannel="", lastapp="Echo", ps_dialed="*43",
                        ps_route="feature"))  # fmt: skip
    assert (r.direction, r.status, r.to_extension) == ("internal", "answered", None)


def test_incoming_call_from_a_trunk():
    r = record(
        cdr_line(src="+15551234567", clid='"ACME Corp" <+15551234567>',
                 channel="PJSIP/trunk-voipms-0000000a", dstchannel="PJSIP/101-0000000b",
                 ps_dialed="101")
    )  # fmt: skip
    assert r.direction == "inbound"
    assert (r.from_extension, r.to_extension, r.trunk) == (None, "101", "trunk-voipms")
    assert r.src_name == "ACME Corp"


def test_outgoing_call_through_a_trunk():
    r = record(
        cdr_line(dst="15551234567", dstchannel="PJSIP/trunk-voipms-0000000c",
                 ps_dialed="15551234567", ps_route="trunk")
    )  # fmt: skip
    assert r.direction == "outbound"
    assert (r.from_extension, r.to_extension, r.trunk) == ("101", None, "trunk-voipms")


def test_names_with_commas_and_quotes():
    r = record(cdr_line(clid='"Smith, \\"Bob\\"" <102>'))
    assert r.src_name == 'Smith, "Bob"'


@pytest.mark.parametrize(
    "line",
    [
        "",
        "not,a,record",
        "v2," + cdr_line()[3:],  # a future format
        cdr_line().rsplit(",", 1)[0],  # missing a column
    ],
)
def test_unreadable_lines_are_rejected(line):
    assert calls.parse_line(line.strip()) is None


def test_endpoint_of():
    assert calls.endpoint_of("PJSIP/101-0000002a") == "101"
    assert calls.endpoint_of("PJSIP/trunk-voipms-0000002a") == "trunk-voipms"
    assert calls.endpoint_of("Local/101@ctx-0001;1") is None
    assert calls.endpoint_of("") is None


# ------------------------------------------------------------ importing


@pytest.fixture
def cdr_file(tmp_path):
    return tmp_path / "phonesystem-calls.csv"


def count(db: Database) -> int:
    with db.session() as s:
        return s.scalar(select(func.count(CallRecord.id)))


def test_import_is_incremental_and_skips_duplicates(db: Database, cdr_file):
    importer = calls.CdrImporter(db, cdr_file)
    assert importer.import_new() == 0  # no file yet

    cdr_file.write_text(cdr_line(0) + cdr_line(1))
    assert importer.import_new() == 2
    assert importer.import_new() == 0

    with cdr_file.open("a") as f:
        f.write(cdr_line(2))
        f.write(cdr_line(3).rstrip("\n")[:40])  # Asterisk is mid-write
    assert importer.import_new() == 1
    with cdr_file.open("a") as f:
        f.write(cdr_line(3)[40:])
    assert importer.import_new() == 1
    assert count(db) == 4


def test_import_starts_over_on_a_new_file(db: Database, cdr_file):
    importer = calls.CdrImporter(db, cdr_file)
    cdr_file.write_text(cdr_line(0) + cdr_line(1))
    importer.import_new()

    # Rotated: the new file has the old lines plus a new one, and a new inode.
    new = cdr_file.with_name("new.csv")
    new.write_text(cdr_line(0) + cdr_line(1) + cdr_line(2))
    os.replace(new, cdr_file)
    assert importer.import_new() == 1
    # Truncated.
    cdr_file.write_text(cdr_line(5))
    assert importer.import_new() == 1
    assert count(db) == 4


def test_import_skips_garbage_lines(db: Database, cdr_file):
    cdr_file.write_text("garbage\n" + cdr_line(0) + "v1,too,short\n")
    assert calls.CdrImporter(db, cdr_file).import_new() == 1


# ------------------------------------------------------------ API


@pytest.fixture
def call_log(admin: TestClient, db: Database, cfg, cdr_file):
    """Admin client with a small call history loaded."""
    cfg.cdr_file = cdr_file  # the app reads it through the shared config object
    admin.app.state.phonesystem.cdr_importer.path = cdr_file
    with db.session() as s:
        s.add_all(
            [
                Extension(number="101", name="Alice", sip_password="abcdefgh12345"),
                Extension(number="102", name="Bob", sip_password="abcdefgh12345"),
            ]
        )
        s.flush()
        s.add(Phone(mac="001b4faabbcc", label="Front desk", model="9608", extension_id=1))
    day = 24 * 3600
    cdr_file.write_text(
        cdr_line(0)  # 101 -> 102 answered, 30 s
        + cdr_line(1, disposition="NO ANSWER", billsec="0")  # 101 -> 102 missed
        + cdr_line(2, src="+15551234567", clid='"ACME" <+15551234567>',
                   channel="PJSIP/trunk-voipms-00000010", dstchannel="PJSIP/102-00000011",
                   ps_dialed="102")  # inbound answered by 102
        + cdr_line(3, src="+15551234567", clid='"ACME" <+15551234567>',
                   channel="PJSIP/trunk-voipms-00000012", dstchannel="PJSIP/101-00000013",
                   disposition="NO ANSWER", billsec="0", ps_dialed="101")  # inbound missed
        + cdr_line(4, dst="15559876543", dstchannel="PJSIP/trunk-voipms-00000014",
                   ps_dialed="15559876543", ps_route="trunk")  # outbound from 101
        + cdr_line(5, start=T0 - day, src="=cmd|calc", clid='"=HYPERLINK()" <5550000>',
                   channel="PJSIP/trunk-voipms-00000015", dstchannel="PJSIP/101-00000016",
                   ps_dialed="101")  # yesterday, hostile caller ID
    )  # fmt: skip
    return admin


def test_search_and_lookup(call_log: TestClient):
    page = call_log.get("/api/calls").json()
    assert page["total"] == 6
    assert page["lookup"] is None
    assert page["items"][0]["dst_number"] == "15559876543"  # newest first

    r = call_log.get("/api/calls", params={"q": "(555) 123-4567"}).json()
    assert r["total"] == 2
    assert r["lookup"]["answered"] == 1 and r["lookup"]["missed"] == 1
    assert r["lookup"]["first_at"].startswith("2026-09-29T15:02")

    assert call_log.get("/api/calls", params={"q": "acme"}).json()["total"] == 2
    assert call_log.get("/api/calls", params={"q": "100%"}).json()["total"] == 0


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"direction": "inbound"}, 3),
        ({"direction": "outbound"}, 1),
        ({"direction": "internal"}, 2),
        ({"status": "missed"}, 2),
        ({"extension": "102"}, 3),
        ({"start": "2026-09-29T00:00:00Z", "end": "2026-09-30T00:00:00Z"}, 5),
    ],
)
def test_filters(call_log: TestClient, params, expected):
    assert call_log.get("/api/calls", params=params).json()["total"] == expected


def test_pagination(call_log: TestClient):
    first = call_log.get("/api/calls", params={"page_size": 4}).json()
    second = call_log.get("/api/calls", params={"page_size": 4, "page": 2}).json()
    assert len(first["items"]) == 4 and len(second["items"]) == 2
    assert {c["id"] for c in first["items"]}.isdisjoint(c["id"] for c in second["items"])


def test_stats(call_log: TestClient):
    r = call_log.get(
        "/api/calls/stats",
        params={
            "start": "2026-09-28T04:00:00Z",  # midnight in New York
            "end": "2026-09-30T04:00:00Z",
            "tz": "America/New_York",
        },
    ).json()
    t = r["totals"]
    assert (t["calls"], t["inbound"], t["outbound"], t["internal"]) == (6, 3, 1, 2)
    assert (t["answered"], t["missed"], t["inbound_missed"]) == (4, 2, 1)
    assert t["talk_seconds"] == 4 * 30
    assert r["daily"] == [
        {"date": "2026-09-28", "inbound": 1, "outbound": 0, "internal": 0},
        {"date": "2026-09-29", "inbound": 2, "outbound": 1, "internal": 2},
    ]
    ext = {e["extension"]: e for e in r["per_extension"]}
    assert ext["101"]["phone"] == "Front desk"
    assert ext["101"]["outgoing"] == 3 and ext["101"]["outgoing_external"] == 1
    assert ext["101"]["incoming"] == 2 and ext["101"]["missed"] == 1
    assert ext["102"]["incoming"] == 3 and ext["102"]["incoming_external"] == 1
    assert ext["102"]["missed"] == 1


def test_stats_validation(call_log: TestClient):
    base = {"start": "2026-09-28T00:00:00Z", "end": "2026-09-29T00:00:00Z"}
    assert call_log.get("/api/calls/stats", params={**base, "tz": "Mars/Base"}).status_code == 422
    swapped = {"start": base["end"], "end": base["start"]}
    assert call_log.get("/api/calls/stats", params=swapped).status_code == 422


def test_export_csv(call_log: TestClient):
    r = call_log.get("/api/calls/export.csv", params={"direction": "inbound"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert len(rows) == 3
    # Caller IDs that look like spreadsheet formulas are defused.
    hostile = next(row for row in rows if "HYPERLINK" in row["from_name"])
    assert hostile["from_name"].startswith("'=")
    assert hostile["from_number"].startswith("'=")


def test_calls_api_requires_login(client: TestClient):
    assert client.get("/api/calls").status_code == 401
    assert client.get("/api/calls/export.csv").status_code == 401


def test_stats_day_buckets_follow_the_time_zone(db: Database, cdr_file):
    # 02:30 UTC on the 30th is still the 29th in New York.
    cdr_file.write_text(cdr_line(0, start=datetime(2026, 9, 30, 2, 30, tzinfo=UTC).timestamp()))
    calls.CdrImporter(db, cdr_file).import_new()
    with db.session() as s:
        r = calls.stats(
            s,
            datetime(2026, 9, 29, 4, tzinfo=UTC),
            datetime(2026, 9, 30, 4, tzinfo=UTC),
            ZoneInfo("America/New_York"),
        )
    assert r["daily"] == [{"date": "2026-09-29", "inbound": 0, "outbound": 0, "internal": 1}]
