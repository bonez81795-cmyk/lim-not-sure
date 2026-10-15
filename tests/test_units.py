"""Small pieces: phone numbers, the Twilio signature check, start-up safety checks, helpers."""
import unittest

from werkzeug.datastructures import MultiDict

from jobping import flow
from jobping.config import ConfigError, load_config
from jobping.ics import _escape, _fold
from jobping.security import Limiter, safe_next
from jobping.texting import fmt_phone, normalize_phone, twilio_signature_ok


class PhoneTests(unittest.TestCase):
    def test_normalize_phone(self):
        good = {
            "(586) 555-0100": "+15865550100",
            "586-555-0100": "+15865550100",
            "586.555.0100": "+15865550100",
            "1 586 555 0100": "+15865550100",
            "+1 (586) 555-0100": "+15865550100",
            "+442079460958": "+442079460958",
        }
        for raw, want in good.items():
            self.assertEqual(normalize_phone(raw), want, raw)
        for raw in (None, "", "anonymous", "555-0100", "12345", "+1 (186) 555-0100", "0865550100", "+1586555010", "58655501000"):
            self.assertIsNone(normalize_phone(raw), raw)

    def test_fmt_phone(self):
        self.assertEqual(fmt_phone("+15865550100"), "(586) 555-0100")
        self.assertEqual(fmt_phone("+442079460958"), "+442079460958")
        self.assertEqual(fmt_phone(None), "")


class SignatureTests(unittest.TestCase):
    def test_known_good_signature(self):
        # Twilio's documented method: the full URL (query string exactly as sent) plus every form field as
        # name+value in alphabetical order, HMAC-SHA1 with the auth token, base64. The expected value was
        # computed separately from the app's code and matches the long-standing example for token 12345.
        form = MultiDict(
            {"CallSid": "CA1234567890ABCDE", "Caller": "+12349013030", "Digits": "1234", "From": "+12349013030", "To": "+18005551212"}
        )
        url = "https://mycompany.com/myapp.php?foo=1&bar=2"
        self.assertTrue(twilio_signature_ok("12345", url, form, "0/KCTR6DLpKmkAf8muzZqo1nDgQ="))
        self.assertFalse(twilio_signature_ok("12345", url, form, "0/KCTR6DLpKmkAf8muzZqo1nDgQa="))
        self.assertFalse(twilio_signature_ok("54321", url, form, "0/KCTR6DLpKmkAf8muzZqo1nDgQ="))
        self.assertFalse(twilio_signature_ok("12345", url + "&x=1", form, "0/KCTR6DLpKmkAf8muzZqo1nDgQ="))
        self.assertFalse(twilio_signature_ok("12345", url, form, ""))
        self.assertFalse(twilio_signature_ok("", url, form, "0/KCTR6DLpKmkAf8muzZqo1nDgQ="))


class ConfigTests(unittest.TestCase):
    LIVE = {
        "SMS_PROVIDER": "twilio", "SECRET_KEY": "k" * 40, "TWILIO_ACCOUNT_SID": "ACx", "TWILIO_AUTH_TOKEN": "tok",
        "BASE_URL": "https://app.example.test",
    }

    def test_local_defaults_are_safe(self):
        cfg = load_config(env={})
        self.assertEqual(cfg["SMS_PROVIDER"], "console")
        self.assertTrue(cfg["SIMULATOR"])
        self.assertEqual(len(cfg["SECRET_KEY"]), 64)
        self.assertFalse(cfg["COOKIE_SECURE"])
        self.assertNotEqual(cfg["SECRET_KEY"], load_config(env={})["SECRET_KEY"])  # a new random key each start

    def test_live_texting_needs_everything_set_up_properly(self):
        self.assertFalse(load_config(env=self.LIVE)["SIMULATOR"])
        self.assertTrue(load_config(env=self.LIVE)["COOKIE_SECURE"])
        for key, bad, message in [
            ("SECRET_KEY", "short", "SECRET_KEY"),
            ("SECRET_KEY", "", "SECRET_KEY"),
            ("TWILIO_AUTH_TOKEN", "", "TWILIO_ACCOUNT_SID"),
            ("TWILIO_ACCOUNT_SID", "", "TWILIO_ACCOUNT_SID"),
            ("BASE_URL", "http://app.example.test", "https"),
            ("SIMULATOR", "1", "SIMULATOR"),
        ]:
            with self.assertRaises(ConfigError, msg=key) as ctx:
                load_config(env={**self.LIVE, key: bad})
            self.assertIn(message, str(ctx.exception))

    def test_unknown_provider_is_refused(self):
        with self.assertRaises(ConfigError):
            load_config(env={"SMS_PROVIDER": "carrier-pigeon"})

    def test_numbers_fall_back_to_defaults_when_garbled(self):
        cfg = load_config(env={"MAX_TEXTS_PER_NUMBER_PER_DAY": "lots", "MAX_FIRST_TEXTS_PER_BUSINESS_PER_DAY": "7"})
        self.assertEqual(cfg["MAX_TEXTS_PER_NUMBER_PER_DAY"], 6)
        self.assertEqual(cfg["MAX_FIRST_TEXTS_PER_BUSINESS_PER_DAY"], 7)


