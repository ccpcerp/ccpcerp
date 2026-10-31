"""No-network tests for restricted native B2 private ciphertext prefetch."""
import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest
from unittest.mock import patch

import ci_b2_native as b


class FakeResponse(io.BytesIO):
    def __init__(self, payload: bytes, status=200, content_length=None):
        super().__init__(payload)
        self.status = status
        self.headers = {} if content_length is None else {"Content-Length": str(content_length)}

    def getcode(self):
        return self.status


def synthetic_auth(**overrides):
    now = datetime.now(timezone.utc)
    body = {
        "apiInfo": {"storageApi": {
            "s3ApiUrl": b.S3_ENDPOINT,
            "downloadUrl": "https://f004.backblazeb2.com",
            "allowed": {
                "buckets": [{"id": b.BUCKET_ID, "name": b.BUCKET}],
                "capabilities": ["readFiles", "listFiles"],
                "namePrefix": b.PREFIX,
            },
        }},
        "applicationKeyExpirationTimestamp": int(
            (now + timedelta(days=2)).timestamp() * 1000
        ),
        "authorizationToken": "synthetic-token",
    }
    for key, value in overrides.items():
        if key.startswith("allowed_"):
            body["apiInfo"]["storageApi"]["allowed"][key[8:]] = value
        elif key.startswith("storage_"):
            body["apiInfo"]["storageApi"][key[8:]] = value
        else:
            body[key] = value
    return body


