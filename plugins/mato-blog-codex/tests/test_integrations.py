from __future__ import annotations

import sys
import unittest

from _support import IsolatedMatoEnvironment, SCRIPTS_DIR

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import integrations


class IntegrationSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.environment = IsolatedMatoEnvironment()
        self.env = self.environment.__enter__()

    def tearDown(self) -> None:
        self.environment.__exit__(None, None, None)

    def test_wordpress_settings_keep_password_out_of_plain_settings(self) -> None:
        integrations.configure_wordpress(
            site_url="example.test",
            username="writer",
            app_password="abcd efgh ijkl mnop",
            categories="Travel, 숙소",
            tags="여행,호텔",
            default_status="draft",
        )

        saved = integrations.settings_path().read_text(encoding="utf-8")
        protected = integrations.secrets_path().read_text(encoding="utf-8")
        self.assertNotIn("abcd", saved)
        self.assertNotIn("abcd", protected)
        self.assertEqual(integrations.get_secret(integrations.WORDPRESS_SECRET_KEY), "abcdefghijklmnop")
        status = integrations.integration_status()["wordpress"]
        self.assertTrue(status["configured"])
        self.assertTrue(status["has_app_password"])

    def test_blogspot_status_never_prints_secret_or_token(self) -> None:
        integrations.configure_blogspot(
            client_id="desktop-client.apps.googleusercontent.com",
            client_secret="very-secret-value",
            blog_id="12345",
            labels="travel,guide",
            acknowledge_public_drive_images=True,
        )
        integrations.set_secret(integrations.BLOGSPOT_TOKEN_SECRET_KEY, '{"refresh_token":"token-value"}')

        status = integrations.integration_status()["blogspot"]
        self.assertTrue(status["configured"])
        self.assertTrue(status["authorized"])
        self.assertNotIn("very-secret", str(status))
        self.assertNotIn("token-value", str(status))


if __name__ == "__main__":
    unittest.main()
