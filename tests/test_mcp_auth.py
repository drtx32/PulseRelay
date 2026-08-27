import os
import tempfile
import unittest
from pathlib import Path

from core.mcp_auth import MCPAuthStore, normalize_scopes


class MCPAuthStoreTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db = Path(self.tempdir.name) / "auth.db"
        self.store = MCPAuthStore(self.db)
        self.previous_admin = os.environ.pop("PULSERELAY_ADMIN_TOKEN", None)

    def tearDown(self):
        if self.previous_admin is not None:
            os.environ["PULSERELAY_ADMIN_TOKEN"] = self.previous_admin
        else:
            os.environ.pop("PULSERELAY_ADMIN_TOKEN", None)
        self.tempdir.cleanup()

    def test_gbrain_style_scope_aliases_expand(self):
        self.assertEqual(
            set(normalize_scopes("read")),
            {"connectors:read", "routes:read", "deliveries:read", "events:read"},
        )
        write = set(normalize_scopes("write"))
        self.assertIn("connectors:write", write)
        self.assertIn("routes:write", write)
        self.assertIn("deliveries:replay", write)

    def test_api_key_without_scope_is_full_control_for_existing_ui(self):
        created = self.store.create_api_key("web-ui-key")
        principal = self.store.authenticate_bearer(created["api_key"])
        self.assertIsNotNone(principal)
        self.assertTrue(principal.allows("connectors:write"))
        self.assertTrue(principal.allows("routes:write"))
        self.assertEqual(principal.auth_type, "api_key")

    def test_api_key_can_be_scoped_and_revoked_by_name(self):
        created = self.store.create_api_key("read-only", "read")
        principal = self.store.authenticate_bearer(created["api_key"])
        self.assertTrue(principal.allows("routes:read"))
        self.assertFalse(principal.allows("routes:write"))
        self.assertTrue(self.store.revoke_api_key(name="read-only"))
        self.assertIsNone(self.store.authenticate_bearer(created["api_key"]))

    def test_oauth_client_credentials_can_request_subset_of_grant(self):
        client = self.store.create_oauth_client("agent", "write")
        token = self.store.issue_client_credentials_token(
            client["client_id"], client["client_secret"], "read"
        )
        self.assertIsNotNone(token)
        principal = self.store.authenticate_bearer(token["access_token"])
        self.assertEqual(principal.auth_type, "oauth")
        self.assertTrue(principal.allows("connectors:read"))
        self.assertFalse(principal.allows("connectors:write"))

    def test_oauth_client_cannot_escalate_scope(self):
        client = self.store.create_oauth_client("reader", "read")
        with self.assertRaises(ValueError):
            self.store.issue_client_credentials_token(
                client["client_id"], client["client_secret"], "write"
            )

    def test_revoking_oauth_client_invalidates_existing_tokens(self):
        client = self.store.create_oauth_client("agent", "read")
        token = self.store.issue_client_credentials_token(client["client_id"], client["client_secret"])
        self.assertIsNotNone(self.store.authenticate_bearer(token["access_token"]))
        self.assertTrue(self.store.revoke_oauth_client(client["client_id"]))
        self.assertIsNone(self.store.authenticate_bearer(token["access_token"]))

    def test_admin_bootstrap_remains_super_scope(self):
        os.environ["PULSERELAY_ADMIN_TOKEN"] = "bootstrap-secret"
        principal = self.store.authenticate_bearer("bootstrap-secret")
        self.assertEqual(principal.auth_type, "admin_token")
        self.assertTrue(principal.allows("connectors:write"))
        self.assertTrue(principal.allows("deliveries:replay"))


if __name__ == "__main__":
    unittest.main()