class HelperTests(unittest.TestCase):
    def test_safe_next_only_allows_paths_on_this_site(self):
        self.assertEqual(safe_next("/setup"), "/setup")
        self.assertEqual(safe_next("/tickets/3?show=all"), "/tickets/3?show=all")
        for bad in (None, "", "https://evil.example", "//evil.example", "/\\evil.example", "javascript:alert(1)", "evil", "http:evil"):
            self.assertEqual(safe_next(bad), "/dashboard", bad)

    def test_limiter_allows_the_limit_then_blocks_per_key(self):
        limiter = Limiter(3, 60)
        self.assertEqual([limiter.allow("a") for _ in range(5)], [True, True, True, False, False])
        self.assertTrue(limiter.allow("b"))
        limiter.reset("a")
        self.assertTrue(limiter.allow("a"))

    def test_calendar_escaping_and_folding(self):
        self.assertEqual(_escape("a,b;c\\d\ne"), r"a\,b\;c\\d\ne")
        for line in _fold("X" * 200 + "é" * 50):
            self.assertLessEqual(len(line.encode()), 75)
        folded = _fold("é" * 80)
        self.assertEqual("".join(p[1:] if i else p for i, p in enumerate(folded)), "é" * 80)

    def test_emergency_plain_wording(self):
        for text in ("water is coming through the ceiling", "my water heater is leaking", "the pipe broke under the sink",
                     "frozen pipe", "ceiling is dripping", "toilet is overflowing", "sump pump failed", "raw sewage in the yard"):
            self.assertTrue(flow.EMERGENCY.search(text), text)

    def test_emergency_and_price_words(self):
        for text in ("my basement is flooding", "I smell gas", "sewer backing up", "pipe burst", "no water at all", "carbon monoxide alarm"):
            self.assertTrue(flow.EMERGENCY.search(text), text)
        # Leaning toward false alarms is deliberate: a needless alert costs the owner a glance.
        for text in ("kitchen faucet drips", "toilet runs", "need a new water heater", "garbage disposal hums"):
            self.assertFalse(flow.EMERGENCY.search(text), text)
        for text in ("how much?", "what's the price", "do you charge a fee", "free estimate?", "about $100"):
            self.assertTrue(flow.PRICING.search(text), text)
        self.assertFalse(flow.PRICING.search("my faucet is leaking"))


if __name__ == "__main__":
    unittest.main()


class CostAndAbuseHelperTests(unittest.TestCase):
    def test_normalize_us_only_accepts_us_and_canadian_numbers(self):
        from jobping.texting import normalize_us

        self.assertEqual(normalize_us("(586) 555-0100"), "+15865550100")
        for raw in ("+442079460958", "+1 900 555 0100", "", None, "555"):
            self.assertIsNone(normalize_us(raw), raw)

    def test_alert_text_is_trimmed_to_plain_characters(self):
        from jobping.flow import short

        self.assertEqual(short("It’s “leaking” — badly…", 100), 'It\'s "leaking" - badly...')
        self.assertEqual(short("pipe burst \U0001F4A7\U0001F631 help", 100), "pipe burst help")
        self.assertEqual(short("x" * 50, 20), "x" * 17 + "...")
        self.assertLessEqual(len(short("word " * 100, 40)), 40)
        self.assertEqual(short(None, 10), "")

    def test_open_signup_is_on_for_the_simulator_and_off_for_live_texting(self):
        self.assertTrue(load_config(env={})["ALLOW_SIGNUP"])
        live = {
            "SMS_PROVIDER": "twilio", "SECRET_KEY": "k" * 40, "TWILIO_ACCOUNT_SID": "ACx", "TWILIO_AUTH_TOKEN": "tok",
            "BASE_URL": "https://app.example.test",
        }
        self.assertFalse(load_config(env=live)["ALLOW_SIGNUP"])
        self.assertTrue(load_config(env={**live, "ALLOW_SIGNUP": "1"})["ALLOW_SIGNUP"])