class BackblazeNativeTests(unittest.TestCase):
    def test_private_key_and_exact_object_only_two_no_redirect_requests(self):
        fake_payload = b"synthetic-age-ciphertext"
        with patch.object(b, "_open", side_effect=[
            FakeResponse(json.dumps(synthetic_auth()).encode()),
            FakeResponse(fake_payload, content_length=len(fake_payload)),
        ]) as opener, patch.object(b.guard, "verify_ciphertext") as verify, \
             patch.object(b.guard, "CIPHERTEXT_BYTES", len(fake_payload)):
            result = b.fetch_exact_ciphertext("keyid1234567890", "appkey1234567890")
        self.assertEqual(result, fake_payload)
        verify.assert_called_once_with(fake_payload)
        self.assertEqual(opener.call_count, 2)
        auth_req = opener.call_args_list[0].args[0]
        file_req = opener.call_args_list[1].args[0]
        self.assertEqual(auth_req.full_url, b.AUTH_URL)
        self.assertTrue(auth_req.get_header("Authorization").startswith("Basic "))
        self.assertEqual(file_req.full_url, (
            "https://f004.backblazeb2.com/file/" + b.BUCKET + "/" + b.OBJECT
        ))
        self.assertEqual(file_req.get_header("Authorization"), "synthetic-token")
        self.assertNotIn("appkey1234567890", file_req.full_url)

    def test_backblaze_base64_punctuation_accepted_without_exposing_keys(self):
        # Backblaze publishes read-only sample app keys containing + and /.
        key_id = "synthetic1234567890"
        for secret in (
            "Synthetic+Secret/WithChars=0123456789",
            "Synthetic_Secret-WithChars0123456789",
        ):
            with self.subTest(secret_suffix=secret[-4:]):
                self.assertTrue(b._credentials_ok(key_id, secret))
        for bad_id, bad_secret in (
            ("id:forbidden123", "Synthetic+Secret/WithChars=0123456789"),
            ("synthetic1234567890", "Secret has spaces"),
            ("synthetic1234567890", "Secret\\nWithNewline"),
            ("synthetic1234567890", "Secret:WithColon"),
            ("synthetic1234567890", "SecretÜUnicode"),
            ("synthetic1234567890", "x"),
        ):
            with self.subTest(invalid=True):
                self.assertFalse(b._credentials_ok(bad_id, bad_secret))

    def test_missing_or_malformed_keys_denied_before_network(self):
        with patch.object(b, "_open") as opener:
            for a, c in (("", "b" * 18), ("a" * 18, ""), ("a b" * 6, "b" * 18),
                         ("a" * 18, "secret\nsecond"), ("x" * 18, "y:secret")):
                with self.subTest(a=a), self.assertRaises(RuntimeError):
                    b.fetch_exact_ciphertext(a, c)
            opener.assert_not_called()

    def test_fail_closed_on_broad_or_wrong_key_and_redirect_origins(self):
        bad = [
            {"allowed_namePrefix": ""},
            {"allowed_namePrefix": "other/"},
            {"allowed_buckets": []},
            {"allowed_buckets": [{"name": "unrelated", "id": b.BUCKET_ID}]},
            {"allowed_buckets": [{"name": b.BUCKET, "id": "wrong-bucket"}]},
            {"allowed_buckets": [{"name": b.BUCKET, "id": "one"},
                                  {"name": "unrelated", "id": "two"}]},
            {"allowed_capabilities": ["readFiles", "writeFiles"]},
            {"allowed_capabilities": ["listFiles"]},
            {"storage_s3ApiUrl": "https://s3.other.invalid"},
            {"storage_downloadUrl": "http://f004.backblazeb2.com"},
            {"storage_downloadUrl": "https://f004.backblazeb2.com.evil.example"},
            {"storage_downloadUrl": "https://f004.backblazeb2.com/redirect"},
            {"storage_downloadUrl": "https://attacker@f004.backblazeb2.com"},
            {"applicationKeyExpirationTimestamp": None},
            {"applicationKeyExpirationTimestamp": int(
                (datetime.now(timezone.utc) + timedelta(days=30)).timestamp() * 1000)},
            {"authorizationToken": "token\r\nevil"},
        ]
        for changes in bad:
            with self.subTest(changes=changes):
                with self.assertRaises(RuntimeError):
                    b._restricted_authorization(synthetic_auth(**changes))

    def test_restricted_key_can_hide_bucket_name_but_not_bucket_id(self):
        auth = synthetic_auth(allowed_buckets=[{"id": b.BUCKET_ID, "name": None}])
        self.assertEqual(
            b._restricted_authorization(auth),
            ("https://f004.backblazeb2.com", "synthetic-token"),
        )

    def test_http_401_or_oversized_response_never_reaches_download(self):
        for first in (
            FakeResponse(b"unauthorized", status=401),
            FakeResponse(b"x" * (b.MAX_AUTH_BYTES + 1)),
        ):
            with patch.object(b, "_open", return_value=first) as opener, \
                 self.assertRaisesRegex(RuntimeError, "redacted"):
                b.fetch_exact_ciphertext("keyid1234567890", "appkey1234567890")
            self.assertEqual(opener.call_count, 1)

    def test_checksum_and_size_gate_prevents_using_wrong_ciphertext(self):
        auth = FakeResponse(json.dumps(synthetic_auth()).encode())
        with patch.object(b, "_open", side_effect=[
            auth, FakeResponse(b"wrong", content_length=12345)
        ]), self.assertRaisesRegex(RuntimeError, "redacted"):
            b.fetch_exact_ciphertext("keyid1234567890", "appkey1234567890")
        with patch.object(b, "_open", side_effect=[
            FakeResponse(json.dumps(synthetic_auth()).encode()), FakeResponse(b"wrong")
        ]), self.assertRaisesRegex(RuntimeError, "redacted"):
            b.fetch_exact_ciphertext("keyid1234567890", "appkey1234567890")

    def test_source_does_not_use_bearer_urls_or_persist_secrets(self):
        source = Path(b.__file__).read_text(encoding="utf8")
        for bad in ("generate_presigned_url", "X-Amz-", "store_artifacts",
                    "print(", "urlopen(", "logging.", "shell=True"):
            self.assertNotIn(bad, source)


if __name__ == "__main__":
    unittest.main()
