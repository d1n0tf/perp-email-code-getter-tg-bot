import unittest
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from src.config import Settings
from src.email_manager import EmailCodeFetcher
from src.storage import EmailAccount


def _mail(*, code: str, when: datetime, message_id: str | None) -> bytes:
    message = EmailMessage()
    message["From"] = "team@mail.perplexity.ai"
    message["To"] = "member@example.com"
    message["Date"] = when.strftime("%a, %d %b %Y %H:%M:%S +0000")
    if message_id:
        message["Message-ID"] = message_id
    message.set_content(f"Your Perplexity login code is {code}.")
    return message.as_bytes()


class FakeImap:
    def __init__(self, messages_by_folder: dict[str, dict[str, bytes]]) -> None:
        self.messages_by_folder = messages_by_folder
        self.selected_folder: str | None = None
        self.searches: list[tuple[str, ...]] = []
        self.logged_out = False

    def select(self, folder: str):
        if folder not in self.messages_by_folder:
            return "NO", [b"No such mailbox"]
        self.selected_folder = folder
        return "OK", [b""]

    def uid(self, command: str, *args: str):
        if command.lower() == "search":
            self.searches.append(args)
            assert self.selected_folder is not None
            return "OK", [
                " ".join(self.messages_by_folder[self.selected_folder]).encode("ascii")
            ]
        if command.lower() == "fetch":
            assert self.selected_folder is not None
            raw = self.messages_by_folder[self.selected_folder].get(args[0])
            if raw is None:
                return "NO", [b"not found"]
            return "OK", [(b"RFC822", raw)]
        raise AssertionError(f"Unexpected IMAP command: {command}")

    def logout(self):
        self.logged_out = True
        return "BYE", [b"logged out"]


class EmailCodeFetcherRecentScanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fetcher = EmailCodeFetcher(
            Settings(
                mail_folders=["INBOX", "Junk Email", "Missing"],
                mail_global_rate_limit_per_second=100_000,
                mail_global_backoff_base_seconds=0.001,
                mail_global_backoff_max_seconds=0.001,
            )
        )
        self.account = EmailAccount(
            login_email="member@example.com",
            login_password="unused",
            recovery_email="recovery@example.com",
            recovery_password="unused",
            refresh_token="unused",
            client_id="unused",
            raw="unused",
        )

    def test_scan_recent_codes_collects_all_folders_deduplicates_and_orders(self) -> None:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        duplicated = _mail(
            code="111111",
            when=now - timedelta(minutes=3),
            message_id="<same@example.com>",
        )
        imap = FakeImap(
            {
                "INBOX": {
                    "4": _mail(
                        code="222222",
                        when=now - timedelta(minutes=1),
                        message_id="<latest@example.com>",
                    ),
                    "3": duplicated,
                    "2": _mail(
                        code="999999",
                        when=now - timedelta(hours=25),
                        message_id="<too-old@example.com>",
                    ),
                    "1": self._mail_from(
                        sender="other@example.com",
                        code="333333",
                        when=now - timedelta(minutes=2),
                        message_id="<other@example.com>",
                    ),
                },
                "Junk Email": {"9": duplicated},
            }
        )
        self.fetcher._connect = lambda *_: imap  # type: ignore[method-assign]

        records = self.fetcher.scan_recent_codes(self.account, limit=20)

        self.assertEqual([record.code for record in records], ["222222", "111111"])
        self.assertEqual(
            [record.message_identity for record in records],
            ["message-id:<latest@example.com>", "message-id:<same@example.com>"],
        )
        self.assertEqual(records[0].timestamp.tzinfo, timezone.utc)
        self.assertEqual(records[0].folder, "INBOX")
        self.assertTrue(imap.logged_out)
        self.assertEqual(len(imap.searches), 2)
        self.assertEqual(
            sum("FROM \"team@mail.perplexity.ai\"" in item[0] for item in imap.searches),
            2,
        )
        self.assertTrue(all("SINCE" in item[0] for item in imap.searches))

    @staticmethod
    def _mail_from(
        *,
        sender: str,
        code: str,
        when: datetime,
        message_id: str,
    ) -> bytes:
        message = EmailMessage()
        message["From"] = sender
        message["Date"] = when.strftime("%a, %d %b %Y %H:%M:%S +0000")
        message["Message-ID"] = message_id
        message.set_content(f"Your login code is {code}.")
        return message.as_bytes()

    def test_scan_recent_codes_uses_payload_hash_when_message_id_is_missing(self) -> None:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        raw = _mail(code="123456", when=now, message_id=None)
        imap = FakeImap({"INBOX": {"1": raw}})
        self.fetcher.folders = ["INBOX"]
        self.fetcher._connect = lambda *_: imap  # type: ignore[method-assign]

        records = self.fetcher.scan_recent_codes(self.account)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].code, "123456")
        self.assertTrue(records[0].message_identity.startswith("sha256:"))

    def test_scan_recent_codes_finds_code_in_html_when_plain_part_has_no_code(self) -> None:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        message = EmailMessage()
        message["From"] = "team@mail.perplexity.ai"
        message["Date"] = now.strftime("%a, %d %b %Y %H:%M:%S +0000")
        message["Message-ID"] = "<html@example.com>"
        message.set_content("Open Perplexity to continue.")
        message.add_alternative("<strong>654321</strong>", subtype="html")
        imap = FakeImap({"INBOX": {"1": message.as_bytes()}})
        self.fetcher.folders = ["INBOX"]
        self.fetcher._connect = lambda *_: imap  # type: ignore[method-assign]

        records = self.fetcher.scan_recent_codes(self.account)

        self.assertEqual([record.code for record in records], ["654321"])

    def test_scan_recent_codes_finds_code_split_across_html_tags(self) -> None:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        message = EmailMessage()
        message["From"] = "team@mail.perplexity.ai"
        message["Date"] = now.strftime("%a, %d %b %Y %H:%M:%S +0000")
        message["Message-ID"] = "<split-html@example.com>"
        message.set_content("Open Perplexity to continue.")
        message.add_alternative(
            "<span>1</span><span>2</span><span>3</span>"
            "<span>4</span><span>5</span><span>6</span>",
            subtype="html",
        )
        imap = FakeImap({"INBOX": {"1": message.as_bytes()}})
        self.fetcher.folders = ["INBOX"]
        self.fetcher._connect = lambda *_: imap  # type: ignore[method-assign]

        records = self.fetcher.scan_recent_codes(self.account)

        self.assertEqual([record.code for record in records], ["123456"])

    def test_scan_recent_codes_returns_empty_for_non_positive_limit_without_connecting(self) -> None:
        self.fetcher._connect = lambda *_: self.fail("IMAP must not connect")  # type: ignore[method-assign]

        self.assertEqual(self.fetcher.scan_recent_codes(self.account, limit=0), [])

    def test_scan_recent_codes_treats_connection_failure_as_an_empty_history_update(self) -> None:
        def failed_connect(*_):
            raise RuntimeError("temporary Outlook failure")

        self.fetcher._connect = failed_connect  # type: ignore[method-assign]

        self.assertEqual(self.fetcher.scan_recent_codes(self.account), [])

    def test_scan_recent_codes_falls_back_when_sender_search_is_empty(self) -> None:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        raw = _mail(
            code="246810",
            when=now - timedelta(minutes=1),
            message_id="<alternate-sender@example.com>",
        )
        imap = FakeImap({"INBOX": {"1": raw}})
        self.fetcher.folders = ["INBOX"]
        self.fetcher._connect = lambda *_: imap  # type: ignore[method-assign]

        original_uid = imap.uid

        def search_with_empty_from(command: str, *args: str):
            if command.lower() == "search" and "FROM" in args[0]:
                assert imap.selected_folder is not None
                imap.searches.append(args)
                return "OK", [b""]
            return original_uid(command, *args)

        imap.uid = search_with_empty_from  # type: ignore[method-assign]
        records = self.fetcher.scan_recent_codes(self.account)

        self.assertEqual([record.code for record in records], ["246810"])
        self.assertEqual(len(imap.searches), 3)
        self.assertIn("SINCE", imap.searches[-1][0])
        self.assertRegex(imap.searches[-1][0], r"SINCE \d{2}-[A-Z][a-z]{2}-\d{4}")

    def test_scan_recent_codes_retries_plain_sender_search_for_outlook_compatibility(self) -> None:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        raw = _mail(
            code="975310",
            when=now - timedelta(minutes=1),
            message_id="<plain-from-fallback@example.com>",
        )
        imap = FakeImap({"INBOX": {"1": raw}})
        self.fetcher.folders = ["INBOX"]
        self.fetcher._connect = lambda *_: imap  # type: ignore[method-assign]
        original_uid = imap.uid

        def compound_search_is_empty(command: str, *args: str):
            if command.lower() == "search" and "SINCE" in args[0]:
                imap.searches.append(args)
                return "OK", [b""]
            return original_uid(command, *args)

        imap.uid = compound_search_is_empty  # type: ignore[method-assign]

        records = self.fetcher.scan_recent_codes(self.account)

        self.assertEqual([record.code for record in records], ["975310"])
        self.assertEqual(len(imap.searches), 2)
        self.assertIn('FROM "team@mail.perplexity.ai"', imap.searches[-1][0])
        self.assertNotIn("SINCE", imap.searches[-1][0])

    def test_scan_recent_codes_accepts_current_perplexity_sender_alias(self) -> None:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        message = EmailMessage()
        message["From"] = "Perplexity <no-reply@perplexity.ai>"
        message["Date"] = now.strftime("%a, %d %b %Y %H:%M:%S +0000")
        message["Message-ID"] = "<alias@example.com>"
        message.set_content("Your login code is 135790.")
        imap = FakeImap({"INBOX": {"1": message.as_bytes()}})
        self.fetcher.folders = ["INBOX"]
        self.fetcher._connect = lambda *_: imap  # type: ignore[method-assign]

        records = self.fetcher.scan_recent_codes(self.account)

        self.assertEqual([record.code for record in records], ["135790"])

    def test_fetch_accepts_metadata_after_a_leading_response_item(self) -> None:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        raw = _mail(
            code="112233",
            when=now - timedelta(minutes=1),
            message_id="<leading-item@example.com>",
        )
        imap = FakeImap({"INBOX": {"1": raw}})
        self.fetcher.folders = ["INBOX"]
        self.fetcher._connect = lambda *_: imap  # type: ignore[method-assign]
        original_uid = imap.uid

        def fetch_with_leading_item(command: str, *args: str):
            result = original_uid(command, *args)
            if command.lower() == "fetch" and result[0] == "OK":
                return "OK", [b"unexpected marker", *result[1]]
            return result

        imap.uid = fetch_with_leading_item  # type: ignore[method-assign]
        records = self.fetcher.scan_recent_codes(self.account)

        self.assertEqual([record.code for record in records], ["112233"])


if __name__ == "__main__":
    unittest.main()
