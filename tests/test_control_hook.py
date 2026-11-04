"""Public GitHub comment hook denies unauthorized and paid commands offline."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ci_broker.control_hook import authorize, execute
from ci_broker.model import BrokerError


def event(command="/ci-broker capabilities", login="LuigiD5555", issue=2,
          repo="LuigiD5555/ci-broker", assoc="OWNER"):
    return {"action": "created", "repository": {"full_name": repo},
            "issue": {"number": issue},
            "comment": {"body": command,
                        "user": {"login": login},
                        "author_association": assoc}}


class HookTests(unittest.TestCase):
    def test_capabilities_are_safe(self):
        cmd = authorize(event(), "LuigiD5555/ci-broker", 2, "LuigiD5555")
        result = execute(cmd)
        self.assertEqual(result["state"], "CAPABILITIES")
        self.assertEqual(result["billable_remote_dispatch"], "DISABLED_ON_PUBLIC_HOOK")
        self.assertNotIn("TOKEN", str(result))

    def test_reject_untrusted_author(self):
        with self.assertRaises(BrokerError):
            authorize(event(login="outsider"), "LuigiD5555/ci-broker", 2, "LuigiD5555")

    def test_reject_paid_dispatch_on_public_hook(self):
        with self.assertRaises(BrokerError):
            authorize(event(command="/ci-broker dispatch z01 --approve"),
                      "LuigiD5555/ci-broker", 2, "LuigiD5555")

    def test_reject_other_issues_and_repos(self):
        for payload in (event(issue=3), event(repo="somebody/other"),
                        event(assoc="NONE")):
            with self.assertRaises(BrokerError):
                authorize(payload, "LuigiD5555/ci-broker", 2, "LuigiD5555")

    def test_selftest_only_returns_summary(self):
        runner = lambda: SimpleNamespace(returncode=0,
                                         stderr="password=hidden\nRan 19 tests in 1.4s\nOK\n")
        result = execute("/ci-broker selftest", test_runner=runner)
        self.assertEqual(result["state"], "OFFLINE_TEST_PASS")
        self.assertEqual(result["tests"], 19)
        self.assertNotIn("password", str(result))


if __name__ == "__main__":
    unittest.main()
