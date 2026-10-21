"""Schlanker Client für die Telegram Bot API (nur Standardbibliothek)."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)


class TelegramError(Exception):
    def __init__(self, message: str, code: Optional[int] = None, retry_after: Optional[int] = None):
        super().__init__(message)
        self.code = code
        self.retry_after = retry_after


class TelegramAPI:
    def __init__(self, token: str, base_url: str = "https://api.telegram.org"):
        if not token:
            raise ValueError("kein Bot-Token gesetzt")
        self._url = "%s/bot%s/" % (base_url.rstrip("/"), token)

    def call(self, method: str, http_timeout: float = 30.0, **params: Any) -> Any:
        body = json.dumps({k: v for k, v in params.items() if v is not None}).encode()
        request = urllib.request.Request(
            self._url + method, data=body, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(request, timeout=http_timeout) as response:
                payload = json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode())
            except Exception:
                raise TelegramError("HTTP %s bei %s" % (exc.code, method), exc.code)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            # Token nie ins Log: nur Methode und Fehlerart
            raise TelegramError("%s: %s" % (method, type(exc).__name__))
        if not payload.get("ok"):
            retry = (payload.get("parameters") or {}).get("retry_after")
            raise TelegramError(
                "%s: %s" % (method, payload.get("description", "Fehler")),
                payload.get("error_code"), retry,
            )
        return payload.get("result")

    def get_updates(self, offset: Optional[int], timeout: int) -> List[Dict[str, Any]]:
        return self.call("getUpdates", http_timeout=timeout + 15, offset=offset,
                         timeout=timeout, allowed_updates=["message", "callback_query"])

    def send_message(self, chat_id: int, text: str, keyboard=None) -> Dict[str, Any]:
        return self.call("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML",
                         disable_web_page_preview=True,
                         reply_markup=_markup(keyboard))

    def edit_message(self, chat_id: int, message_id: int, text: str, keyboard=None) -> None:
        try:
            self.call("editMessageText", chat_id=chat_id, message_id=message_id, text=text,
                      parse_mode="HTML", disable_web_page_preview=True,
                      reply_markup=_markup(keyboard))
        except TelegramError as exc:
            if "message is not modified" not in str(exc):
                raise

    def answer_callback(self, callback_id: str, text: Optional[str] = None) -> None:
        self.call("answerCallbackQuery", callback_query_id=callback_id, text=text)

    def delete_message(self, chat_id: int, message_id: int) -> None:
        self.call("deleteMessage", chat_id=chat_id, message_id=message_id)

    def set_commands(self, commands: List[Dict[str, str]]) -> None:
        self.call("setMyCommands", commands=commands)


def _markup(keyboard) -> Optional[Dict[str, Any]]:
    """keyboard: Liste von Zeilen mit (Text, callback_data)."""
    if not keyboard:
        return None
    return {"inline_keyboard": [
        [{"text": text, "callback_data": data} for text, data in row] for row in keyboard
    ]}
