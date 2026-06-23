from __future__ import annotations

import keyring


SERVICE_NAME = "wegoodcharger-cli"


class AuthStore:
    def get_token(self, email: str) -> str | None:
        return keyring.get_password(SERVICE_NAME, email)

    def set_token(self, email: str, token: str) -> None:
        keyring.set_password(SERVICE_NAME, email, token)

    def clear_token(self, email: str) -> None:
        try:
            keyring.delete_password(SERVICE_NAME, email)
        except keyring.errors.PasswordDeleteError:
            pass
