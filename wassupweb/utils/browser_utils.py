from __future__ import annotations

import platform


_PLATFORM_MAP = {
    "aix": "AIX",
    "darwin": "Mac OS",
    "windows": "Windows",
    "android": "Android",
    "freebsd": "FreeBSD",
    "openbsd": "OpenBSD",
    "sunos": "Solaris",
}


class Browsers:
    @staticmethod
    def ubuntu(browser: str) -> tuple[str, str, str]:
        return ("Ubuntu", browser, "22.04.4")

    @staticmethod
    def macos(browser: str) -> tuple[str, str, str]:
        return ("Mac OS", browser, "14.4.1")

    @staticmethod
    def baileys(browser: str) -> tuple[str, str, str]:
        return ("Baileys", browser, "6.5.0")

    @staticmethod
    def windows(browser: str) -> tuple[str, str, str]:
        return ("Windows", browser, "10.0.22631")

    @staticmethod
    def appropriate(browser: str) -> tuple[str, str, str]:
        os_name = platform.system().lower()
        mapped = _PLATFORM_MAP.get(os_name, "Ubuntu")
        return (mapped, browser, platform.release())


_PLATFORM_TYPE_MAP = {
    "CHROME": "1",
    "FIREFOX": "2",
    "SAFARI": "3",
    "EDGE": "4",
}


def get_platform_id(browser: str) -> str:
    return _PLATFORM_TYPE_MAP.get(browser.upper(), "1")
