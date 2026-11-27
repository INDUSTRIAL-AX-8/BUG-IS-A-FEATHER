"""외부 네트워크 호출 점검 (폐쇄망 배포 전 체크).

    python scripts/check_no_external.py

점검 항목
  1. 백엔드 소스: 로컬/사설망이 아닌 http(s) 주소, 허용 위치 밖의 HTTP 클라이언트 사용
  2. 프론트 소스(src, index.html): 외부 http(s) 주소 (CDN, 웹폰트, 외부 API, 텔레메트리)
  3. 빌드 산출물(frontend/dist): index.html 의 외부 script/link, CSS 의 외부 @import/url(), 외부 fetch/XHR
     (라이브러리 안의 문서 링크·XML 네임스페이스 문자열은 호출이 아니므로 정보로만 표시)
  4. 설정 예시: LLM 주소가 로컬인지

위반이 있으면 종료 코드 1.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
URL = re.compile(r"https?://[^\s'\"`)<>\]]+")
LOCAL = re.compile(r"^https?://(localhost|127\.0\.0\.1|\[::1\]|0\.0\.0\.0|192\.168\.|10\.|172\.(1[6-9]|2\d|3[01])\.)")
# 호출이 아닌 식별자/문서 문자열
BENIGN = re.compile(r"^https?://(www\.w3\.org/|json-schema\.org/|purl\.org/|ns\.adobe\.com/|schemas\.)")
HTTP_CLIENT_ALLOWED = {ROOT / "backend/app/llm/ollama_client.py"}
HTTP_CLIENT = re.compile(r"^\s*(import|from)\s+(requests|urllib3|aiohttp|httpx|urllib\.request)\b", re.M)

violations: list[str] = []
infos: list[str] = []


def scan_source(paths, kinds: tuple[str, ...]) -> None:
    for base in paths:
        for p in base.rglob("*"):
            if not p.is_file() or p.suffix not in kinds or "node_modules" in p.parts or ".venv" in p.parts:
                continue
            if "tests" in p.parts:
                continue
            text = p.read_text(encoding="utf-8", errors="ignore")
            for m in URL.finditer(text):
                u = m.group(0)
                if LOCAL.match(u) or BENIGN.match(u):
                    continue
                line = text.count("\n", 0, m.start()) + 1
                violations.append(f"{p.relative_to(ROOT)}:{line}: 외부 주소 {u}")
            if p.suffix == ".py" and p not in HTTP_CLIENT_ALLOWED and HTTP_CLIENT.search(text):
                violations.append(f"{p.relative_to(ROOT)}: HTTP 클라이언트 사용 (허용 위치: app/llm/ollama_client.py)")


def scan_dist() -> None:
    dist = ROOT / "frontend" / "dist"
    if not dist.is_dir():
        infos.append("frontend/dist 없음 — 빌드 후 다시 점검하세요 (npm run build).")
        return
    index = (dist / "index.html").read_text(encoding="utf-8")
    for m in re.finditer(r"<(script|link)[^>]+(src|href)=\"(https?://[^\"]+)\"", index):
        violations.append(f"frontend/dist/index.html: 외부 {m.group(1)} {m.group(3)}")
    for css in dist.rglob("*.css"):
        text = css.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r"(@import\s+url\(|url\()\s*['\"]?(https?://[^'\")]+)", text):
            violations.append(f"{css.relative_to(ROOT)}: CSS 외부 리소스 {m.group(2)}")
    for js in dist.rglob("*.js"):
        text = js.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r"(fetch|XMLHttpRequest\(\)\.open|navigator\.sendBeacon|new WebSocket)\s*\(\s*['\"`](https?://|wss?://)[^'\"`]+", text):
            violations.append(f"{js.relative_to(ROOT)}: 외부 호출 {m.group(0)[:120]}")
        urls = {u for u in URL.findall(text) if not LOCAL.match(u) and not BENIGN.match(u)}
        if urls:
            infos.append(f"{js.relative_to(ROOT)}: 라이브러리 내부 문자열 URL {len(urls)}개 (호출 아님, 예: {sorted(urls)[0][:60]})")
    fonts = list(dist.rglob("*.woff2"))
    infos.append(f"번들 폰트 {len(fonts)}개 포함" if fonts else "번들 폰트 없음")


def scan_config() -> None:
    for p in (ROOT / "config").glob("*.toml"):
        text = p.read_text(encoding="utf-8")
        m = re.search(r"ollama_url\s*=\s*\"([^\"]+)\"", text)
        if m and not LOCAL.match(m.group(1)):
            violations.append(f"{p.relative_to(ROOT)}: LLM 주소가 로컬/사설망이 아님 {m.group(1)}")


def scan_installed() -> None:
    """텔레메트리 전송 패키지가 설치돼 있지 않은지. (FastAPI 가 의존하는 opentelemetry-api 는 전송 기능 없는 API 뿐)"""
    import importlib.util

    for mod in ("opentelemetry.sdk", "opentelemetry.exporter", "sentry_sdk", "posthog", "segment"):
        try:
            found = importlib.util.find_spec(mod) is not None
        except ModuleNotFoundError:
            found = False
        if found:
            violations.append(f"텔레메트리/전송 패키지 설치됨: {mod}")
    infos.append("텔레메트리 SDK·익스포터 미설치 (opentelemetry-api 는 FastAPI 의존성, 전송 기능 없음)")


def main() -> int:
    scan_source([ROOT / "backend" / "app"], (".py",))
    scan_source([ROOT / "frontend" / "src"], (".ts", ".tsx", ".css", ".html"))
    scan_source([ROOT / "frontend"], (".html",)) if (ROOT / "frontend" / "index.html").exists() else None
    scan_dist()
    scan_config()
    scan_installed()
    for i in infos:
        print(f"[정보] {i}")
    if violations:
        print(f"\n[위반] {len(violations)}건")
        for v in violations:
            print(f"  - {v}")
        return 1
    print("\n[통과] 외부 네트워크 호출이 발견되지 않았습니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
